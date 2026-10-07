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


@pytest.fixture
def case_colliding_wheel(tmp_path):
    """A wheel that only installs cleanly on a case-sensitive filesystem.

    It contains both ``A.py`` and ``a.py`` (target collision inside purelib)
    and, via a ``.data/scripts`` entry and a generated entry point script,
    a second collision between the archive path ``FOO`` and the generated
    script ``foo``.
    """
    return mock_case_colliding_wheel(tmp_path)


def mock_case_colliding_wheel(tmp_path):
    name = "collider"
    version = "1.0.0"
    path = tmp_path / f"{name}-{version}-py3-none-any.whl"
    files = {
        "A.py": b"top = 'uppercase'\n",
        "a.py": b"top = 'lowercase'\n",
        f"{name}-{version}.data/scripts/FOO": b"#!/bin/sh\necho FOO\n",
        f"{name}-{version}.dist-info/WHEEL": b"""\
            Wheel-Version: 1.0
            Generator: magic (1.0.0)
            Root-Is-Purelib: true
            Tag: py3-none-any
        """,
        f"{name}-{version}.dist-info/METADATA": f"""\
            Metadata-Version: 2.1
            Name: {name}
            Version: {version}
        """.encode(),
        f"{name}-{version}.dist-info/entry_points.txt": b"""\
            [console_scripts]
            foo = collider:main
        """,
        f"{name}-{version}.dist-info/top_level.txt": b"""\
            A.py
            a.py
        """,
    }

    record_lines = []
    written_files = {}
    for filename, content in files.items():
        data = textwrap.dedent(content.decode()).encode()
        written_files[filename] = data
        digest = hashlib.sha256(data).hexdigest()
        record_lines.append(f"{filename},sha256={digest},{len(data)}")
    record_lines.append(f"{name}-{version}.dist-info/RECORD,,")

    with zipfile.ZipFile(path, "w") as archive:
        for filename, data in written_files.items():
            archive.writestr(filename, data)
        archive.writestr(
            f"{name}-{version}.dist-info/RECORD",
            ("\n".join(record_lines) + "\n").encode(),
        )

    return path


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
        f"{name}-1.0.0.dist-info/RECORD": f"""\
            {name}/__init__.py,sha256=qZ2qq7xVBAiUFQVv-QBHhdtCUF5p1NsWwSOiD7qdHN0,36
            {name}/__main__.py,sha256=Wd4SyWJOIMsHf_5-0oN6aNFwen8ehJnRo-erk2_K-eY,61
            {name}-1.0.0.data/data/{name}/data.py,sha256=nuFRUNQF5vP7FWE-v5ysyrrfpIaAvfzSiGOgfPpLOeI,17
            {name}-1.0.0.dist-info/top_level.txt,sha256=SW-yrrF_c8KlserorMw54inhLjZ3_YIuLz7fYT4f8ao,6
            {name}-1.0.0.dist-info/entry_points.txt,sha256=AxJl21_zgoNWjCfvSkC9u_rWSzGyCtCzhl84n979jCc,75
            {name}-1.0.0.dist-info/WHEEL,sha256=1DrXMF1THfnBjsdS5sZn-e7BKcmUn7jnMbShGeZomgc,84
            {name}-1.0.0.dist-info/METADATA,sha256=hRhZavK_Y6WqKurFFAABDnoVMjZFBH0NJRjwLOutnJI,236
            {name}-1.0.0.dist-info/RECORD,,
        """.encode(),
    }

    with zipfile.ZipFile(path, "w") as archive:
        for filename, indented_content in files.items():
            archive.writestr(
                filename,
                textwrap.dedent(indented_content.decode("utf-8")).encode("utf-8"),
            )

    return path
