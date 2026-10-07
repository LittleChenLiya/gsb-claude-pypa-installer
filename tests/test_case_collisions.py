"""End-to-end tests for pre-install case-insensitive target path checks.

The fixtures here are intentionally small: the colliding wheel is built fully
in memory (a named ``BytesIO`` passed to ``zipfile.ZipFile``) and the clean
wheel is written into pytest's temporary directory.
"""

import base64
import hashlib
import io
import sys
import textwrap
import zipfile
from pathlib import Path

import pytest

from installer import install
from installer.destinations import SchemeDictionaryDestination
from installer.exceptions import DestinationCollisionError
from installer.sources import WheelFile
from installer.utils import SCHEME_NAMES, get_launcher_kind


class _NamedBytesIO(io.BytesIO):
    """A BytesIO with a ``name`` attribute, as zipfile expects from a file."""

    def __init__(self, name: str, initial_bytes: bytes = b"") -> None:
        super().__init__(initial_bytes)
        self.name = name


def _record_for(files: dict[str, bytes], dist_info_dir: str) -> str:
    lines = []
    for filename, data in files.items():
        digest = hashlib.sha256(data).hexdigest()
        lines.append(f"{filename},sha256={digest},{len(data)}")
    lines.append(f"{dist_info_dir}/RECORD,,")
    return "\n".join(lines) + "\n"


def _build_colliding_wheel_stream(script_kind: str = "posix") -> _NamedBytesIO:
    """Build the A.py/a.py + FOO-vs-foo collision wheel entirely in memory."""
    dist_info = "collider-1.0.0.dist-info"
    # With the posix kind the generated script is named "foo"; with a Windows
    # launcher kind it is "foo.exe".
    archived_script = "FOO" if script_kind == "posix" else "FOO.exe"
    files = {
        "A.py": b"top = 'uppercase'\n",
        "a.py": b"top = 'lowercase'\n",
        f"collider-1.0.0.data/scripts/{archived_script}": b"#!/bin/sh\necho FOO\n",
        f"{dist_info}/WHEEL": textwrap.dedent(
            """\
            Wheel-Version: 1.0
            Generator: magic (1.0.0)
            Root-Is-Purelib: true
            Tag: py3-none-any
            """
        ).encode(),
        f"{dist_info}/METADATA": textwrap.dedent(
            """\
            Metadata-Version: 2.1
            Name: collider
            Version: 1.0.0
            """
        ).encode(),
        f"{dist_info}/entry_points.txt": textwrap.dedent(
            """\
            [console_scripts]
            foo = collider:main
            """
        ).encode(),
        f"{dist_info}/top_level.txt": b"A.py\na.py\n",
    }
    files[f"{dist_info}/RECORD"] = _record_for(files, dist_info).encode()

    stream = _NamedBytesIO("collider-1.0.0-py3-none-any.whl")
    with zipfile.ZipFile(stream, "w") as archive:
        # Force the FOO archive entry to appear right before its colliding
        # generated script in install order.
        for filename in sorted(files):
            archive.writestr(filename, files[filename])
    stream.seek(0)
    return stream


def _build_clean_wheel(path: Path) -> Path:
    """Write a small, conflict-free wheel into directory ``path``."""
    path.mkdir(parents=True, exist_ok=True)
    wheel_path = path / "clean-1.0.0-py3-none-any.whl"
    dist_info = "clean-1.0.0.dist-info"
    files = {
        "clean/__init__.py": b"def main():\n    return 'ok'\n",
        f"{dist_info}/WHEEL": textwrap.dedent(
            """\
            Wheel-Version: 1.0
            Generator: magic (1.0.0)
            Root-Is-Purelib: true
            Tag: py3-none-any
            """
        ).encode(),
        f"{dist_info}/METADATA": textwrap.dedent(
            """\
            Metadata-Version: 2.1
            Name: clean
            Version: 1.0.0
            """
        ).encode(),
        f"{dist_info}/top_level.txt": b"clean\n",
    }
    files[f"{dist_info}/RECORD"] = _record_for(files, dist_info).encode()

    with zipfile.ZipFile(wheel_path, "w") as archive:
        for filename, data in files.items():
            archive.writestr(filename, data)
    return wheel_path


