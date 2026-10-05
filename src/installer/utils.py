"""Utilities related to handling / interacting with wheel files."""

import base64
import contextlib
import csv
import hashlib
import io
import os
import re
import shlex
import sys
from collections import namedtuple
from collections.abc import Callable, Iterable, Iterator
from configparser import ConfigParser
from email.message import Message
from email.parser import FeedParser
from email.policy import compat32
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    BinaryIO,
    NewType,
    cast,
)

if TYPE_CHECKING:
    from installer.records import RecordEntry
    from installer.scripts import LauncherKind, ScriptSection

Scheme = NewType("Scheme", str)
AllSchemes = tuple[Scheme, ...]

__all__ = [
    "SCHEME_NAMES",
    "WheelFilename",
    "construct_record_file",
    "copyfileobj_with_hashing",
    "fix_shebang",
    "get_launcher_kind",
    "make_file_executable",
    "parse_entrypoints",
    "parse_metadata_file",
    "parse_wheel_filename",
]

# Borrowed from https://github.com/python/cpython/blob/v3.9.1/Lib/shutil.py#L52
_WINDOWS = os.name == "nt"
_COPY_BUFSIZE = 1024 * 1024 if _WINDOWS else 64 * 1024

# According to https://www.python.org/dev/peps/pep-0427/#file-name-convention
_WHEEL_FILENAME_REGEX = re.compile(
    r"""
    ^
    (?P<distribution>.+?)
    -(?P<version>.*?)
    (?:-(?P<build_tag>\d[^-]*?))?
    -(?P<tag>.+?-.+?-.+?)
    \.whl
    $
    """,
    re.VERBOSE | re.UNICODE,
)
WheelFilename = namedtuple(
    "WheelFilename", ["distribution", "version", "build_tag", "tag"]
)

# Adapted from https://github.com/python/importlib_metadata/blob/v3.4.0/importlib_metadata/__init__.py#L90
_ENTRYPOINT_REGEX = re.compile(
    r"""
    (?P<module>[\w.]+)\s*
    (:\s*(?P<attrs>[\w.]+))\s*
    (?P<extras>\[.*\])?\s*$
    """,
    re.VERBOSE | re.UNICODE,
)

# According to https://www.python.org/dev/peps/pep-0427/#id7
SCHEME_NAMES = cast("AllSchemes", ("purelib", "platlib", "headers", "scripts", "data"))


def parse_metadata_file(contents: str) -> Message:
    """Parse :pep:`376` ``PKG-INFO``-style metadata files.

    ``METADATA`` and ``WHEEL`` files (as per :pep:`427`) use the same syntax
    and can also be parsed using this function.

    :param contents: The entire contents of the file
    """
    feed_parser = FeedParser(policy=compat32)
    feed_parser.feed(contents)
    return feed_parser.close()


def canonicalize_name(name: str) -> str:
    """Canonicalize a project name according to PEP-503.

    :param name: The project name to canonicalize
    """
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_wheel_filename(filename: str) -> WheelFilename:
    """Parse a wheel filename, into it's various components.

    :param filename: The filename to parse
    """
    wheel_info = _WHEEL_FILENAME_REGEX.match(filename)
    if not wheel_info:
        raise ValueError(f"Not a valid wheel filename: {filename}")
    return WheelFilename(*wheel_info.groups())


def copyfileobj_with_hashing(
    source: BinaryIO,
    dest: BinaryIO,
    hash_algorithm: str,
) -> tuple[str, int]:
    """Copy a buffer while computing the content's hash and size.

    Copies the source buffer into the destination buffer while computing the
    hash of the contents. Adapted from :any:`shutil.copyfileobj`.

    :param source: buffer holding the source data
    :param dest: destination buffer
    :param hash_algorithm: hashing algorithm

    :return: hash digest of the contents, size of the contents
    """
    hasher = hashlib.new(hash_algorithm)
    size = 0
    while True:
        buf = source.read(_COPY_BUFSIZE)
        if not buf:
            break
        hasher.update(buf)
        dest.write(buf)
        size += len(buf)

    return base64.urlsafe_b64encode(hasher.digest()).decode("ascii").rstrip("="), size


