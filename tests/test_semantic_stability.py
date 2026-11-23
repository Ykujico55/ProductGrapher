import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from how2trust.project import build_graph
from how2trust.semantics import _response_object, generate_semantics, validate_semantics


class SemanticStabilityTests(unittest.TestCase):
    def test_missing_overview_brace_does_not_change_model_content(self):
        text = '{"overview":{"claims":[{"text":"内嵌 \\"items\\"","refs":[]}],"items":[]}'
        self.assertEqual(_response_object(text),
                         {"overview": {"claims": [{"text": '内嵌 "items"', "refs": []}]}, "items": []})
        with self.assertRaisesRegex(ValueError, "JSON"):
            _response_object(text[:-2])

    def test_full_tree_scoped_reviews_and_recovery_without_manual_selection(self):
        class Reader:
            provider, model = "local", "test"

            def __init__(self):
                self.explains, self.verifies, self.reviews = 0, 0, []

            def complete(self, messages):
                request = json.loads(messages[-1]["content"])
                self.reviews.append(request)
                if request["task"] == "verify":
                    self.verifies += 1
                    return '{"claimReplacements":[],"itemReplacements":[]}'
                self.explains += 1
                node = next(n for n in request["nodes"] if n["qualname"] == "create")
                ref = {"file": "orders.py", "line": 1, "end": 3}
                if self.explains == 1:
                    ref["end"] = 600
                return json.dumps({"overview": {"claims": [{"text": "处理订单", "refs": [ref]}]},
                                   "items": [{"nodeId": node["id"], "aliases": [], "title": "订单",
                                              "text": "处理订单", "refs": [ref]}]})

        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "orders.py").write_text("def create(order):\n    if order:\n        return order\n")
            (root / "tests").mkdir()
            (root / "tests/test_app.py").write_text("def fake():\n    pass\n")
            graph, reader = build_graph(root), Reader()
            cache = root / "cache"
            events = []
            def progress(stage, **info):
                events.append((stage, info))
            guide = generate_semantics(graph, reader, checkpoint=cache, progress=progress)
            self.assertEqual([i["phase"] for stage, i in events if stage == "batches"],
                             ["reading", "reviewing", "done"])
            self.assertTrue(all((i["batch"], i["total"], i["files"]) == (1, 1, ["orders.py"])
                                for stage, i in events if stage == "batches"))
            self.assertEqual([stage for stage, _ in events if stage != "batches"], ["overview", "grouping"])
            self.assertEqual(guide["coverage"], {"eligible": 2, "explained": 1})
            self.assertEqual(reader.explains, 2)
            self.assertEqual(reader.verifies, 1)
            self.assertIn("600", reader.reviews[1]["validationFeedback"]["error"])
            self.assertNotIn("tests/test_app.py", str(reader.reviews))
            self.assertEqual(validate_semantics(graph, guide), guide)
            with patch.object(reader, "complete", side_effect=AssertionError("unexpected model call")):
                events.clear()
                self.assertEqual(generate_semantics(graph, reader, checkpoint=cache, progress=progress), guide)
                self.assertEqual([i["phase"] for stage, i in events if stage == "batches"], ["cached"])

    def test_batch_failure_reports_source_and_propagates_original_error(self):
        error = RuntimeError("无法连接模型服务")
        class Reader:
            def complete(self, messages):
                raise error
        with TemporaryDirectory() as directory:
            (Path(directory) / "items.py").write_text("def pack(): pass\n")
            graph = build_graph(Path(directory))
        events = []
        with self.assertRaises(RuntimeError) as raised:
            generate_semantics(graph, Reader(), progress=lambda stage, **fields: events.append((stage, fields)))
        self.assertIs(raised.exception, error)
        self.assertEqual(raised.exception.semantic_batch, 1)
        self.assertEqual(events[-1], ("batches", {"phase": "failed", "files": ["items.py"], "batch": 1, "total": 1}))

    def test_review_includes_the_callers_bypass_even_for_a_single_call_site(self):
        from how2trust.semantic_evidence import review_material
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "service.py").write_text("def apply_rule(record):\n    if record.ready:\n        record.write()\n")
            (root / "cli.py").write_text("from service import apply_rule\ndef drive(records):\n"
                                         "    for record in records:\n        if record.cached:\n"
                                         "            continue\n        apply_rule(record)\n")
            graph = build_graph(root)
        ref = {"file": "service.py", "line": 2, "end": 3}
        claim = {"text": "写入记录", "refs": [ref]}
        review = review_material(graph, {"sources": []}, [claim], [], 5000)
        callers = "\n".join(b["text"] for b in review["sources"] if b["file"] == "cli.py")
        self.assertIn("if record.cached:", callers)
        self.assertIn("continue", callers)
        self.assertIn("apply_rule(record)", callers)
