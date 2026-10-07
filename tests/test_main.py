import os

import pytest

from installer.__main__ import _get_scheme_dict as get_scheme_dict
from installer.__main__ import _main as main
from installer.exceptions import DestinationCollisionError


def test_get_scheme_dict():
    d = get_scheme_dict(distribution_name="foo")
    assert set(d.keys()) >= {"purelib", "platlib", "headers", "scripts", "data"}


def test_get_scheme_dict_prefix():
    d = get_scheme_dict(distribution_name="foo", prefix="/foo")
    for key in ("purelib", "platlib", "headers", "scripts", "data"):
        assert d[key].startswith(f"{os.sep}foo"), (
            f"{key} does not start with /foo: {d[key]}"
        )


def test_main(fancy_wheel, tmp_path):
    destdir = tmp_path / "dest"

    main([str(fancy_wheel), "-d", str(destdir)], "python -m installer")

    installed_py_files = destdir.rglob("*.py")

    assert {f.stem for f in installed_py_files} == {"__init__", "__main__", "data"}

    installed_pyc_files = destdir.rglob("*.pyc")
    assert {f.name.split(".")[0] for f in installed_pyc_files} == {
        "__init__",
        "__main__",
    }


def test_main_multiple_wheels(fancy_wheel, another_fancy_wheel, tmp_path):
    destdir = tmp_path / "dest"

    main(
        [str(fancy_wheel), str(another_fancy_wheel), "-d", str(destdir)],
        "python -m installer",
    )

    for wheel_name in ("fancy", "another_fancy"):
        installed_py_files = destdir.rglob(f"**/{wheel_name}/**/*.py")
        assert {f.stem for f in installed_py_files} == {"__init__", "__main__", "data"}

        installed_pyc_files = destdir.rglob(f"**/{wheel_name}/**/*.pyc")
        assert {f.name.split(".")[0] for f in installed_pyc_files} == {
            "__init__",
            "__main__",
        }


def test_main_prefix(fancy_wheel, tmp_path):
    destdir = tmp_path / "dest"

    main([str(fancy_wheel), "-d", str(destdir), "-p", "/foo"], "python -m installer")

    installed_py_files = list(destdir.rglob("*.py"))

    for f in installed_py_files:
        assert str(f.parent).startswith(str(destdir / "foo")), (
            f"path does not respect destdir+prefix: {f}"
        )
    assert {f.stem for f in installed_py_files} == {"__init__", "__main__", "data"}

    installed_pyc_files = destdir.rglob("*.pyc")
    assert {f.name.split(".")[0] for f in installed_pyc_files} == {
        "__init__",
        "__main__",
    }


def test_main_no_pyc(fancy_wheel, tmp_path):
    destdir = tmp_path / "dest"

    main(
        [str(fancy_wheel), "-d", str(destdir), "--no-compile-bytecode"],
        "python -m installer",
    )

    installed_py_files = destdir.rglob("*.py")

    assert {f.stem for f in installed_py_files} == {"__init__", "__main__", "data"}

    installed_pyc_files = destdir.rglob("*.pyc")
    assert set(installed_pyc_files) == set()


@pytest.mark.parametrize(
    "validation_part",
    ["all", "entries", "none"],
)
def test_main_validate_record_all_pass(fancy_wheel, tmp_path, validation_part):
    destdir = tmp_path / "dest"

    main(
        [str(fancy_wheel), "-d", str(destdir), "--validate-record", validation_part],
        "python -m installer",
    )

    installed_py_files = destdir.rglob("*.py")

    assert {f.stem for f in installed_py_files} == {"__init__", "__main__", "data"}

    installed_pyc_files = destdir.rglob("*.pyc")
    assert {f.name.split(".")[0] for f in installed_pyc_files} == {
        "__init__",
        "__main__",
    }


def test_main_check_case_collisions_rejects(case_colliding_wheel, tmp_path):
    destdir = tmp_path / "dest"

    with pytest.raises(DestinationCollisionError) as exc_info:
        main(
            [str(case_colliding_wheel), "-d", str(destdir), "--check-case-collisions"],
            "python -m installer",
        )

    assert "A.py" in str(exc_info.value)
    assert "a.py" in str(exc_info.value)

    # The pre-flight check runs before anything is written.
    assert not destdir.exists() or not any(destdir.rglob("*"))


def test_main_check_case_collisions_accepts_clean_wheel(fancy_wheel, tmp_path):
    destdir = tmp_path / "dest"

    main(
        [str(fancy_wheel), "-d", str(destdir), "--check-case-collisions"],
        "python -m installer",
    )

    installed_py_files = destdir.rglob("*.py")
    assert {f.stem for f in installed_py_files} == {"__init__", "__main__", "data"}
    assert list(destdir.rglob("RECORD"))
