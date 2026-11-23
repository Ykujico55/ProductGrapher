"""Classify repository-relative paths without importing or executing the project."""

from pathlib import PurePosixPath


def source_role(path):
    parts = PurePosixPath(path.replace("\\", "/").casefold()).parts
    folders, name = parts[:-1], parts[-1]
    if (any(folder in {"tests", "test", "fixtures", "__tests__"} for folder in folders)
            or name == "conftest.py"
            or (name.endswith(".py") and (name.startswith("test_") or name.endswith("_test.py")))):
        return "test"
    if any(folder in {"examples", "example", "demos", "demo", "docs", "doc"} for folder in folders):
        return "example"
    return "production"
