import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from how2trust.overview_review import review_context
from how2trust.project import build_graph
from how2trust.semantics import generate_semantics, upgrade_overview


class Reader:
    provider = "local"
    model = "example"

    def __init__(self):
        self.requests = []

    def complete(self, messages):
        p = json.loads(messages[-1]["content"])
        self.requests.append(p)
        if p["task"] == "review_overview":
            result = []
            for c in p["claims"]:
                ref = c["refs"][0]
                if ref["file"] == "forms.py" and "validationFeedback" not in p:
                    ref = {**ref, "end": 40000}
                result.append({"id": c["id"], "text": "已核查 " + c["text"], "refs": [ref]})
            return json.dumps({"results": result})
        if p["task"] == "verify":
            return '{"claimReplacements":[],"itemReplacements":[]}'
        if p["task"] == "overview":
            return json.dumps({"claimIds": [c["id"] for c in p["claims"]]})
        self.assert_task(p)
        rows = []
        for n in p["nodes"]:
            if n["kind"] != "function":
                continue
            ref = {"file": n["file"], "line": n["line"], "end": n["line"]}
            rows.append({"nodeId": n["id"], "text": n["qualname"], "title": n["label"],
                         "refs": [ref], "aliases": []})
        return json.dumps({"overview": {"claims": [{"text": r["text"], "refs": r["refs"]} for r in rows]}, "items": rows})

    @staticmethod
    def assert_task(p):
        if p["task"] != "explain":
            raise AssertionError(p["task"])


class OverviewReviewTests(unittest.TestCase):
    def test_multibatch_recheck_reuses_guide_and_checks_whole_function(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "forms.py").write_text("def collect(values):\n    if values:\n        return values[0]\n    return None\n")
            (root / "padding.py").write_text("# code documentation across independent source batches\n" * 80)
            (root / "service.py").write_text("from forms import collect\ndef submit(nested, values):\n"
                                            "    if nested:\n        return submit(False, [])\n    return collect(values)\n")
            graph = build_graph(root)
        reader = Reader()
        with patch("how2trust.semantics.BATCH_CHARS", 1800):
            with patch("how2trust.semantics.upgrade_overview", side_effect=lambda g, a, c, r, **kwargs: a):
                old = generate_semantics(graph, reader)
            reader.requests.clear()
            with self.assertRaisesRegex(ValueError, "配置"):
                upgrade_overview(graph, old, reader, requirements="different")
            self.assertFalse(reader.requests)
            stages = []
            guide = upgrade_overview(graph, old, reader, progress=stages.append)
            self.assertEqual(stages, ["review"])
            self.assertEqual({p["task"] for p in reader.requests}, {"review_overview"})
            self.assertTrue(any("validationFeedback" in p for p in reader.requests))
            self.assertEqual(guide["sourceOverview"]["fingerprint"], old["fingerprint"])
            self.assertEqual(guide["sourceOverview"]["overview"], old["overview"])
            self.assertTrue(guide["sourceOverview"]["reviewed"])
            self.assertEqual(guide["promptVersion"], "12")
            self.assertEqual(guide["items"], old["items"])
            self.assertEqual(len(guide["overview"]["claims"]), len(old["overview"]["claims"]))
            for p in reader.requests:
                source = "\n".join(b["text"] for b in p["sources"])
                self.assertIn("return values[0]", source)
                self.assertIn("return submit(False, [])", source)
                self.assertTrue(p["nodeReadings"])

    def test_only_cited_functions_carry_local_exceptions_and_evidence_into_overview(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "report.py").write_text(
                "@command()\ndef export(data, mode):\n    if mode == 'raw':\n        return data\n"
                "    return {k: v for k, v in data.items() if v is not None}\n"
                "def lookup(data, key):\n    value = data.get(key)\n    if value:\n        return value\n"
                "    return 'missing'\ndef other():\n    pass\n")
            graph = build_graph(root)
        def ref(line, end):
            return {"file": "report.py", "line": line, "end": end}
        items = {}
        for n in graph["nodes"]:
            if n["kind"] == "function":
                items[n["id"]] = {"text": n["label"], "refs": [ref(n["line"], n["end"])]}
        claims = [{"id": 1, "refs": [ref(1, 2), ref(6, 6)]}, {"id": 2, "refs": [ref(6, 6)]}]
        sources, readings = review_context(graph, claims, items, 8000)
        self.assertEqual({r["text"]: r["claimIds"] for r in readings}, {"export": [1], "lookup": [1, 2]})
        for reading in readings:
            for r in reading["refs"]:
                self.assertTrue(any(b["file"] == r["file"] and b["line"] <= r["line"] <= r["end"] <= b["end"]
                                    for b in sources))
        source = "\n".join(b["text"] for b in sources)
        self.assertIn("return data", source)
        self.assertIn("if v is not None", source)
        self.assertIn("if value:", source)

    def test_shared_handler_selects_only_cited_registration(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "api.py").write_text(
                "from fastapi import FastAPI\napp = FastAPI()\n"
                "@app.get('/a')\n@app.post('/b')\ndef shared():\n    return 1\n"
                "@app.get('/single')\ndef single():\n    return 2\n")
            graph = build_graph(root)
        items = {n["id"]: {"text": n["label"], "refs": [{"file": n["file"], "line": n["line"], "end": n["end"]}]}
                 for n in graph["nodes"] if n["kind"] == "entry"}
        claims = [{"id": i, "refs": [{"file": "api.py", "line": line, "end": line}]}
                  for i, line in enumerate([6, 4, 9], 1)]
        _, readings = review_context(graph, claims, items, 8000)
        self.assertEqual([(r["text"], r["claimIds"]) for r in sorted(readings, key=lambda v: v["text"])],
                         [("GET /single", [3]), ("POST /b", [2])])
