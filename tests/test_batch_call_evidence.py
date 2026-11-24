import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from how2trust.project import build_graph, canonical, digest
from how2trust.semantic_evidence import review_material
from how2trust.semantics import _explain_batch, _node_anchors, generate_semantics


class BatchCallEvidenceTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.repo = Path(temp.name)
        (self.repo / "service.py").write_text("def deliver(value):\n    return value\n")
        lines = ["from service import deliver", "def submit(blocked, urgent):", "    if blocked:", "        return"]
        lines.extend(["    # intervening implementation detail"] * 65)
        self.submit = len(lines) + 1
        lines.extend(['    deliver("express" if urgent else "regular")', "", "def retry(enabled):",
                      "    if enabled:", '        deliver("retry")', "", "def manual():",
                      '    return deliver("one"), deliver("two")'])
        self.retry, self.manual = len(lines) - 3, len(lines)
        (self.repo / "cli.py").write_text("\n".join(lines))
        self.graph = build_graph(self.repo)
        self.claim = {"text": "交付", "refs": [{"file": "service.py", "line": 1, "end": 2}]}
        self.node = next(n for n in self.graph["nodes"] if n["qualname"] == "deliver")
        self.item = {**self.claim, "nodeId": self.node["id"], "title": "交付", "aliases": []}

    def assert_sources(self, review):
        for row in review["actionFacts"]:
            for r in row["refs"]:
                blocks = [s for s in review["sources"] if s["file"] == r["file"] and
                          s["line"] <= r["line"] <= r["end"] <= s["end"]]
                self.assertTrue(blocks, r)
        for s in review["sources"]:
            original = self.graph["files"][s["file"]].splitlines()[s["line"] - 1:s["end"]]
            self.assertEqual(s["text"].splitlines(), [f"{i} | {text}" for i, text in enumerate(original, s["line"])])

    def test_one_batch_verifies_distinct_call_sites_in_its_existing_model_request(self):
        requests = []
        class Reader:
            provider, model = "local", "test"
            def complete(_, messages):
                data = json.loads(messages[-1]["content"])
                requests.append(data)
                if data["task"] == "explain":
                    return json.dumps({"overview": {"claims": [self.claim]}, "items": [self.item]})
                self.assertEqual(data["task"], "verify")
                return '{"claimReplacements":[],"itemReplacements":[]}'
        checkpoint = self.repo / "checkpoints"
        result = generate_semantics(self.graph, Reader(), checkpoint=checkpoint)
        self.assertEqual([p["task"] for p in requests], ["explain", "verify"])
        self.assertEqual(result["overview"]["claims"], [self.claim])
        verify = requests[1]
        self.assert_sources(verify)
        rows = verify["actionFacts"]
        self.assertEqual([r["action"]["line"] for r in rows], [self.submit, self.retry, self.manual, self.manual])
        self.assertEqual({r["target"] for r in rows}, {self.node["id"]})
        self.assertEqual({r["line"] for r in rows[0]["refs"]}, {3, 4, self.submit})
        self.assertIn("return", rows[0]["text"])
        self.assertIn("blocked", rows[0]["text"])
        self.assertIn("urgent 为真时取 'express'，为假时取 'regular'", rows[0]["text"])
        self.assertNotIn("enabled", rows[0]["text"])
        self.assertIn("enabled", rows[1]["text"])
        self.assertNotIn("urgent", rows[1]["text"])
        self.assertEqual(rows[2]["text"], "调用 deliver")
        self.assertLess(rows[2]["column"], rows[3]["column"])
        self.assertEqual(generate_semantics(self.graph, Reader(), checkpoint=checkpoint), result)
        self.assertEqual(len(requests), 2)

    def test_budget_charges_metadata_and_all_attached_evidence(self):
        for limit in [400, 900, 2500]:
            with self.subTest(limit=limit):
                review = review_material(self.graph, {"sources": []}, [self.claim], [], limit)
                self.assertLessEqual(len(canonical(review)), limit)
                self.assert_sources(review)
                if limit == 400:
                    self.assertEqual(review["actionFacts"], [])
                if limit == 2500:
                    self.assertTrue(any(row["action"]["line"] == self.submit for row in review["actionFacts"]))

    def test_assert_and_conditional_argument_start_review_but_words_inside_strings_do_not(self):
        cases = {
            "assertion": ("def submit(ready):\n    assert ready\n    return deliver('paid')", "AssertionError"),
            "choice": ("def submit(preview): return deliver('preview' if preview else 'paid')", "为假时取 'paid'"),
            "multiline": ("def submit(preview):\n    return deliver(\n        'preview'\n        if preview\n"
                          "        else 'paid'\n    )", "为假时取 'paid'"),
            "words": ('def submit():\n    message = """\n    if preview:\n    assert ready\n    """\n'
                      '    # if a message is needed\n    return deliver(message)', None),
        }
        for name, (function, expected) in cases.items():
            with self.subTest(name=name):
                root = self.repo / name
                root.mkdir()
                (root / "billing.py").write_text("def deliver(value): return value\n\n" + function + "\n")
                graph = build_graph(root)
                requests = []
                class Reader:
                    provider, model = "local", "test"
                    def complete(_, messages):
                        data = json.loads(messages[-1]["content"])
                        requests.append(data)
                        if data["task"] == "verify":
                            return '{"claimReplacements":[],"itemReplacements":[]}'
                        self.assertEqual(data["task"], "explain")
                        node = next(n for n in data["nodes"] if n["qualname"] == "submit")
                        refs = [{k: node[k] for k in ("file", "line", "end")}]
                        return json.dumps({"overview": {"claims": [{"text": "提交", "refs": refs}]},
                                           "items": [{"nodeId": node["id"], "text": "提交", "title": "提交",
                                                      "aliases": [], "refs": refs}]})
                checkpoint = self.repo / (name + "_cache")
                generate_semantics(graph, Reader(), checkpoint=checkpoint)
                self.assertEqual([p["task"] for p in requests], ["explain", "verify"] if expected else ["explain"])
                if expected:
                    fact, = requests[1]["actionFacts"]
                    self.assertIn(expected, fact["text"])
                    if name == "assertion":
                        self.assertIn("-O", fact["text"])
                        self.assertEqual({r["line"] for r in fact["refs"]}, {4, 5})
                        # A legacy one-line-guard checkpoint contains only its
                        # valid draft. Upgrade it without asking for that draft again.
                        batch_file, = checkpoint.glob("*.json")
                        saved = json.loads(batch_file.read_text(encoding="utf-8"))["batch"]
                        old = {"draft": saved["draft"]}
                        batch_file.write_text(canonical({"batch": old, "fingerprint": digest(old)}), encoding="utf-8")
                        requests.clear()
                        generate_semantics(graph, Reader(), checkpoint=checkpoint)
                        self.assertEqual([p["task"] for p in requests], ["verify"])
                        generate_semantics(graph, Reader(), checkpoint=checkpoint)
                        self.assertEqual(len(requests), 1)
                    if name == "multiline":
                        self.assertEqual({(r["line"], r["end"]) for r in fact["refs"]},
                                         {(4, 8), (5, 5), (6, 6), (7, 7)})

    def test_old_batch_replays_citations_within_its_original_source_budget(self):
        source = {"file": "service.py", "line": 1, "end": 2, "text": "1 | def deliver(value):\n2 |     return value"}
        material = {"sources": [source], "nodes": [self.node], "relations": []}
        limit = 1200
        legacy = review_material(self.graph, material, [self.claim], [self.item], limit, legacy=True)
        fresh = review_material(self.graph, material, [self.claim], [self.item], limit)
        def caller_lines(review):
            return {n for s in review["sources"] if s["file"] == "cli.py" for n in range(s["line"], s["end"] + 1)}
        old_only = caller_lines(legacy) - caller_lines(fresh)
        self.assertTrue(old_only)
        line = max(old_only)
        claim = {"text": "旧版已核对", "refs": [{"file": "cli.py", "line": line, "end": line}]}
        record = {"draft": {"overview": {"claims": [self.claim]}, "items": [self.item]},
                  "review": {"claimReplacements": [{"id": 1, "claims": [claim]}], "itemReplacements": []}}
        path = self.repo / (digest(material) + ".json")
        path.write_text(canonical({"fingerprint": digest(record), "batch": record}), encoding="utf-8")
        phases = []
        with patch("how2trust.semantics.BATCH_CHARS", limit):
            result, _ = _explain_batch(None, self.graph, material, _node_anchors(self.graph), self.repo, phases.append)
        self.assertEqual(phases, ["cached"])
        self.assertEqual(result, [claim])
