"""A callee's local draft can outlive changes to evidence in its callers."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from how2trust.project import build_graph
from how2trust.semantic_evidence import needs_batch_review, review_material


class CallerReviewTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.repo = Path(temp.name)
        (self.repo / "billing.py").write_text("def charge(value):\n    return value\n", encoding="utf-8")

    def graph_and_draft(self, caller, offered=()):
        (self.repo / "orders.py").write_text(caller, encoding="utf-8")
        graph = build_graph(self.repo)
        charge = next(n for n in graph["nodes"] if n["qualname"] == "charge")
        sources = []
        for file, start, end in [("billing.py", 1, 2), *offered]:
            lines = graph["files"][file].splitlines()
            sources.append({"file": file, "line": start, "end": end,
                            "text": "\n".join(f"{i} | {lines[i - 1]}" for i in range(start, end + 1))})
        draft = {"nodes": [charge], "relations": [], "sources": sources}
        item = {"nodeId": charge["id"], "text": "收款", "refs": [{"file": "billing.py", "line": 1, "end": 2}]}
        return graph, draft, item

    def test_long_distance_caller_guard_changes_review_while_initial_material_stays_identical(self):
        def caller(guard):
            return ("from billing import charge\ndef checkout(ready, order):\n"
                    f"    if {guard}:\n        return\n" + "    # processing\n" * 60 + "    charge(order)\n")
        reviews, drafts = [], []
        for guard in ("ready", "not ready"):
            graph, draft, item = self.graph_and_draft(caller(guard), [("orders.py", 65, 65)])
            self.assertTrue(needs_batch_review(graph, draft))
            drafts.append(draft)
            review = review_material(graph, draft, [], [item], 4200)
            fact, = review["actionFacts"]
            self.assertEqual({(r["file"], r["line"]) for r in fact["refs"]},
                             {("orders.py", 3), ("orders.py", 4), ("orders.py", 65)})
            self.assertIn(guard, fact["text"])
            reviews.append(review)
        self.assertEqual(drafts[0], drafts[1])
        self.assertNotEqual(reviews[0], reviews[1])

    def test_single_unseen_caller_and_its_removal(self):
        source = "from billing import charge\ndef checkout():\n    return charge(\n        'paid',\n    )\n"
        graph, draft, item = self.graph_and_draft(source)
        self.assertTrue(needs_batch_review(graph, draft))
        review = review_material(graph, draft, [], [item], 2000)
        fact, = review["actionFacts"]
        self.assertEqual(fact["refs"], [{"file": "orders.py", "line": 3, "end": 5}])
        self.assertIn("'paid'", "\n".join(s["text"] for s in review["sources"]))
        legacy = review_material(graph, draft, [], [item], 2000, incoming_evidence=False)
        self.assertEqual(legacy["actionFacts"], [])

        graph, same_draft, _ = self.graph_and_draft(source.replace("charge(", "str("))
        self.assertEqual(draft, same_draft)
        self.assertFalse(needs_batch_review(graph, same_draft))

    def test_complete_caller_evidence_and_unrelated_helper_need_no_review(self):
        caller = ("from billing import charge\ndef checkout():\n    return charge('paid')\n"
                  "def helper(ready):\n    if ready:\n        return\n")
        graph, draft, _ = self.graph_and_draft(caller, [("orders.py", 1, 3)])
        self.assertFalse(needs_batch_review(graph, draft))
