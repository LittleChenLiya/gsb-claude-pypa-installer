"""Tests for pre-install RECORD validation in :func:`installer.install`."""

import base64
import hashlib
import zipfile
from unittest import mock

import pytest

from installer import install
from installer.exceptions import InvalidWheelSource
from installer.sources import WheelFile


def _record_hash(data):
    digest = hashlib.sha256(data).digest()
    return "sha256=" + base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def build_tiny_wheel(path, *, tamper=None):
    """Build a minimal unsigned wheel with three files (plus RECORD).

    :param tamper: ``None`` for a valid wheel, ``"digest"`` to change a file
        without changing its size, or ``"size"`` to also change its size. In
        both tampered cases, RECORD is left unchanged.
    """
    files = {
        "tiny/__init__.py": b"def main():\n    print('tiny')\n",
        "tiny-1.0.0.dist-info/WHEEL": (
            b"Wheel-Version: 1.0\n"
            b"Generator: test (1.0.0)\n"
            b"Root-Is-Purelib: true\n"
            b"Tag: py3-none-any\n"
        ),
        "tiny-1.0.0.dist-info/METADATA": (
            b"Metadata-Version: 2.1\nName: tiny\nVersion: 1.0.0\n"
        ),
    }

    record_lines = [
        f"{name},{_record_hash(content)},{len(content)}"
        for name, content in sorted(files.items())
    ]
    # RECORD's own entry has an empty hash and size, as required by the spec.
    record_lines.append("tiny-1.0.0.dist-info/RECORD,,")
    record = "\n".join(record_lines) + "\n"

    if tamper == "digest":
        # Same length, different content: digest no longer matches RECORD.
        files["tiny/__init__.py"] = b"def main():\n    print('TINY')\n"
    elif tamper == "size":
        # Different length: size (and digest) no longer matches RECORD.
        files["tiny/__init__.py"] = b"def main():\n    print('tiny tiny')\n"

    with zipfile.ZipFile(path, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
        archive.writestr("tiny-1.0.0.dist-info/RECORD", record)

    return path


@pytest.fixture
def tiny_wheel(tmp_path):
    """A valid three-file wheel whose RECORD matches its contents."""
    return build_tiny_wheel(tmp_path / "tiny-1.0.0-py3-none-any.whl")


@pytest.fixture(params=["digest", "size"])
def tampered_tiny_wheel(request, tmp_path):
    """A wheel with one file modified after RECORD was written."""
    return build_tiny_wheel(
        tmp_path / "tiny-1.0.0-py3-none-any.whl", tamper=request.param
    )


def test_install_succeeds_when_record_matches(tiny_wheel):
    destination = mock.Mock()

    with WheelFile.open(tiny_wheel) as source:
        install(source, destination, additional_metadata={})

    # All three regular files are written, then the installation is finalized.
    assert destination.write_file.call_count == 3
    destination.write_script.assert_not_called()
    assert destination.finalize_installation.call_count == 1


def test_install_writes_nothing_when_record_mismatches(tampered_tiny_wheel):
    destination = mock.Mock()

    with WheelFile.open(tampered_tiny_wheel) as source:
        with pytest.raises(InvalidWheelSource):
            install(source, destination, additional_metadata={})

    # Validation failed before anything was written to the destination.
    destination.write_file.assert_not_called()
    destination.write_script.assert_not_called()
    destination.finalize_installation.assert_not_called()


def test_record_own_empty_hash_and_size_is_allowed(tiny_wheel):
    # Sanity check: the fixture's RECORD entry for RECORD itself is empty.
    with zipfile.ZipFile(tiny_wheel) as archive:
        record_text = archive.read("tiny-1.0.0.dist-info/RECORD").decode("utf-8")
    assert "tiny-1.0.0.dist-info/RECORD,," in record_text

    destination = mock.Mock()
    with WheelFile.open(tiny_wheel) as source:
        install(source, destination, additional_metadata={})

    assert destination.finalize_installation.call_count == 1
