"""Directed tests for pre-install RECORD validation.

These tests use small, signature-free wheels (no ``RECORD.jws`` /
``RECORD.p7s``) without duplicate paths, and verify that ``install`` validates
the wheel's RECORD before writing anything to the destination.
"""

import hashlib
import zipfile
from base64 import urlsafe_b64encode
from unittest import mock

import pytest

from installer import install
from installer.sources import WheelFile


def _b64_nopad(data: bytes) -> str:
    """Return the URL-safe base64 (no padding) SHA-256 digest of ``data``."""
    return urlsafe_b64encode(hashlib.sha256(data).digest()).decode("ascii").rstrip("=")


def _record_line(path: str, content: bytes) -> str:
    """Build a ``path,hash,size`` RECORD line for the given file content."""
    return f"{path},sha256={_b64_nopad(content)},{len(content)}"


def _build_wheel(path, files) -> None:
    """Write ``{name: content}`` into a zip archive at ``path``."""
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)


def _replace_in_wheel(path, name: str, content: bytes) -> None:
    """Rewrite the wheel at ``path`` with the entry ``name`` replaced by ``content``."""
    files = {}
    with zipfile.ZipFile(path) as archive:
        for item in archive.infolist():
            if item.filename == name:
                files[item.filename] = content
            else:
                files[item.filename] = archive.read(item.filename)
    _build_wheel(path, files)


@pytest.fixture
def small_wheel(tmp_path):
    """A minimal valid wheel with three files: one package file, WHEEL, RECORD."""
    dist_info = "smallpkg-1.0.0.dist-info"
    init_py = b'"""small package."""\n'
    wheel_meta = (
        b"Wheel-Version: 1.0\n"
        b"Generator: test-fixture\n"
        b"Root-Is-Purelib: true\n"
        b"Tag: py3-none-any\n"
    )
    record = (
        "\n".join(
            [
                _record_line("smallpkg/__init__.py", init_py),
                _record_line(f"{dist_info}/WHEEL", wheel_meta),
                f"{dist_info}/RECORD,,",
            ]
        )
        + "\n"
    )
    files = {
        "smallpkg/__init__.py": init_py,
        f"{dist_info}/WHEEL": wheel_meta,
        f"{dist_info}/RECORD": record.encode(),
    }
    path = tmp_path / "smallpkg-1.0.0-py3-none-any.whl"
    _build_wheel(path, files)
    return path


def _mock_destination():
    """A destination that records writes instead of writing to disk."""
    destination = mock.Mock()
    destination.write_file.side_effect = lambda scheme, path, stream, is_executable: (
        path,
        scheme,
        0,
    )
    destination.write_script.side_effect = lambda name, module, attr, section: (
        name,
        module,
        attr,
        section,
    )
    return destination


class TestInstallRecordValidation:
    def test_small_wheel_installs_successfully(self, small_wheel):
        destination = _mock_destination()

        with WheelFile.open(small_wheel) as source:
            install(source, destination, {})

        # The package file and WHEEL are written; RECORD itself is written
        # once at finalization.
        written = {
            call.kwargs["path"] for call in destination.write_file.call_args_list
        }
        assert written == {
            "smallpkg/__init__.py",
            "smallpkg-1.0.0.dist-info/WHEEL",
        }
        destination.finalize_installation.assert_called_once()

    def test_modified_file_without_record_change_writes_nothing(self, small_wheel):
        # Tamper with the package file without updating RECORD.
        _replace_in_wheel(
            small_wheel,
            "smallpkg/__init__.py",
            b'"""small package (tampered)."""\n',
        )
        destination = _mock_destination()

        with (
            WheelFile.open(small_wheel) as source,
            pytest.raises(WheelFile.validation_error, match="didn't match RECORD"),
        ):
            install(source, destination, {})

        # No file may have been written before validation failed.
        assert destination.write_file.call_count == 0
        assert destination.finalize_installation.call_count == 0

    def test_record_with_empty_hash_is_legal(self, small_wheel):
        # RECORD itself must have an empty hash/size per PEP 376; the wheel
        # fixture already encodes this and must install without error.
        destination = _mock_destination()

        with WheelFile.open(small_wheel) as source:
            install(source, destination, {})

        destination.finalize_installation.assert_called_once()
