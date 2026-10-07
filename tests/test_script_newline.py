"""End-to-end checks for the script newline policy during wheel installation.

Two minimal wheels (one carrying a CRLF script, one an LF script, in
``{distribution}-{version}.data/scripts/``) are installed through the regular
``installer.install`` data flow. The tests verify the rewritten shebang line,
the preserved trailing bytes, and the final RECORD hash/size.
"""

import base64
import hashlib
import zipfile

import pytest

from installer import install
from installer.destinations import SchemeDictionaryDestination
from installer.records import parse_record_file
from installer.sources import WheelFile

_INTERPRETER = "/usr/bin/env python3"

# Binary bodies (including non-text bytes) after the shebang line, to prove
# that everything past the first line is preserved byte-for-byte.
LF_SCRIPT = b"#!python\nprint('lf')\n\x00\xff\x01binary\n"
CRLF_SCRIPT = b"#!python\r\nprint('crlf')\r\n\x00\xff\x01binary\r\n"


def _record_line(path, data):
    digest = hashlib.sha256(data).digest()
    hash_ = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return f"{path},sha256={hash_},{len(data)}"


def _make_wheel(tmp_path, name, script_bytes):
    """Build a minimal wheel carrying one script in .data/scripts/."""
    dist_info = f"{name}-1.0.0.dist-info"
    script_path = f"{name}-1.0.0.data/scripts/demo-script"
    files = {
        f"{dist_info}/METADATA": (
            f"Metadata-Version: 2.1\nName: {name}\nVersion: 1.0.0\n".encode()
        ),
        f"{dist_info}/WHEEL": (
            b"Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
        ),
        script_path: script_bytes,
    }
    record = "\n".join(_record_line(p, d) for p, d in files.items())
    record += f"\n{dist_info}/RECORD,,\n"

    wheel_path = tmp_path / f"{name}-1.0.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel_path, "w") as zf:
        for path, data in files.items():
            zf.writestr(path, data)
        zf.writestr(f"{dist_info}/RECORD", record)
    return wheel_path


def _install(wheel_path, target, **destination_kwargs):
    schemes = ("purelib", "platlib", "headers", "scripts", "data")
    destination = SchemeDictionaryDestination(
        scheme_dict={scheme: str(target / scheme) for scheme in schemes},
        interpreter=_INTERPRETER,
        script_kind="posix",
        **destination_kwargs,
    )
    with WheelFile.open(wheel_path) as source:
        install(source, destination, {})
    return target


def _read_record(target, name):
    record_path = target / "purelib" / f"{name}-1.0.0.dist-info" / "RECORD"
    with record_path.open("r", encoding="utf-8", newline="") as f:
        lines = f.read().splitlines()
    return {row[0]: row for row in parse_record_file(lines)}


@pytest.mark.parametrize(
    ("script_bytes", "shebang_ending"),
    [
        pytest.param(LF_SCRIPT, b"\n", id="lf-script"),
        pytest.param(CRLF_SCRIPT, b"\r\n", id="crlf-script"),
    ],
)
def test_install_preserves_script_newline(tmp_path, script_bytes, shebang_ending):
    name = "newline_demo"
    wheel = _make_wheel(tmp_path, name, script_bytes)
    target = tmp_path / "installed"

    _install(wheel, target, script_newline="preserve")

    # The shebang line is rewritten, keeping the original line ending...
    installed = target / "scripts" / "demo-script"
    data = installed.read_bytes()
    expected = b"#!" + _INTERPRETER.encode() + shebang_ending
    expected += script_bytes.split(b"\n", 1)[1]  # ...rest preserved byte-for-byte
    assert data == expected
    # First line check, explicitly.
    first_line = data.split(b"\n", 1)[0]
    assert first_line == b"#!" + _INTERPRETER.encode() + shebang_ending[:-1]

    # RECORD hash and size are computed over the final script bytes.
    records = _read_record(target, name)
    (script_row,) = [row for row in records.values() if row[0].endswith("demo-script")]
    digest = hashlib.sha256(data).digest()
    expected_hash = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    assert script_row[1] == f"sha256={expected_hash}"
    assert script_row[2] == str(len(data))

    # Non-script files are installed unmodified.
    with zipfile.ZipFile(wheel) as zf:
        for member in ("METADATA", "WHEEL"):
            original = zf.read(f"{name}-1.0.0.dist-info/{member}")
            installed_member = target / "purelib" / f"{name}-1.0.0.dist-info" / member
            assert installed_member.read_bytes() == original


@pytest.mark.parametrize(
    ("script_bytes",),
    [
        pytest.param(LF_SCRIPT, id="lf-script"),
        pytest.param(CRLF_SCRIPT, id="crlf-script"),
    ],
)
def test_install_default_newline_regression(tmp_path, script_bytes):
    """Regression: without an explicit policy, the shebang line ends with LF
    and the remaining original bytes are preserved, as before."""
    name = "newline_demo"
    wheel = _make_wheel(tmp_path, name, script_bytes)
    target = tmp_path / "installed"

    _install(wheel, target)  # no script_newline: historical behaviour

    installed = target / "scripts" / "demo-script"
    data = installed.read_bytes()
    expected = b"#!" + _INTERPRETER.encode() + b"\n"
    expected += script_bytes.split(b"\n", 1)[1]
    assert data == expected

    records = _read_record(target, name)
    (script_row,) = [row for row in records.values() if row[0].endswith("demo-script")]
    digest = hashlib.sha256(data).digest()
    expected_hash = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    assert script_row[1] == f"sha256={expected_hash}"
    assert script_row[2] == str(len(data))
