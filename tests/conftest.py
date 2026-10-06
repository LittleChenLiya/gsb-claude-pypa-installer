import base64
import hashlib
import textwrap
import zipfile

import pytest


@pytest.fixture
def fancy_wheel(tmp_path):
    return mock_wheel(tmp_path, "fancy")


@pytest.fixture
def another_fancy_wheel(tmp_path):
    return mock_wheel(tmp_path, "another_fancy")


def _b64_nopad(data: bytes) -> str:
    """Return the URL-safe base64 (no padding) SHA-256 digest of ``data``."""
    return (
        base64.urlsafe_b64encode(hashlib.sha256(data).digest())
        .decode("ascii")
        .rstrip("=")
    )


def mock_wheel(tmp_path, name):
    path = tmp_path / f"{name}-1.0.0-py2.py3-none-any.whl"
    raw_files = {
        f"{name}/": b"""""",
        f"{name}/__init__.py": b"""\
            def main():
                print("I'm fancy.")
        """,
        f"{name}/__main__.py": b"""\
            if __name__ == "__main__":
                from . import main
                main()
        """,
        f"{name}-1.0.0.data/data/{name}/": b"""""",
        f"{name}-1.0.0.data/data/{name}/data.py": b"""\
            # put me in data
        """,
        f"{name}-1.0.0.dist-info/": b"""""",
        f"{name}-1.0.0.dist-info/top_level.txt": f"""\
            {name}
        """.encode(),
        f"{name}-1.0.0.dist-info/entry_points.txt": f"""\
            [console_scripts]
            {name} = {name}:main

            [gui_scripts]
            {name}-gui = {name}:main
        """.encode(),
        f"{name}-1.0.0.dist-info/WHEEL": b"""\
            Wheel-Version: 1.0
            Generator: magic (1.0.0)
            Root-Is-Purelib: true
            Tag: py3-none-any
        """,
        f"{name}-1.0.0.dist-info/METADATA": f"""\
            Metadata-Version: 2.1
            Name: {name}
            Version: 1.0.0
            Summary: A fancy package
            Author: Agendaless Consulting
            Author-email: nobody@example.com
            License: MIT
            Keywords: fancy amazing
            Platform: UNKNOWN
            Classifier: Intended Audience :: Developers
        """.encode(),
    }

    # Normalize content (dedent + encode) and compute RECORD hashes dynamically
    # so the fixture is valid for any distribution name.
    files = {}
    for filename, indented_content in raw_files.items():
        files[filename] = textwrap.dedent(indented_content.decode("utf-8")).encode(
            "utf-8"
        )

    record_lines = []
    for filename, content in files.items():
        if filename.endswith("/"):  # directory entries are not in RECORD
            continue
        record_lines.append(f"{filename},sha256={_b64_nopad(content)},{len(content)}")
    record_lines.append(f"{name}-1.0.0.dist-info/RECORD,,")
    files[f"{name}-1.0.0.dist-info/RECORD"] = "\n".join(record_lines).encode("utf-8")

    with zipfile.ZipFile(path, "w") as archive:
        for filename, content in files.items():
            archive.writestr(filename, content)

    return path
