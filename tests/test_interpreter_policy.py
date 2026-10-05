"""Acceptance tests for the explicit script interpreter strategy.

These cover, with small deterministic fixtures:

- ``#!python`` / ``#!pythonw`` placeholders use the *given* interpreter
  (``PATH`` is never consulted);
- interpreter paths containing spaces get a platform-supported wrapper and
  the original script body survives untouched;
- a real shebang such as ``#!/bin/sh`` is never replaced;
- a failed install only removes files written during that attempt and
  restores overwritten pre-existing files;
- the regenerated ``RECORD`` size summary matches the final on-disk files.
"""

import base64
import hashlib
import textwrap
import zipfile
from io import BytesIO
from pathlib import Path

import pytest

from installer import install
from installer.destinations import SchemeDictionaryDestination
from installer.records import parse_record_file
from installer.scripts import (
    build_shebang,
    get_gui_executable,
    parse_placeholder_shebang,
)
from installer.sources import WheelFile
from installer.utils import SCHEME_NAMES, fix_shebang

DATA = Path(__file__).parent / "data" / "scripts"
POSIX_INTERP_WITH_SPACE = "D:/Program Files/My Python/python"
WIN_INTERP_WITH_SPACE = r"D:\Program Files\My Python\python.exe"


def _rewrite(data, interpreter, kind="posix"):
    with BytesIO(data) as source, fix_shebang(source, interpreter, kind) as out:
        return out.read()


# ---------------------------------------------------------------------------
# Placeholder recognition
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("line", "expected"),
    [
        (b"#!python\n", "console"),
        (b"#!python\r\n", "console"),
        (b"#!python", "console"),
        (b"#!pythonw\n", "gui"),
        (b"#!/bin/sh\n", None),
        (b"#!python3\n", None),
        (b"#!pythonic\n", None),
        (b"#!py\n", None),
        (b"", None),
    ],
)
def test_parse_placeholder_shebang(line, expected):
    assert parse_placeholder_shebang(line) == expected


# ---------------------------------------------------------------------------
# fix_shebang: explicit interpreter, no PATH guessing
# ---------------------------------------------------------------------------
def test_python_placeholder_uses_given_interpreter():
    result = _rewrite(b"#!python\nprint(1)\n", "/explicit/python")
    assert result == b"#!/explicit/python\nprint(1)\n"


def test_pythonw_placeholder_posix_keeps_console_interpreter():
    # On POSIX there is no pythonw concept, so the given interpreter is used.
    result = _rewrite(b"#!pythonw\nprint(1)\n", "/explicit/python")
    assert result == b"#!/explicit/python\nprint(1)\n"


@pytest.mark.parametrize("kind", ["win-ia32", "win-amd64", "win-arm", "win-arm64"])
def test_pythonw_placeholder_windows_uses_pythonw(kind):
    result = _rewrite(b"#!pythonw\nprint(1)\n", r"C:\Py Env\python.exe", kind)
    assert result == b'#!"C:\\Py Env\\pythonw.exe"\nprint(1)\n'


def test_real_shebang_is_kept():
    data = b"#!/bin/sh\necho hello\n"
    assert _rewrite(data, "/explicit/python") == data


def test_python3_shebang_is_not_a_placeholder():
    data = b"#!python3\nprint(1)\n"
    assert _rewrite(data, "/explicit/python") == data


def test_body_without_trailing_newline_is_preserved():
    result = _rewrite(b"#!python\nprint(1)", "/explicit/python")
    assert result == b"#!/explicit/python\nprint(1)"


def test_path_with_space_posix_wraps_and_preserves_body():
    result = _rewrite(b"#!python\nimport sys\nsys.exit(0)\n", POSIX_INTERP_WITH_SPACE)
    assert result.startswith(b"#!/bin/sh\n")
    assert b"'D:/Program Files/My Python/python'" in result
    assert result.endswith(b"import sys\nsys.exit(0)\n")


def test_path_with_space_windows_is_quoted():
    result = _rewrite(b"#!python\nprint(1)\n", WIN_INTERP_WITH_SPACE, "win-amd64")
    assert result == b'#!"D:\\Program Files\\My Python\\python.exe"\nprint(1)\n'


# ---------------------------------------------------------------------------
# Entry-point launcher generation
# ---------------------------------------------------------------------------
def test_build_shebang_posix_space():
    shebang = build_shebang(POSIX_INTERP_WITH_SPACE, "posix")
    assert shebang.startswith(b"#!/bin/sh\n")
    assert b"'D:/Program Files/My Python/python'" in shebang


@pytest.mark.parametrize("kind", ["win-ia32", "win-amd64", "win-arm", "win-arm64"])
def test_build_shebang_windows_gui(kind):
    shebang = build_shebang(r"C:\Py Env\python.exe", kind, gui=True, forlauncher=True)
    # The embedded launcher shebang is verbatim (the launcher parses it).
    assert shebang == b"#!C:\\Py Env\\pythonw.exe"


def test_get_gui_executable_derives_sibling_pythonw():
    assert get_gui_executable(r"D:\Program Files\Py\python.exe") == (
        r"D:\Program Files\Py\pythonw.exe"
    )


# ---------------------------------------------------------------------------
# End-to-end installation using the example scripts
# ---------------------------------------------------------------------------
def _make_scheme_dict(base):
    scheme_dict = {}
    for scheme in SCHEME_NAMES:
        p = base / scheme
        p.mkdir(parents=True, exist_ok=True)
        scheme_dict[scheme] = str(p)
    return scheme_dict


