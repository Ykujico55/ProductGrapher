"""Rebuild pycg.zip from the pinned upstream codeload source archive."""

import argparse
import hashlib
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo


COMMIT = "79a0ee706c44f8e63721f69e1ee185aa27573418"
ARCHIVE_SHA256 = "9372e593ab9436cd2843a83fb8d624fbf212e3f9ed13fc62743edcbe3d0c5250"
VENDOR = Path(__file__).resolve().parent
PACKAGE = "PyCG-" + COMMIT + "/"

# On older Python versions the parser cannot produce the missing node type.
# Use a private class for that pattern, without changing the stdlib ast module.
AST_COMPAT = b'''"""AST node types that exist only on newer Python interpreters."""
import ast


class _UnavailableNode(ast.AST):
    pass


TypeAlias = getattr(ast, "TypeAlias", _UnavailableNode)
TryStar = getattr(ast, "TryStar", _UnavailableNode)
'''


def adapt(name, data):
    if name in {"pycg/analysis/annotations.py", "pycg/analysis/builder.py"}:
        data = data.replace(b"import ast\n", b"import ast\nfrom . import _ast_compat\n")
        return data.replace(b"ast.TypeAlias", b"_ast_compat.TypeAlias").replace(
            b"ast.TryStar", b"_ast_compat.TryStar")
    if name == "pycg/analysis/stubs.py":
        data = data.replace(b"from pathlib import Path", b"from importlib.resources import files\nfrom pathlib import Path")
        data = data.replace(b'Path(__file__).with_name("stub_data")', b'files(__package__) / "stub_data"')
        return data.replace(b"self._directory = Path(directory)",
                            b"self._directory = DATA if directory is DATA else Path(directory)")
    if name == "pycg/formats/document.py":
        data = data.replace(b"from pathlib import Path", b"from importlib.resources import files")
        return data.replace(b'Path(__file__).resolve().parent.parent / "analysis/stub_data/README.md"',
                            b'files("pycg.analysis") / "stub_data" / "README.md"')
    return data


def build(archive):
    if hashlib.sha256(archive.read_bytes()).hexdigest() != ARCHIVE_SHA256:
        raise ValueError(f"Unexpected SHA-256 for the source snapshot at {COMMIT}")
    entries = {"pycg/analysis/_ast_compat.py": AST_COMPAT}
    with ZipFile(archive) as src:
        for name in src.namelist():
            path = name.removeprefix(PACKAGE)
            if not path.startswith("pycg/") or path.startswith("pycg/view/") or path == "pycg/__main__.py":
                continue
            if name.endswith("/") or not (path.endswith(".py") or path.startswith("pycg/analysis/stub_data/")):
                continue
            original = src.read(name)
            patched = adapt(path, original)
            if patched != original:
                patched = b"# Modified by How2Trust for offline ZIP loading or Python 3.10+ AST support.\n" + patched
            entries[path] = patched
        entries["pycg/LICENCE"] = src.read(PACKAGE + "LICENCE")
    (VENDOR / "LICENSE-pycg.txt").write_bytes(entries["pycg/LICENCE"])
    (VENDOR / "LICENSE-typeshed.txt").write_bytes(entries["pycg/analysis/stub_data/LICENSE"])
    with ZipFile(VENDOR / "pycg.zip", "w") as dest:
        for name, data in sorted(entries.items()):
            info = ZipInfo(name)
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            dest.writestr(info, data, compress_type=ZIP_DEFLATED, compresslevel=9)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    build(parser.parse_args().archive)
