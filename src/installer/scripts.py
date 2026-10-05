"""Generate executable scripts, on various platforms."""

import io
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from importlib.resources import files
from typing import TYPE_CHECKING

from installer import _scripts
from installer.utils import _build_shebang, _get_windowed_interpreter

if TYPE_CHECKING:
    from typing import Literal

    LauncherKind = Literal["posix", "win-ia32", "win-amd64", "win-arm", "win-arm64"]
    ScriptSection = Literal["console", "gui"]


__all__ = ["InvalidScript", "Script"]


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

    def _get_alternate_executable(self, executable: str, kind: "LauncherKind") -> str:
        """Get an alternate executable for the launcher.

        On Windows, when the script section is gui-script, pythonw.exe should be used.
        """
        if self.section == "gui" and kind != "posix":
            return _get_windowed_interpreter(executable)
        return executable

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
        executable = self._get_alternate_executable(executable, kind)
        shebang = self._build_shebang(executable, forlauncher=bool(launcher))
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

    @staticmethod
    def _build_shebang(executable: str, forlauncher: bool) -> bytes:
        """Build a shebang line.

        Delegates to :func:`installer.utils._build_shebang`, which handles
        spaces in the interpreter path with a platform-supported wrapper.
        """
        return _build_shebang(executable, for_launcher=forlauncher)