def _make_destination(
    root: Path, script_kind: str = "posix"
) -> SchemeDictionaryDestination:
    scheme_dict = {scheme: str(root / scheme) for scheme in SCHEME_NAMES}
    for folder in scheme_dict.values():
        Path(folder).mkdir(parents=True, exist_ok=True)
    return SchemeDictionaryDestination(
        scheme_dict=scheme_dict,
        interpreter=sys.executable,
        script_kind=script_kind,
        bytecode_optimization_levels=(),
    )


def test_colliding_wheel_from_memory_is_rejected_before_any_write(tmp_path):
    target_root = tmp_path / "target"
    destination = _make_destination(target_root)
    wheel_stream = _build_colliding_wheel_stream()

    with zipfile.ZipFile(wheel_stream) as archive:
        source = WheelFile(archive)
        with pytest.raises(DestinationCollisionError) as exc_info:
            install(
                source,
                destination,
                {},
                check_case_collisions=True,
            )

    message = str(exc_info.value)
    # Both collisions are reported, each naming the two source paths.
    assert "A.py" in message
    assert "a.py" in message
    assert "collider-1.0.0.data/scripts/FOO" in message
    assert "entry point console script 'foo'" in message

    # Nothing was written anywhere under the target directory.
    written = [p for p in target_root.rglob("*") if p.is_file()]
    assert written == []


def test_colliding_wheel_native_kind_is_reported_on_windows(tmp_path):
    """Same as above, using the platform's real launcher kind.

    On Windows the generated launcher is ``foo.exe``; the archive carries
    ``FOO.exe``. On other platforms it is the ``foo``/``FOO`` pair.
    """
    script_kind = get_launcher_kind()
    archived_name = "FOO.exe" if script_kind != "posix" else "FOO"
    target_root = tmp_path / "target"
    destination = _make_destination(target_root, script_kind=script_kind)
    wheel_stream = _build_colliding_wheel_stream(script_kind=script_kind)

    with zipfile.ZipFile(wheel_stream) as archive:
        source = WheelFile(archive)
        with pytest.raises(DestinationCollisionError) as exc_info:
            install(source, destination, {}, check_case_collisions=True)

    message = str(exc_info.value)
    assert f"collider-1.0.0.data/scripts/{archived_name}" in message
    assert "entry point console script 'foo'" in message
    assert not [p for p in target_root.rglob("*") if p.is_file()]


def test_clean_wheel_installs_with_valid_record(tmp_path):
    target_root = tmp_path / "target"
    destination = _make_destination(target_root)
    wheel_path = _build_clean_wheel(tmp_path / "wheels")

    with WheelFile.open(wheel_path) as source:
        install(source, destination, {}, check_case_collisions=True)

    purelib = target_root / "purelib"
    module = purelib / "clean" / "__init__.py"
    assert module.read_bytes() == b"def main():\n    return 'ok'\n"

    record_path = purelib / "clean-1.0.0.dist-info" / "RECORD"
    record_lines = record_path.read_text().splitlines()
    # Every installed file is listed exactly once, RECORD itself last-ish
    # and without hash/size.
    record_entries = {}
    for line in record_lines:
        path, hash_value, size = line.split(",")
        record_entries[path] = (hash_value, size)
    assert "clean/__init__.py" in record_entries
    module_data = module.read_bytes()
    digest = base64.urlsafe_b64encode(hashlib.sha256(module_data).digest())
    assert record_entries["clean/__init__.py"] == (
        f"sha256={digest.decode('ascii').rstrip('=')}",
        str(len(module_data)),
    )
    assert record_entries["clean-1.0.0.dist-info/RECORD"] == ("", "")
    # No spurious/colliding entries.
    assert len(record_entries) == len(record_lines) == 5


@pytest.mark.skipif(
    sys.platform.startswith("win"),
    reason="on case-insensitive Windows FS the archive itself can't contain both",
)
def test_default_install_keeps_legacy_behaviour_on_colliding_wheel(tmp_path):
    """Without the option, the original interface/ behaviour is unchanged."""
    target_root = tmp_path / "target"
    destination = _make_destination(target_root)
    wheel_stream = _build_colliding_wheel_stream()

    with zipfile.ZipFile(wheel_stream) as archive:
        source = WheelFile(archive)
        # No check, no error: both files are written (case-sensitive tmp FS).
        install(source, destination, {})

    purelib = target_root / "purelib"
    assert (purelib / "A.py").is_file()
    assert (purelib / "a.py").is_file()
    assert (purelib / "collider-1.0.0.dist-info" / "RECORD").is_file()