def _make_wheel(path):
    files = {
        "exdemo/__init__.py": "",
        "exdemo-1.0.dist-info/WHEEL": textwrap.dedent("""\
            Wheel-Version: 1.0
            Generator: acceptance
            Root-Is-Purelib: true
            Tag: py3-none-any
        """),
        "exdemo-1.0.dist-info/METADATA": textwrap.dedent("""\
            Metadata-Version: 2.1
            Name: exdemo
            Version: 1.0
        """),
        "exdemo-1.0.dist-info/RECORD": "",
        "exdemo-1.0.data/scripts/example-console": (
            DATA / "example-console"
        ).read_bytes(),
        "exdemo-1.0.data/scripts/example-gui": (DATA / "example-gui").read_bytes(),
        "exdemo-1.0.data/scripts/example-shell": (DATA / "example-shell").read_bytes(),
    }
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)


@pytest.fixture
def example_wheel(tmp_path):
    path = tmp_path / "exdemo-1.0-py3-none-any.whl"
    _make_wheel(path)
    return path


def _assert_record_matches_files(purelib, scripts):
    record_path = purelib / "exdemo-1.0.dist-info" / "RECORD"
    rows = list(parse_record_file(record_path.read_text().splitlines()))
    assert rows, "RECORD should not be empty"

    by_name = {"scripts/" + p.name: p for p in scripts.iterdir()}

    checked = 0
    for relpath, hash_value, size in rows:
        if relpath == "exdemo-1.0.dist-info/RECORD":
            assert hash_value == "" and size == ""
            continue
        target = by_name.get(relpath, purelib / relpath)
        data = target.read_bytes()
        assert size == str(len(data)), (relpath, size, len(data))
        if hash_value:
            algo, digest = hash_value.split("=", 1)
            actual_digest = (
                base64.urlsafe_b64encode(hashlib.new(algo, data).digest())
                .decode()
                .rstrip("=")
            )
            assert digest == actual_digest, relpath
            checked += 1
    assert checked >= 3


def test_install_posix_spaced_interpreter_and_record(example_wheel, tmp_path):
    scheme_dict = _make_scheme_dict(tmp_path / "root")
    destination = SchemeDictionaryDestination(
        scheme_dict, POSIX_INTERP_WITH_SPACE, "posix"
    )
    with WheelFile.open(example_wheel) as source:
        install(source, destination, {})

    scripts = Path(scheme_dict["scripts"])

    console = (scripts / "example-console").read_bytes()
    assert console.startswith(b"#!/bin/sh\n")
    assert b"'D:/Program Files/My Python/python'" in console
    assert b'sys.stdout.write("console example\\n")' in console

    gui = (scripts / "example-gui").read_bytes()
    assert gui.startswith(b"#!/bin/sh\n")
    assert b"'D:/Program Files/My Python/python'" in gui
    assert b'print("gui example")' in gui

    shell = (scripts / "example-shell").read_bytes()
    assert shell == (DATA / "example-shell").read_bytes()
    assert shell.startswith(b"#!/bin/sh\necho")

    _assert_record_matches_files(Path(scheme_dict["purelib"]), scripts)


def test_install_windows_gui_script_kind(example_wheel, tmp_path):
    scheme_dict = _make_scheme_dict(tmp_path / "root")
    destination = SchemeDictionaryDestination(
        scheme_dict, WIN_INTERP_WITH_SPACE, "win-amd64"
    )
    with WheelFile.open(example_wheel) as source:
        install(source, destination, {})

    scripts = Path(scheme_dict["scripts"])
    assert (
        (scripts / "example-console")
        .read_bytes()
        .startswith(b'#!"D:\\Program Files\\My Python\\python.exe"')
    )
    assert (
        (scripts / "example-gui")
        .read_bytes()
        .startswith(b'#!"D:\\Program Files\\My Python\\pythonw.exe"')
    )
    # A non-placeholder shebang is untouched even on Windows.
    assert (scripts / "example-shell").read_bytes().startswith(b"#!/bin/sh\n")


# ---------------------------------------------------------------------------
# Rollback on failed installation
# ---------------------------------------------------------------------------
class FailingDestination(SchemeDictionaryDestination):
    """Writes some files then raises while installing the package."""

    def __init__(self, *args, fail_path, **kwargs):
        super().__init__(*args, **kwargs)
        self.fail_path = fail_path

    def write_file(self, scheme, path, stream, is_executable):
        if path == self.fail_path:
            raise RuntimeError("boom")
        return super().write_file(scheme, path, stream, is_executable)


def test_failed_install_cleans_up_and_preserves_old_files(example_wheel, tmp_path):
    scheme_dict = _make_scheme_dict(tmp_path / "root")
    scripts = Path(scheme_dict["scripts"])

    # A pre-existing script from an earlier installation must be preserved.
    old_file = scripts / "example-shell"
    old_file.write_bytes(b"#!/bin/sh\nold content must survive\n")

    destination = FailingDestination(
        scheme_dict,
        "/explicit/python",
        "posix",
        fail_path="exdemo/__init__.py",
    )
    with (
        WheelFile.open(example_wheel) as source,
        pytest.raises(RuntimeError, match="boom"),
    ):
        install(source, destination, {})

    # The old file is untouched and files from the failed attempt are gone.
    assert old_file.read_bytes() == b"#!/bin/sh\nold content must survive\n"
    assert not (scripts / "example-console").exists()
    assert not (scripts / "example-gui").exists()
    purelib = Path(scheme_dict["purelib"])
    assert not (purelib / "exdemo-1.0.dist-info" / "RECORD").exists()
