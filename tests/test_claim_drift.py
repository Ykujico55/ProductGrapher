import copy
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from how2trust.__main__ import main
from how2trust.claim_drift import compare_claim_evidence, read_previous_graph
from how2trust.outputs import existing_export
from how2trust.project import build_graph, digest
from how2trust.semantics import _overview, validate_semantics


APP = """def retry():
    return True

def return_goods(quality):
    if quality == 'sellable':
        retry()
    return 'recorded'
"""


def citation(line, file="orders.py"):
    return {"file": file, "line": line, "end": line}


def freeze(graph, claims):
    item = {"nodeId": "module:orders.py", "title": "处理退货", "aliases": [], "text": "登记退货",
            "refs": [citation(1)]}
    guide = {"schema": "how2trust.semantics.v1", "snapshot": graph["snapshot"], "model": "fixture",
             "provider": "local", "promptVersion": "4", "overview": _overview(claims), "items": [item],
             "coverage": {"eligible": len(graph["nodes"]), "explained": 1}}
    guide["fingerprint"] = digest(guide)
    graph["semantics"] = validate_semantics(graph, guide)
    return graph


class ClaimDriftTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.file = self.root / "orders.py"
        self.file.write_text(APP, encoding="utf-8")
        self.previous = freeze(build_graph(self.root), [
            {"text": "可售退货才重试。", "refs": [citation(6)]},
            {"text": "重试返回 True。", "refs": [citation(2)]}])

    def compare(self, source):
        self.file.write_text(source, encoding="utf-8")
        return compare_claim_evidence(self.previous, build_graph(self.root))

    def test_moves_within_function_ignore_absolute_guard_lines(self):
        source = APP.replace("    if quality", "    # Inventory policy\n\n    if quality")
        report = self.compare(source)
        self.assertEqual(report["counts"], {"missing": 0, "changed": 0, "unresolved": 0, "moved": 1, "unchanged": 1})
        pair = report["claims"][0]["refs"][0]
        self.assertEqual((pair["old"], pair["new"]), (citation(6), citation(8)))
        self.assertEqual(pair["oldText"], pair["newText"])
        self.assertEqual(report, self.compare(source))
        # A change after the cited call in the same function need not invalidate its cited condition.
        report = self.compare(APP.replace("return 'recorded'", "return 'receipt'"))
        self.assertEqual(report["counts"]["unchanged"], 2)

    def test_uncited_guard_changes_on_an_unchanged_call_line(self):
        report = self.compare(APP.replace("'sellable'", "'damaged'"))
        pair = report["claims"][0]["refs"][0]
        self.assertEqual(pair["status"], "changed")
        self.assertEqual(pair["old"], pair["new"])
        self.assertEqual(pair["oldText"], pair["newText"])
        self.assertIn("sellable", pair["oldConditions"][0]["text"])
        self.assertIn("damaged", pair["newConditions"][0]["text"])
        self.assertEqual(pair["newConditions"][0]["line"], 5)
        self.assertEqual(report["counts"]["unchanged"], 1)

    def test_moving_assertions_preserves_conditions_but_changing_their_values_does_not(self):
        source = ("def retry(): pass\n"
                  "def submit(quantity, label):\n"
                  "    assert quantity >= 10 and label != '第 3 行'\n"
                  "    if quantity == 60:\n"
                  "        return 42\n"
                  "    if quantity < 100:\n"
                  "        retry()\n")
        self.file.write_text(source, encoding="utf-8")
        self.previous = freeze(build_graph(self.root), [{"text": "按数量重试。", "refs": [citation(7)]}])
        moved = self.compare("\n\n" + source)["claims"][0]["refs"][0]
        self.assertEqual((moved["status"], moved["new"]), ("moved", citation(9)))
        for old, new, kind in [(">= 10", ">= 11", "assertion"), ("第 3 行", "第 4 行", "assertion"),
                               ("return 42", "return 24", "priorExit"), ("< 100", "< 101", "predicate"),
                               ("        retry()", "        pass\n    else:\n        retry()", "predicate")]:
            with self.subTest(old=old):
                row = self.compare(source.replace(old, new))["claims"][0]["refs"][0]
                self.assertEqual(row["status"], "changed")
                self.assertEqual(row["oldText"], row["newText"])
                self.assertIn(kind, {guard["kind"] for guard in row["newConditions"]})

    def test_rewritten_and_deleted_call_are_not_relocated_to_another_function(self):
        report = self.compare(APP.replace("        retry()", "        print(quality)"))
        changed = report["claims"][0]["refs"][0]
        self.assertEqual((changed["status"], changed["newText"]), ("changed", "        print(quality)"))
        source = APP.replace("    if quality == 'sellable':\n        retry()\n", "")
        report = self.compare(source + "\ndef unrelated(quality):\n    if quality == 'sellable':\n        retry()\n")
        missing = report["claims"][0]["refs"][0]
        self.assertEqual((missing["status"], missing["new"]), ("missing", None))
        self.assertEqual(report["counts"]["unchanged"], 1)

    def test_ambiguous_repeats_and_unparsable_file_need_review(self):
        source = APP.replace("        retry()", "        retry()\n        retry()")
        report = self.compare(source)
        self.assertEqual(report["claims"][0]["status"], "unresolved")
        self.assertIsNone(report["claims"][0]["refs"][0]["new"])
        self.assertEqual(self.compare("def invalid( \n")["counts"]["unresolved"], 2)

    def test_unchanged_call_text_can_lose_its_target_in_another_file(self):
        self.file.write_text("from target import dispatch\n\ndef send():\n    dispatch()\n", encoding="utf-8")
        target = self.root / "target.py"
        target.write_text("def dispatch(): pass\n", encoding="utf-8")
        previous = freeze(build_graph(self.root), [{"text": "提交发货。", "refs": [citation(4)]}])
        target.write_text("def something_else(): pass\n", encoding="utf-8")
        pair = compare_claim_evidence(previous, build_graph(self.root))["claims"][0]["refs"][0]
        self.assertEqual(pair["status"], "changed")
        self.assertEqual(pair["oldText"], pair["newText"])
        self.assertIn("目标", pair["reason"])

    def test_saved_export_real_guide_and_legacy_single_overview(self):
        project = Path(__file__).resolve().parents[1]
        for name, total in (("audit/fulfillment-deepseek-improved/architecture.html", 9),
                            ("examples/fulfillment-architecture.html", 1)):
            with self.subTest(name=name):
                old = read_previous_graph((project / name).read_text(encoding="utf-8"))
                report = compare_claim_evidence(old, build_graph(project / "audit/terra-fulfillment/project"))
                self.assertEqual(report["counts"]["unchanged"], total)
                self.assertEqual(len(report["claims"]), total)

    def test_cli_emits_source_report_without_writing_guides_or_changing_snapshot(self):
        source = self.compare("\n" + APP)["currentSnapshot"]
        with TemporaryDirectory() as directory:
            path = Path(directory) / "baseline.json"
            guide = Path(directory) / "guide.json"
            frozen = copy.deepcopy(self.previous)
            guide.write_text(json.dumps(frozen.pop("semantics")), encoding="utf-8")
            path.write_text(json.dumps(frozen), encoding="utf-8")
            before = (path.read_bytes(), guide.read_bytes())
            output = self.root / "comparison.json"
            with redirect_stdout(io.StringIO()) as messages:
                self.assertEqual(main(["check-evidence", str(self.root), "--previous", str(path),
                                       "--semantics", str(guide), "--json", str(output)]), 0)
            self.assertIn("orders.py L6", messages.getvalue())
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(report["counts"]["moved"], 2)
            self.assertEqual(existing_export(output), "claim-drift")
            self.assertEqual(build_graph(self.root)["snapshot"], source)
            self.assertEqual((path.read_bytes(), guide.read_bytes()), before)

    def test_rejects_invalid_artifacts_and_preserves_original_guide(self):
        frozen = copy.deepcopy(self.previous)
        original_source = self.previous["files"]["orders.py"]
        with self.assertRaisesRegex(ValueError, "没有业务讲解"):
            compare_claim_evidence({k: v for k, v in frozen.items() if k != "semantics"}, self.previous)
        with self.assertRaisesRegex(ValueError, "旧版源码"):
            read_previous_graph(json.dumps(frozen["semantics"]))
        with self.assertRaisesRegex(ValueError, "快照不一致"):
            validate_semantics(build_graph(self.root) | {"snapshot": "new"}, frozen["semantics"])
        frozen["files"]["orders.py"] += "# mismatched input"
        with self.assertRaisesRegex(ValueError, "指纹"):
            compare_claim_evidence(frozen, self.previous)
        self.assertEqual(self.previous["files"]["orders.py"], original_source)

    def test_file_move_follows_only_a_unique_new_path(self):
        self.file.rename(self.root / "returns.py")
        result = compare_claim_evidence(self.previous, build_graph(self.root))
        self.assertEqual(result["counts"]["moved"], 2)
        self.assertEqual(result["claims"][0]["refs"][0]["new"], citation(6, "returns.py"))
        (self.root / "copy.py").write_text(APP, encoding="utf-8")
        ambiguous = compare_claim_evidence(self.previous, build_graph(self.root))
        self.assertEqual(ambiguous["counts"]["unresolved"], 2)
