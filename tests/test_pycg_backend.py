import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from how2trust.analyzer import Index, analyze
from how2trust.pycg_backend import supplement_calls


class PyCGIntegrationTests(unittest.TestCase):
    def test_src_layout_resolves_callback_from_indexed_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pkg = root / "src" / "orders"
            pkg.mkdir(parents=True)
            (pkg / "__init__.py").write_text("", encoding="utf8")
            (pkg / "runner.py").write_text("def dispatch(fn):\n    return fn()\n", encoding="utf8")
            main = root / "cli.py"
            original = "# coding: latin-1\nfrom orders.runner import dispatch\ndef café(): pass\ndispatch(café)\n"
            main.write_bytes(original.encode("latin-1"))
            index = Index(root)
            index.load()
            index.connect()
            main.write_text("raise RuntimeError('changed since index')", encoding="utf8")
            supplement_calls(index)
        inferred = [e for e in index.edges if e.get("engine") == "pycg"]
        self.assertEqual([(e["file"], e["line"], e["target"]) for e in inferred],
                         [("src/orders/runner.py", 2, "function:cli.py:café")])
        self.assertEqual(index.files["cli.py"], original)

    def test_selected_package_resolves_relative_import(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "__init__.py").write_text("from .run import apply\nfrom .cb import done\napply(done)\n", encoding="utf8")
            (root / "run.py").write_text("def apply(fn): return fn()\n", encoding="utf8")
            (root / "cb.py").write_text("def done(): pass\n", encoding="utf8")
            graph = analyze(root)
        self.assertEqual([(e["file"], e["target"]) for e in graph["edges"] if e.get("engine") == "pycg"],
                         [("run.py", "function:cb.py:done")])

    def test_worker_failure_retains_native_edges_and_unresolved_record(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app.py").write_text("def run(fn): fn()\nrun(print)\n", encoding="utf8")
            with patch("how2trust.pycg_backend.subprocess.run", side_effect=OSError("worker unavailable")):
                graph = analyze(root)
        self.assertTrue(any(e["kind"] == "calls" and e["target"] == "function:app.py:run" for e in graph["edges"]))
        self.assertTrue(any(d.get("engine") == "pycg" and "失败" in d["message"] for d in graph["diagnostics"]))
        self.assertTrue(any(d.get("caller") == "function:app.py:run" for d in graph["diagnostics"]))