def get_stream_length(source: BinaryIO) -> int:
    """Read a buffer while computing the content's size.

    :param source: buffer holding the source data
    :return: size of the contents
    """
    size = 0
    while True:
        buf = source.read(_COPY_BUFSIZE)
        if not buf:
            break
        size += len(buf)

    return size


def get_launcher_kind() -> "LauncherKind":  # pragma: no cover
    """Get the launcher kind for the current machine."""
    if os.name != "nt":
        return "posix"

    if "amd64" in sys.version.lower():
        return "win-amd64"
    if "(arm64)" in sys.version.lower():
        return "win-arm64"
    if "(arm)" in sys.version.lower():
        return "win-arm"
    if sys.platform == "win32":
        return "win-ia32"

    raise NotImplementedError("Unknown launcher kind for this machine")


def _is_executable_simple(executable: bytes) -> bool:
    """Check if the executable path can be used directly in a shebang line.

    A path is "simple" if it contains no spaces and the resulting shebang line
    (``#!`` + path + newline) is short enough for the platform's shebang limit.
    """
    if b" " in executable:
        return False
    shebang_length = len(executable) + 3  # Prefix #! and newline after.
    # According to distlib, Darwin can handle up to 512 characters. But I want
    # to avoid platform sniffing to make this as platform-agnostic as possible.
    # The "complex" script isn't that bad anyway.
    return shebang_length <= 127


def _build_shebang(executable: str, for_launcher: bool) -> bytes:
    """Build a shebang line for the given interpreter.

    The interpreter is used as-is; this function does not consult ``PATH`` or
    guess the user environment. When the path contains spaces, a
    platform-supported wrapper is generated:

    - On Windows (or for launcher executables), the path is enclosed in double
      quotes so the launcher can resolve it correctly.
    - On POSIX, a ``/bin/sh`` wrapper script is emitted that re-executes the
      interpreter with the original arguments.

    :param executable: Path to the interpreter executable.
    :param for_launcher: Whether this shebang is consumed by a launcher
        executable (Windows). When ``True`` the path is quoted rather than
        wrapped in ``/bin/sh``. This also determines the target platform:
        ``True`` means Windows, ``False`` means POSIX.
    :return: The shebang line as bytes.
    """
    executable_bytes = executable.encode("utf-8")
    if for_launcher:
        # On Windows (or for launchers), use a simple shebang. Enquote the
        # executable if it contains spaces, so the launcher can resolve it.
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


def _get_windowed_interpreter(executable: str) -> str:
    """Get the windowed (GUI) variant of the interpreter executable.

    On Windows this replaces ``python`` with ``pythonw`` in the file name.
    On other platforms the path is returned unchanged, since there is no
    separate windowed interpreter. The path separator style (forward or back
    slash) is preserved.
    """
    if os.name != "nt":
        return executable
    # Determine the separator used in the path, so we preserve the style
    # (e.g. a POSIX-style path on Windows keeps its forward slashes).
    if "\\" in executable:
        sep = "\\"
    elif "/" in executable:
        sep = "/"
    else:
        sep = None
    if sep is None:
        return executable.replace("python", "pythonw")
    dn, fn = executable.rsplit(sep, 1)
    fn = fn.replace("python", "pythonw")
    return dn + sep + fn


# The exact placeholder shebangs recognised by wheel installers.
_PYTHON_SHEBANG = b"#!python"
_PYTHONW_SHEBANG = b"#!pythonw"


