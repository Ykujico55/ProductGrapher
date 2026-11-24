"""Resolve additional call sites against the same source snapshot as the native index."""

import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

from .pycg_merge import merge_pycg


def _snapshot_paths(index):
    """Lay out src packages and top-level scripts under one private import root."""
    strip_src = "src/__init__.py" not in index.files
    package = "__init__.py" in index.files
    paths = {}
    for scopes in index.modules.values():
        # Two files claiming the same module name have no unambiguous import target.
        if len(scopes) != 1:
            continue
        original = scopes[0].path
        staged = original.removeprefix("src/") if strip_src else original
        if package:
            staged = "__root__/" + staged
        paths[staged] = original
    return paths


def _document(index, paths):
    # PyCG follows imports. A private tree ensures that even an imported module
    # can only contribute bytes that the user can inspect in the exported graph.
    with TemporaryDirectory(prefix="how2trust-pycg-") as directory:
        root = Path(directory)
        for staged, original in paths.items():
            target = root / staged
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(index.files[original].encode(index.encodings[original]))
        request = json.dumps({"root": directory, "files": sorted(paths)}).encode("utf-8")
        worker = Path(__file__).with_name("pycg_worker.py")
        process = subprocess.run([sys.executable, "-I", "-S", str(worker.resolve())], input=request,
                                 capture_output=True, check=False)
    if process.returncode:
        # The traceback can contain temp paths and excerpts from the source.
        lines = process.stderr.decode("utf-8", errors="replace").rstrip().splitlines()
        error = lines[-1].partition(":")[0] if lines else ""
        cause = error if error.isidentifier() else f"exit {process.returncode}"
        raise RuntimeError(cause)
    document = json.loads(process.stdout)
    if document.get("format") != "pycg/1":
        raise RuntimeError("未知的 PyCG 输出格式")
    for module in document["modules"].values():
        module["file"] = paths[module["file"]]
    return document


def supplement_calls(index):
    paths = _snapshot_paths(index)
    if not paths:
        return
    try:
        document = _document(index, paths)
    except (OSError, UnicodeError, json.JSONDecodeError, RuntimeError) as exc:
        cause = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
        index.diagnostics.append({"file": "", "line": 1, "end": 1, "engine": "pycg",
                                  "message": f"PyCG 补充分析失败（{cause}），本图仅包含基础分析结果。"})
        return
    for module in document["modules"].values():
        if module.get("status") in {"partial", "failed"}:
            index.diagnostics.append({"file": module["file"], "line": 1, "end": 1, "engine": "pycg",
                                      "message": f"PyCG 未完整分析此模块（{module['cause']}）。"})
    merge_pycg(index, document)
    return document
