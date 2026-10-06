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


def mock_wheel(tmp_path, name):
    path = tmp_path / f"{name}-1.0.0-py2.py3-none-any.whl"
    files = {
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

    # Compute the RECORD file from the actual contents, so that the hashes and
    # sizes always match, regardless of the package name.
    contents = {
        filename: textwrap.dedent(indented_content.decode("utf-8")).encode("utf-8")
        for filename, indented_content in files.items()
    }
    record_lines = []
    for filename, content in sorted(contents.items()):
        if filename[-1:] == "/":  # skip directory entries
            continue
        digest = hashlib.sha256(content).digest()
        hash_ = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
        record_lines.append(f"{filename},sha256={hash_},{len(content)}")
    # RECORD's own entry has an empty hash and size, as required by the spec.
    record_lines.append(f"{name}-1.0.0.dist-info/RECORD,,")
    contents[f"{name}-1.0.0.dist-info/RECORD"] = (
        "\n".join(record_lines) + "\n"
    ).encode("utf-8")

    with zipfile.ZipFile(path, "w") as archive:
        for filename, content in contents.items():
            archive.writestr(filename, content)

    return path