@contextlib.contextmanager
def fix_shebang(stream: BinaryIO, interpreter: str) -> Iterator[BinaryIO]:
    """Replace ``#!python`` and ``#!pythonw`` shebang placeholders in a stream.

    Only the exact ``#!python`` and ``#!pythonw`` placeholders are replaced,
    using the interpreter supplied by the caller (never guessed from ``PATH``).
    All other shebangs (such as ``#!/bin/sh``) are preserved unchanged. The
    remainder of the stream is copied verbatim, so the original script tail
    content is not corrupted.

    :param stream: stream to modify
    :param interpreter: The interpreter to substitute for ``#!python``. The
        windowed variant (``pythonw``) is used for ``#!pythonw``.

    :returns: A context manager, that provides an appropriately modified stream.
    """
    stream.seek(0)
    first_line = stream.readline()
    # Strip the trailing newline(s) to compare against the placeholders.
    stripped = first_line.rstrip(b"\r\n")
    if stripped in (_PYTHON_SHEBANG, _PYTHONW_SHEBANG):
        if stripped == _PYTHONW_SHEBANG:
            target = _get_windowed_interpreter(interpreter)
        else:
            target = interpreter
        new_stream = io.BytesIO()
        # Write our new shebang, with a platform-supported wrapper if needed.
        # On Windows the shebang is just a comment (no launcher is prepended),
        # but enquoting a space-containing path is still safe and correct.
        new_stream.write(_build_shebang(target, for_launcher=os.name == "nt"))
        new_stream.write(b"\n")
        # Copy the rest of the stream verbatim.
        while True:
            buf = stream.read(_COPY_BUFSIZE)
            if not buf:
                break
            new_stream.write(buf)
        new_stream.seek(0)
        yield new_stream
        new_stream.close()
    else:
        stream.seek(0)
        yield stream


def construct_record_file(
    records: Iterable[tuple[Scheme, "RecordEntry"]],
    prefix_for_scheme: Callable[[Scheme], str | None] = lambda _: None,
) -> BinaryIO:
    """Construct a RECORD file.

    :param records:
        ``records`` as passed into :any:`WheelDestination.finalize_installation`
    :param prefix_for_scheme:
        function to get a prefix to add for RECORD entries, within a scheme

    :return: A stream that can be written to file. Must be closed by the caller.
    """
    stream = io.TextIOWrapper(
        io.BytesIO(), encoding="utf-8", write_through=True, newline=""
    )
    writer = csv.writer(stream, delimiter=",", quotechar='"', lineterminator="\n")
    for scheme, record in sorted(records, key=lambda x: x[1].path):
        writer.writerow(record.to_row(prefix_for_scheme(scheme)))
    stream.seek(0)
    return stream.detach()


def parse_entrypoints(text: str) -> Iterable[tuple[str, str, str, "ScriptSection"]]:
    """Parse ``entry_points.txt``-style files.

    :param text: entire contents of the file
    :return:
        name of the script, module to use, attribute to call, kind of script (cli / gui)
    """
    # Borrowed from https://github.com/python/importlib_metadata/blob/v3.4.0/importlib_metadata/__init__.py#L115
    config = ConfigParser(delimiters="=")
    config.optionxform = str  # type: ignore[assignment, method-assign]
    config.read_string(text)

    for section in config.sections():
        if section not in ["console_scripts", "gui_scripts"]:
            continue

        for name, value in config.items(section):
            assert isinstance(name, str)
            match = _ENTRYPOINT_REGEX.match(value)
            assert match

            module = match.group("module")
            assert isinstance(module, str)

            attrs = match.group("attrs")
            # TODO: make this a proper error, which can be caught.
            assert attrs is not None
            assert isinstance(attrs, str)

            script_section = cast("ScriptSection", section[: -len("_scripts")])

            yield name, module, attrs, script_section


def _current_umask() -> int:
    """Get the current umask which involves having to set it temporarily."""
    mask = os.umask(0)
    os.umask(mask)
    return mask


# Borrowed from:
# https://github.com/pypa/pip/blob/0f21fb92/src/pip/_internal/utils/unpacking.py#L93
def make_file_executable(path: Path) -> None:
    """Make the file at the provided path executable."""
    path.chmod(0o777 & ~_current_umask() | 0o111)
