import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from how2trust.claim_drift import read_previous_graph


ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "audit/terra-fulfillment/project"
STORED_GRAPH = ROOT / "audit/fulfillment-deepseek-improved/architecture.html"


class SemArcBenchmarkTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.data = Path(temp.name)
        self.export = self.data / "graph.json"
        self.report = self.data / "result.json"
        self.reference = self.data / "reference.json"
        components = {"Core": ["__main__.py", "store.py", "__init__.py", "missing/store.py"],
                      "Dispatch": ["__main__.py", "store.py"], "Fixtures": ["tests/test_orders.py"]}
        self.reference.write_text(json.dumps({"structure": [
            {"@type": "group", "name": name, "nested": [{"@type": "item", "name": p} for p in paths]}
            for name, paths in components.items()]}), encoding="utf-8")

    def run_benchmark(self, *args, check=True):
        return subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "tests/bench_semarc.py"),
                               str(PROJECT), str(self.reference), "--source-prefix", "dispatchdesk",
                               "--json", str(self.report), *args],
                              check=check, capture_output=True, encoding="utf-8")

    def test_model_groups_project_symbol_files_and_keep_every_aligned_file_in_recall(self):
        actual = read_previous_graph(STORED_GRAPH.read_text(encoding="utf-8"))
        self.export.write_text(json.dumps(actual), encoding="utf-8")
        self.run_benchmark("--graph", str(self.export))
        report = json.loads(self.report.read_text(encoding="utf-8"))
        self.assertEqual(report["alignment"]["missingFromSource"], ["dispatchdesk/missing/store.py"])
        self.assertEqual(report["alignment"]["excludedNonProductionOrNonPython"], ["dispatchdesk/tests/test_orders.py"])
        self.assertEqual(report["alignment"]["productionReferencePairs"], 6)
        self.assertEqual(report["alignment"]["alignedReferencePairs"], 3)
        self.assertEqual(report["reference"]["shared"]["dispatchdesk/store.py"], ["Core", "Dispatch"])
        model = report["workbenchBusiness"]
        self.assertEqual(model["model"], actual["semantics"]["model"])
        self.assertEqual(model["ungrouped"], ["dispatchdesk/__init__.py"])
        self.assertEqual(len(model["shared"]["dispatchdesk/store.py"]), 6)
        self.assertEqual((model["matchedPairs"], model["predictedPairs"], model["referencePairs"]), (1, 1, 3))
        self.assertEqual((model["precision"], model["recall"]), (1, 1 / 3))
        self.assertEqual(report["directoryBaseline"]["predictedPairs"], 3)

    def test_structure_only_and_missing_export_yield_no_model_score(self):
        graph = read_previous_graph(STORED_GRAPH.read_text(encoding="utf-8"))
        del graph["semantics"]
        self.export.write_text(json.dumps(graph), encoding="utf-8")
        for args in [(), ("--graph", str(self.export))]:
            self.run_benchmark(*args)
            model = json.loads(self.report.read_text(encoding="utf-8"))["workbenchBusiness"]
            self.assertEqual(model["status"], "not_measured")
            self.assertNotIn("precision", model)
        self.run_benchmark("--source-prefix", "wrong_package")
        no_match = json.loads(self.report.read_text(encoding="utf-8"))
        self.assertEqual(no_match["alignment"]["aligned"], [])
        self.assertEqual(no_match["directoryBaseline"]["status"], "not_measured")

    def test_rejects_external_model_export_from_different_source(self):
        graph = read_previous_graph(STORED_GRAPH.read_text(encoding="utf-8"))
        graph["files"]["dispatchdesk/store.py"] += "\n# other revision\n"
        self.export.write_text(json.dumps(graph), encoding="utf-8")
        result = self.run_benchmark("--graph", str(self.export), check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Python 源文件与待测仓库不一致", result.stderr)
        self.assertFalse(self.report.exists())
