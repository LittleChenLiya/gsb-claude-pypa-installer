"""Acceptance tests: script newline strategies, end-to-end through ``install()``.

Builds two minimal wheels -- one carrying a CRLF script, one carrying an LF
script, both in ``.data/scripts`` with a ``#!python`` shebang -- and checks,
for each newline strategy:

- the rewritten shebang (first line),
- the remaining bytes of the installed script,
- the hash and size recorded for the script in the installed ``RECORD``,
- that non-script files are installed byte-for-byte unchanged.
"""

import base64
import hashlib
import zipfile
from pathlib import Path

import pytest

from installer import install
from installer.destinations import SchemeDictionaryDestination
from installer.records import parse_record_file
from installer.sources import WheelFile
from installer.utils import SCHEME_NAMES

INTERPRETER = "/my/python"

CRLF_SCRIPT = b"#!python\r\nimport sys\r\nprint('crlf', sys.argv)\r\n"
LF_SCRIPT = b"#!python\nimport sys\nprint('lf', sys.argv)\n"
INIT_PY = b"def main():\n    print('hello')\n"


def _record_hash(data):
    digest = hashlib.sha256(data).digest()
    return "sha256=" + base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _make_wheel(tmp_path, name, script_bytes):
    dist_info = f"{name}-1.0.0.dist-info"
    files = {
        f"{name}/__init__.py": INIT_PY,
        f"{name}-1.0.0.data/scripts/{name}-tool": script_bytes,
        f"{dist_info}/WHEEL": (
            b"Wheel-Version: 1.0\n"
            b"Generator: test-script-newlines (1.0.0)\n"
            b"Root-Is-Purelib: true\n"
            b"Tag: py3-none-any\n"
        ),
        f"{dist_info}/METADATA": (
            f"Metadata-Version: 2.1\nName: {name}\nVersion: 1.0.0\n".encode()
        ),
    }
    rows = [
        f"{path},{_record_hash(content)},{len(content)}"
        for path, content in sorted(files.items())
    ]
    rows.append(f"{dist_info}/RECORD,,")
    files[f"{dist_info}/RECORD"] = ("\n".join(rows) + "\n").encode()

    wheel_path = tmp_path / f"{name}-1.0.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel_path, "w") as archive:
        for filename, content in files.items():
            archive.writestr(filename, content)
    return wheel_path


def _install(wheel_path, dest_dir, script_newline):
    scheme_dict = {}
    for scheme in SCHEME_NAMES:
        folder = dest_dir / scheme
        folder.mkdir(parents=True)
        scheme_dict[scheme] = str(folder)
    destination = SchemeDictionaryDestination(
        scheme_dict,
        INTERPRETER,
        "posix",
        script_newline=script_newline,
    )
    with WheelFile.open(wheel_path) as source:
        source.validate_record()
        install(source, destination, {})
    return scheme_dict


def _installed_record(scheme_dict, dist_info_dir):
    record_path = Path(scheme_dict["purelib"]) / dist_info_dir / "RECORD"
    with record_path.open(encoding="utf-8") as f:
        return {
            path: (hash_, size)
            for path, hash_, size in parse_record_file(f.read().splitlines())
        }


@pytest.fixture()
def crlf_wheel(tmp_path):
    return _make_wheel(tmp_path, "crlfdemo", CRLF_SCRIPT)


@pytest.fixture()
def lf_wheel(tmp_path):
    return _make_wheel(tmp_path, "lfdemo", LF_SCRIPT)


@pytest.mark.parametrize(
    ("script_newline", "expected"),
    [
        pytest.param(
            "preserve",
            b"#!/my/python\nimport sys\r\nprint('crlf', sys.argv)\r\n",
            id="preserve",
        ),
        pytest.param(
            "crlf",
            b"#!/my/python\r\nimport sys\r\nprint('crlf', sys.argv)\r\n",
            id="crlf",
        ),
        pytest.param(
            "lf",
            b"#!/my/python\nimport sys\nprint('crlf', sys.argv)\n",
            id="lf",
        ),
    ],
)
def test_install_crlf_script_wheel(crlf_wheel, tmp_path, script_newline, expected):
    scheme_dict = _install(crlf_wheel, tmp_path / "dest", script_newline)

    installed = (tmp_path / "dest" / "scripts" / "crlfdemo-tool").read_bytes()

    # First line: the shebang is rewritten with the requested newline.
    expected_first_line, expected_rest = expected.split(b"\n", 1)
    first_line, rest = installed.split(b"\n", 1)
    assert first_line.rstrip(b"\r") == b"#!" + INTERPRETER.encode()
    assert first_line == expected_first_line.rstrip(b"\n")
    # Remaining bytes follow the requested newline strategy.
    assert rest == expected_rest
    assert installed == expected

    # RECORD hash and size are computed from the final script.
    records = _installed_record(scheme_dict, "crlfdemo-1.0.0.dist-info")
    (script_path,) = [p for p in records if p.endswith("crlfdemo-tool")]
    hash_, size = records[script_path]
    assert hash_ == _record_hash(installed)
    assert size == str(len(installed))

    # Non-script files are installed unchanged.
    assert (
        tmp_path / "dest" / "purelib" / "crlfdemo" / "__init__.py"
    ).read_bytes() == INIT_PY


@pytest.mark.parametrize(
    ("script_newline", "expected"),
    [
        pytest.param(
            "preserve",
            b"#!/my/python\nimport sys\nprint('lf', sys.argv)\n",
            id="preserve",
        ),
        pytest.param(
            "lf",
            b"#!/my/python\nimport sys\nprint('lf', sys.argv)\n",
            id="lf",
        ),
        pytest.param(
            "crlf",
            b"#!/my/python\r\nimport sys\r\nprint('lf', sys.argv)\r\n",
            id="crlf",
        ),
    ],
)
def test_install_lf_script_wheel(lf_wheel, tmp_path, script_newline, expected):
    scheme_dict = _install(lf_wheel, tmp_path / "dest", script_newline)

    installed = (tmp_path / "dest" / "scripts" / "lfdemo-tool").read_bytes()

    first_line, rest = installed.split(b"\n", 1)
    assert first_line.rstrip(b"\r") == b"#!" + INTERPRETER.encode()
    assert rest == expected.split(b"\n", 1)[1]
    assert installed == expected

    records = _installed_record(scheme_dict, "lfdemo-1.0.0.dist-info")
    (script_path,) = [p for p in records if p.endswith("lfdemo-tool")]
    hash_, size = records[script_path]
    assert hash_ == _record_hash(installed)
    assert size == str(len(installed))

    assert (
        tmp_path / "dest" / "purelib" / "lfdemo" / "__init__.py"
    ).read_bytes() == INIT_PY


def test_default_destination_keeps_original_behaviour(crlf_wheel, tmp_path):
    # Regression check for the original interface: a destination constructed
    # without an explicit newline strategy behaves exactly as before.
    scheme_dict = {}
    for scheme in SCHEME_NAMES:
        folder = tmp_path / "dest" / scheme
        folder.mkdir(parents=True)
        scheme_dict[scheme] = str(folder)
    destination = SchemeDictionaryDestination(scheme_dict, INTERPRETER, "posix")
    with WheelFile.open(crlf_wheel) as source:
        install(source, destination, {})

    installed = (tmp_path / "dest" / "scripts" / "crlfdemo-tool").read_bytes()
    assert installed == b"#!/my/python\nimport sys\r\nprint('crlf', sys.argv)\r\n"
