"""Observe a single Python definition during an opted-in acceptance check."""

import atexit
from contextlib import contextmanager
from inspect import CO_NEWLOCALS
import json
import os
from pathlib import Path
import sys
import tempfile
import tokenize
from types import CodeType


def install(folder, filename, name, first):
    """Run in the test interpreter, including children that inherit PYTHONPATH."""
    if sys.gettrace() is not None:
        return
    filename = os.path.normcase(filename)
    hits = 0
    lines = set()

    def local(frame, event, arg):
        if event == "line":
            lines.add(frame.f_lineno)
        return local

    def enter(frame, event, arg):
        nonlocal hits
        code = frame.f_code
        if event == "call" and code.co_name == name and code.co_firstlineno == first:
            if os.path.normcase(os.path.abspath(code.co_filename)) == filename:
                hits += 1
                return local
        return None

    def finish():
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=folder,
                                         prefix="visits-", suffix=".json", delete=False) as dest:
            json.dump({"hits": hits, "lines": sorted(lines), "active": sys.gettrace() is enter}, dest)

    atexit.register(finish)
    sys.settrace(enter)


def _definition(filename, symbol):
    with tokenize.open(filename) as stream:
        source = stream.read()
    codes = [compile(source, str(filename), "exec", dont_inherit=True)]
    for part in symbol.split("."):
        codes = [child for parent in codes for child in parent.co_consts
                 if isinstance(child, CodeType) and child.co_name == part]
    if len(codes) != 1 or not codes[0].co_flags & CO_NEWLOCALS:
        raise ValueError("该版本中未找到唯一的 Python 函数或方法")
    return codes[0], source.splitlines()


@contextmanager
def trace_check(check, checkout, env, result):
    if not check.get("trace"):
        yield
        return
    file, symbol = check["source"].split("::", 1)
    capture = {"status": "unavailable", "file": file, "symbol": symbol,
               "interpreters": 0, "hits": 0, "executed": [], "sourceLines": []}
    result["traceEvidence"] = capture
    target = (checkout / file).resolve()
    try:
        if not target.is_relative_to(checkout.resolve()):
            raise ValueError("跟踪目标不在快照内")
        code, lines = _definition(target, symbol)
    except (OSError, SyntaxError, ValueError) as exc:
        capture["detail"] = f"无法定位该版本的跟踪目标：{exc}"
        yield
        return

    capture["line"] = code.co_firstlineno
    with tempfile.TemporaryDirectory(prefix="how2trust-trace-") as temp:
        folder = Path(temp)
        # Keep an existing sitecustomize from the application's or the host's
        # Python path intact. A previously installed tracer keeps its own hook.
        hook = (
            "from importlib.machinery import PathFinder\n"
            "from importlib.util import module_from_spec\n"
            "import os, runpy, sys\n"
            f"_h2t_dir = {str(folder)!r}\n"
            "_h2t_spec = PathFinder.find_spec('sitecustomize', [p for p in sys.path "
            "if os.path.abspath(p) != _h2t_dir])\n"
            "if _h2t_spec and _h2t_spec.loader:\n"
            "    _h2t_original = module_from_spec(_h2t_spec)\n"
            "    sys.modules['sitecustomize'] = _h2t_original\n"
            "    _h2t_spec.loader.exec_module(_h2t_original)\n"
            f"runpy.run_path({str(Path(__file__).resolve())!r})['install']("
            f"_h2t_dir, {str(target)!r}, {code.co_name!r}, {code.co_firstlineno})\n"
        )
        (folder / "sitecustomize.py").write_text(hook, encoding="utf-8")
        env["PYTHONPATH"] = str(folder) + os.pathsep + env["PYTHONPATH"]
        yield
        executed = set()
        active = False
        for record in folder.glob("visits-*.json"):
            try:
                observed = json.loads(record.read_bytes())
            except (OSError, ValueError):
                continue
            capture["interpreters"] += 1
            capture["hits"] += observed["hits"]
            executed.update(observed["lines"])
            active |= observed["active"]
        capture["executed"] = sorted(executed)
        capture["sourceLines"] = [{"line": n, "text": lines[n - 1]} for n in sorted(executed) if 1 <= n <= len(lines)]
        if capture["hits"]:
            capture.update(status="observed", detail="在本场景的 Python 调用帧中观察到所选定义。")
        elif active:
            capture.update(status="not_observed", detail="已接入的 Python 解释器主线程中未观察到目标调用。")
        else:
            capture["detail"] = "未收到活动跟踪钩子的结果。隔离模式、禁用 site 或覆盖跟踪钩子会使调用不可观测。"
