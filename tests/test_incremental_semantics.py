import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from how2trust.project import build_graph
from how2trust.semantic_store import SemanticStore
from how2trust.semantics import validate_semantics


CALLER = "orders/api.py"
HANDLER = "shipping/service.py"
UNRELATED = "reports/totals.py"


class RecordingReader:
    provider = "local-fixture"
    model = "reading-v1"
    url = "http://localhost:9991"

    def __init__(self):
        self.requests = []

    def complete(self, messages):
        request = json.loads(messages[-1]["content"])
        self.requests.append(request)
        task = request["task"]
        if task == "overview":
            return json.dumps({"claimIds": [claim["id"] for claim in request["claims"]]})
        if task == "review_overview":
            return json.dumps({"results": request["claims"]})
        if task == "domains":
            return json.dumps({"domains": [{"name": g["name"], "groupIds": [g["id"]]}
                                           for g in request["groups"]]})

        # Express what this request actually shows about the handler, with a
        # citation to the numbered caller line. This makes stale text observable.
        caller_lines = [line.split(" | ", 1) for block in request["sources"] if block["file"] == CALLER
                        for line in block["text"].splitlines()]
        guards = [(int(line), code.strip()) for line, code in caller_lines if code.lstrip().startswith("if ")]
        items = []
        for node in request["nodes"]:
            if node["kind"] != "function":
                continue
            ref = {"file": node["file"], "line": node["line"], "end": node["line"]}
            item = {"nodeId": node["id"], "title": node["qualname"], "capability": node["qualname"],
                    "aliases": [], "text": node["qualname"], "refs": [ref]}
            if node["file"] == HANDLER and guards:
                line, guard = guards[0]
                item["text"] = guard
                item["refs"].append({"file": CALLER, "line": line, "end": line})
            items.append(item)
        if task == "verify":
            return json.dumps({"claimReplacements": [], "itemReplacements": [
                {key: item[key] for key in ("nodeId", "text", "refs")} for item in items]})
        if task != "explain":
            raise AssertionError(f"Unexpected task {task}")
        return json.dumps({"items": items, "overview": {"claims": [
            {"text": item["text"], "refs": item["refs"]} for item in items]}})


class IncrementalSemanticsTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.repo = Path(temp.name) / "project"
        self.cache = Path(temp.name) / "cache"
        self.client = RecordingReader()
        self.write(CALLER, "from shipping.service import ship\n"
                           "def place(order):\n"
                           "    if order.ready:\n"
                           "        return ship(order)\n")
        self.write(HANDLER, "def ship(order):\n    return order\n")
        self.write(UNRELATED, "def total(rows):\n    return len(rows)\n")
        self.write("tests/test_order.py", "def test_ready(): pass\n")

    def write(self, file, source):
        path = self.repo / file
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")

    def generate(self):
        self.client.requests.clear()
        graph = build_graph(self.repo)
        guide = SemanticStore(graph, self.client, "", self.cache).generate()
        self.assertEqual(validate_semantics(graph, guide), guide)
        self.assertEqual(guide["snapshot"], graph["snapshot"])
        return guide

    def requested_files(self):
        return {node["file"] for request in self.client.requests if request["task"] in {"explain", "verify"}
                for node in request["nodes"] if node["kind"] == "function"}

    def handler(self, guide):
        return next(item for item in guide["items"] if item["nodeId"].endswith(":ship"))

    def test_tests_only_change_reuses_all_model_stages_but_emits_new_snapshot(self):
        before = self.generate()
        self.assertEqual({r["task"] for r in self.client.requests},
                         {"explain", "verify", "overview", "domains", "review_overview"})
        self.write("tests/test_order.py", "def test_ready(): pass\n# new test comment\n")
        after = self.generate()
        self.assertNotEqual(after["snapshot"], before["snapshot"])
        self.assertNotEqual(after["generationFingerprint"], before["generationFingerprint"])
        self.assertEqual(after["items"], before["items"])
        self.assertEqual(self.client.requests, [])

    def test_changed_caller_updates_trigger_and_line_evidence_on_unchanged_handler(self):
        first = self.generate()
        self.assertEqual(self.handler(first)["text"], "if order.ready:")
        self.write(CALLER, "from shipping.service import ship\n\n"
                           "def place(order):\n"
                           "    if order.paid:\n"
                           "        return ship(order)\n")
        changed = self.generate()
        self.assertEqual(self.requested_files(), {CALLER, HANDLER})
        handler = self.handler(changed)
        self.assertEqual(handler["text"], "if order.paid:")
        self.assertEqual([ref for ref in handler["refs"] if ref["file"] == CALLER],
                         [{"file": CALLER, "line": 4, "end": 4}])

        self.write(CALLER, "def place(order):\n    return order\n")
        removed = self.generate()
        self.assertEqual(self.requested_files(), {CALLER, HANDLER})
        self.assertEqual(self.handler(removed)["text"], "ship")
        self.assertFalse(any(ref["file"] == CALLER for ref in self.handler(removed)["refs"]))

    def test_model_endpoint_change_cannot_reuse_another_profile(self):
        self.generate()
        expected = {r["task"] for r in self.client.requests}
        self.client.url = "http://localhost:9992"
        self.generate()
        self.assertEqual(self.requested_files(), {CALLER, HANDLER, UNRELATED})
        self.assertEqual({r["task"] for r in self.client.requests}, expected)

    def test_callee_reuses_initial_reading_but_rechecks_a_distant_caller_guard(self):
        # The caller fits in one batch. Its excerpts in the callee's initial
        # reading show the invocation and the start of place(), but skip its guard.
        padding = "    # " + "intermediate processing step " * 4 + "\n"
        def caller(field):
            return ("from shipping.service import ship\ndef place(order):\n" + padding * 160 +
                    f"    if order.{field}:\n        return\n" + padding * 40 + "    return ship(order)\n")

        def handler_requests():
            return [r for r in self.client.requests if any(n["file"] == HANDLER for n in r.get("nodes", []))]

        self.write(CALLER, caller("ready"))
        before = self.generate()
        draft, review = handler_requests()
        self.assertEqual([draft["task"], review["task"]], ["explain", "verify"])
        self.assertNotIn("order.ready", "\n".join(b["text"] for b in draft["sources"]))
        self.assertEqual(self.handler(before)["text"], "if order.ready:")

        self.write(CALLER, caller("paid"))
        after = self.generate()
        refreshed, = handler_requests()
        self.assertEqual(refreshed["task"], "verify")
        self.assertEqual(refreshed["draft"], review["draft"])
        self.assertTrue(any("order.paid" in f["text"] for f in refreshed["actionFacts"]))
        self.assertEqual(self.handler(after)["text"], "if order.paid:")
        ref, = [r for r in self.handler(after)["refs"] if r["file"] == CALLER]
        self.assertEqual(caller("paid").splitlines()[ref["line"] - 1].strip(), "if order.paid:")


if __name__ == "__main__":
    unittest.main()
