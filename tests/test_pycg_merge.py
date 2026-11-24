import copy
import tempfile
import unittest
from pathlib import Path

from how2trust.analyzer import Index
from how2trust.pycg_merge import merge_pycg


class PyCGMergeTest(unittest.TestCase):
    def index(self, source):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        package = root / "src/pkg"
        package.mkdir(parents=True)
        (package / "app.py").write_text(source, encoding="utf-8")
        (package / "targets.py").write_text("def save(): pass\n", encoding="utf-8")
        index = Index(root)
        index.load()
        index.connect()
        return index

    def document(self, sites):
        return {
            "format": "pycg/1",
            "modules": {"pkg.app": {"file": "src/pkg/app.py"}, "pkg.targets": {"file": "src/pkg/targets.py"}},
            "definitions": {
                "pkg.app.run": {"kind": "function", "module": "pkg.app", "lines": [1, 2]},
                "pkg.targets.save": {"kind": "function", "module": "pkg.targets", "lines": [1, 1]},
            },
            "calls": {"pkg.app.run": sites},
        }

    def test_same_line_diagnostics_and_nested_call_spans(self):
        index = self.index("def run(fn):\n    文 = 0; fn(); fn(); fn()()\n")
        path = "src/pkg/app.py"
        positions = sorted(index.unresolved_calls)
        self.assertEqual(len(positions), 4)
        target = "pkg.targets.save"
        sites = [{"line": line, "col": col, "end": [end, end_col], "callees": [target]}
                 for _, line, col, end, end_col in (positions[0], positions[-1])]
        doc = self.document(sites)
        self.assertEqual(merge_pycg(index, doc), 2)
        self.assertEqual(merge_pycg(index, doc), 0)
        added = [e for e in index.edges if e.get("engine") == "pycg"]
        self.assertEqual([(e["file"], e["line"], e["column"], e["endColumn"], e["expression"]) for e in added],
                         [(path, 2, positions[0][2] + 1, positions[0][-1], "fn"),
                          (path, 2, positions[-1][2] + 1, positions[-1][-1], "fn()")])
        self.assertTrue(all(e["target"] == "function:src/pkg/targets.py:save" for e in added))
        self.assertEqual(set(index.unresolved_calls), set(positions[1:-1]))
        self.assertEqual([d["message"] for d in index.diagnostics], ["调用目标未解析 · fn"] * 2)

    def test_ambiguous_external_implicit_and_unresolved_remain_diagnostics(self):
        index = self.index("def run(fn):\n    fn()\n")
        site = {"line": 2, "col": 4, "end": [2, 8], "callees": ["pkg.targets.save"]}
        for replacement in (
                {"callees": ["pkg.targets.save", "external.unknown"]}, {"callees": ["external.unknown"]},
                {"implicit": "iteration"}, {"callees": [], "unresolved": "computed name"}, {"end": [2, 7]}):
            with self.subTest(replacement=replacement):
                self.assertEqual(merge_pycg(index, self.document([{**site, **replacement}])), 0)
                self.assertEqual(len(index.diagnostics), 1)
        self.assertEqual(merge_pycg(index, self.document([site, {**site, "callees": []}])), 0)

    def test_collapsed_redefinitions_and_mismatched_definition_range(self):
        index = self.index("def dup(): pass\ndef dup(): pass\ndef run(fn):\n    fn()\n")
        site = {"line": 4, "col": 4, "end": [4, 8], "callees": ["pkg.app.dup"]}
        doc = self.document([site])
        doc["definitions"]["pkg.app.dup"] = {"kind": "function", "module": "pkg.app", "lines": [1, 1]}
        self.assertEqual(merge_pycg(index, doc), 0)
        wrong_range = copy.deepcopy(doc)
        wrong_range["calls"]["pkg.app.run"][0]["callees"] = ["pkg.targets.save"]
        wrong_range["definitions"]["pkg.targets.save"]["lines"] = [1, 2]
        self.assertEqual(merge_pycg(index, wrong_range), 0)
        self.assertEqual(len(index.diagnostics), 1)
