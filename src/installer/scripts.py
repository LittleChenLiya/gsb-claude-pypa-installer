"""Generate executable scripts, on various platforms."""

import io
import os
import re
import shlex
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from importlib.resources import files
from typing import TYPE_CHECKING

from installer import _scripts

if TYPE_CHECKING:
    from typing import Literal

    LauncherKind = Literal["posix", "win-ia32", "win-amd64", "win-arm", "win-arm64"]
    ScriptSection = Literal["console", "gui"]


__all__ = [
    "InvalidScript",
    "Script",
    "build_shebang",
    "get_gui_executable",
    "parse_placeholder_shebang",
]


_ALLOWED_LAUNCHERS: Mapping[tuple["ScriptSection", "LauncherKind"], str] = {
    ("console", "win-ia32"): "t32.exe",
    ("console", "win-amd64"): "t64.exe",
    ("console", "win-arm"): "t_arm.exe",
    ("console", "win-arm64"): "t64-arm.exe",
    ("gui", "win-ia32"): "w32.exe",
    ("gui", "win-amd64"): "w64.exe",
    ("gui", "win-arm"): "w_arm.exe",
    ("gui", "win-arm64"): "w64-arm.exe",
}

_SCRIPT_TEMPLATE = """\
# -*- coding: utf-8 -*-
import re
import sys
from {module} import {import_name}
if __name__ == "__main__":
    sys.argv[0] = re.sub(r"(-script\\.pyw|\\.exe)?$", "", sys.argv[0])
    sys.exit({func_path}())
"""


# The placeholder shebangs recognised by the wheel installation procedure.
# Only these exact tokens are placeholders; a "real" shebang such as
# ``#!/bin/sh`` or ``#!python3`` is left untouched.
_PLACEHOLDER_SHEBANG_RE = re.compile(rb"^#!(pythonw?)(?:[ \t]|$)")


def parse_placeholder_shebang(line: bytes) -> "ScriptSection | None":
    """Recognise a ``#!python`` / ``#!pythonw`` placeholder shebang line.

    The wheel format uses these placeholders (see the wheel spec's notes on
    shebang lines) to ask the installer to substitute the interpreter it was
    explicitly told to use. No other shebang is ever modified, so a script
    that already names a real interpreter (e.g. ``#!/bin/sh``) is preserved.

    :param line: the first line of a script, including any line ending
    :return: ``"console"`` for ``#!python``, ``"gui"`` for ``#!pythonw``, or
        ``None`` if the line is not a placeholder.
    """
    match = _PLACEHOLDER_SHEBANG_RE.match(line.rstrip(b"\r\n"))
    if match is None:
        return None
    return "gui" if match.group(1) == b"pythonw" else "console"


def get_gui_executable(executable: str) -> str:
    """Get the windowed (GUI) interpreter corresponding to ``executable``.

    On Windows, ``python.exe`` and ``pythonw.exe`` live side by side, so the
    GUI launcher is derived directly from the explicitly-provided interpreter
    path. ``PATH`` is never consulted.
    """
    dn, fn = os.path.split(executable)
    fn = fn.replace("python", "pythonw")
    return os.path.join(dn, fn)  # noqa: PTH118


def _is_executable_simple(executable: bytes) -> bool:
    if b" " in executable:
        return False
    shebang_length = len(executable) + 3  # Prefix #! and newline after.
    # According to distlib, Darwin can handle up to 512 characters. But I want
    # to avoid platform sniffing to make this as platform-agnostic as possible.
    # The "complex" script isn't that bad anyway.
    return shebang_length <= 127


def build_shebang(
    executable: str,
    kind: "LauncherKind",
    *,
    gui: bool = False,
    forlauncher: bool = False,
) -> bytes:
    """Build a shebang line that invokes ``executable``.

    :param executable: Explicit path to the interpreter to invoke. It is used
        verbatim -- this function never searches ``PATH`` for an interpreter.
    :param kind: The platform the generated script is meant for (``"posix"``
        or one of the Windows ``"win-*"`` kinds).
    :param gui: Whether a windowed (``pythonw``) interpreter is required.
        Only meaningful for the Windows kinds.
    :param forlauncher: Whether the shebang will be embedded into a Windows
        launcher stub. Such launchers consume the command as-is.

    When the interpreter path contains spaces, a platform-supported wrapper
    is produced: a ``/bin/sh`` wrapper on POSIX, and a quoted command for the
    Windows launcher (whose shebang parser understands quotes).
    """
    if gui and kind != "posix":
        executable = get_gui_executable(executable)

    executable_bytes = executable.encode("utf-8")
    if forlauncher:  # The launcher can just use the command as-is.
        return b"#!" + executable_bytes
    if kind != "posix":
        # Wheel-installed scripts on Windows are read by the launcher-style
        # interpreter resolution, which accepts a quoted command when the
        # path contains spaces.
        if b" " in executable_bytes:
            executable_bytes = b'"' + executable_bytes + b'"'
        return b"#!" + executable_bytes
    if _is_executable_simple(executable_bytes):
        return b"#!" + executable_bytes

    # Shebang support for an executable with a space in it is under-specified
    # and platform-dependent, so we use a clever hack to generate a script to
    # run in ``/bin/sh`` that should work on all reasonably modern platforms.
    # Read the following message to understand how the hack works:
    # https://github.com/pypa/installer/pull/4#issuecomment-623668717

    quoted = shlex.quote(executable).encode("utf-8")
    # I don't understand a lick what this is trying to do.
    return b"#!/bin/sh\n'''exec' " + quoted + b' "$0" "$@"\n' + b"' '''"


class InvalidScript(ValueError):
    """Raised if the user provides incorrect script section or kind."""


@dataclass
class Script:
    """Describes a script based on an entry point declaration."""

    name: str
    """Name of the script."""

    module: str
    """Module path, to load the entry point from."""

    attr: str
    """Final attribute access, for the entry point."""

    section: "ScriptSection" = field(repr=False)
    """
    Denotes the "entry point section" where this was specified. Valid values
    are ``"gui"`` and ``"console"``.
    """

    def _get_launcher_data(self, kind: "LauncherKind") -> bytes | None:
        if kind == "posix":
            return None
        key = (self.section, kind)
        try:
            name = _ALLOWED_LAUNCHERS[key]
        except KeyError:
            error = f"{key!r} not in {sorted(_ALLOWED_LAUNCHERS)!r}"
            raise InvalidScript(error) from None
        return (files(_scripts) / name).read_bytes()

    def generate(self, executable: str, kind: "LauncherKind") -> tuple[str, bytes]:
        """Generate a launcher for this script.

        :param executable: Path to the executable to invoke.
        :param kind: Which launcher template should be used.
            Valid values are ``"posix"``, ``"win-ia32"``, ``"win-amd64"`` and
            ``"win-arm"``.
        :type kind: str

        :raises InvalidScript: if no appropriate template is available.
        :return: The name and contents of the launcher file.
        """
        launcher = self._get_launcher_data(kind)
        shebang = build_shebang(
            executable,
            kind,
            gui=self.section == "gui",
            forlauncher=bool(launcher),
        )
        code = _SCRIPT_TEMPLATE.format(
            module=self.module,
            import_name=self.attr.split(".")[0],
            func_path=self.attr,
        ).encode("utf-8")

        if launcher is None:
            return self.name, shebang + b"\n" + code

        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as zf:
            zf.writestr("__main__.py", code)
        name = f"{self.name}.exe"
        data = launcher + shebang + b"\n" + stream.getvalue()
        return name, data
