"""Regressions for a whole repository with a single public entry and test fixtures."""
from pathlib import Path
import json
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen

from how2trust.business_groups import merge_groups
from how2trust.discussion import Discussion
from how2trust.llm import ModelClient
from how2trust.project import build_graph
from how2trust.semantic_store import SemanticStore
from how2trust.source_roles import source_role
from how2trust.web import make_server


class BusinessWorkbenchTests(unittest.TestCase):
    def test_generate_action_updates_live_graph_and_export(self):
        def reply(messages):
            payload = json.loads(messages[-1]["content"])
            if payload["task"] == "domains":
                return json.dumps({"domains": [{"name": g["name"], "groupIds": [g["id"]]} for g in payload["groups"]]})
            if payload["task"] == "verify":
                return '{"claimReplacements":[],"itemReplacements":[]}'
            functions = [n for n in payload["nodes"] if n["kind"] == "function"]
            rows = [{"nodeId": n["id"], "title": n["label"], "text": n["label"], "capability": n["label"],
                     "aliases": [], "refs": [{k: n[k] for k in ("file", "line", "end")}]} for n in functions]
            return json.dumps({"items": rows, "overview": {"claims": [{"text": "Job", "refs": rows[0]["refs"]}]}})

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "job.py").write_text("from worker import save\ndef submit(ready):\n    if ready: save()\n")
            (root / "worker.py").write_text("def save(): pass\n")
            (root / "test_job.py").write_text("def test_job(): pass\n")
            graph = build_graph(root)
            model = ModelClient({"base_url": "http://localhost", "model": "fixture"})
            store = SemanticStore(graph, model, "", root / "cache")
            discussion = Discussion(root / "state.db", graph)
            with patch.object(model, "complete", side_effect=reply), make_server(graph, discussion, 0, semantics=store) as server:
                worker = threading.Thread(target=server.serve_forever, daemon=True)
                worker.start()
                base = f"http://127.0.0.1:{server.server_port}"
                try:
                    req = Request(base + "/api/semantics", data=b'{"includeCollaboration":true}',
                                  headers={"Content-Type": "application/json"})
                    with urlopen(req) as response:
                        result = json.load(response)
                    with urlopen(base + "/api/graph") as response:
                        live = json.load(response)
                    self.assertEqual(live["semantics"], result["semantics"])
                    self.assertEqual(live["collaboration"], result["collaboration"])
                    self.assertEqual(live["semantics"]["coverage"], {"eligible": 4, "explained": 2})
                    site = live["collaboration"]["relations"][0]["sites"][0]
                    self.assertEqual((site["file"], site["conditions"][0]["line"]), ("job.py", 3))
                    with urlopen(base + "/export.html") as response:
                        exported = response.read().decode("utf8").split("window.H2T_GRAPH=", 1)[1]
                    self.assertEqual(json.JSONDecoder().raw_decode(exported)[0], live)
                finally:
                    server.shutdown()
                    worker.join()

    def test_json_response_format_is_scoped_to_supported_providers_and_calls(self):
        messages = [{"role": "user", "content": "JSON"}]
        for url, options, expected in [
            ("https://api.deepseek.com", {}, True),
            ("http://localhost:11434/v1", {}, False),
            ("http://localhost:11434/v1", {"json_mode": True}, True),
        ]:
            client = ModelClient({"base_url": url, "model": "test", **options})
            with self.subTest(url=url, options=options), patch.object(client, "_request") as send:
                client.complete_json(messages)
                send.assert_called_with(messages, json_output=expected)
                client.complete(messages)
                send.assert_called_with(messages, json_output=False)

    def test_test_and_example_entries_stay_in_their_own_sections(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for path in ("cli.py", "tests/fixtures/helper.py", "examples/run.py"):
                dest = root / path
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text("def run(): pass\nif __name__ == '__main__': run()\n", encoding="utf8")
            graph = build_graph(root)
        children = graph["sourceTree"]["children"]
        def entries(branch):
            if branch["kind"] == "entry":
                yield branch["nodeIds"][0]
            for child in branch["children"]:
                yield from entries(child)
        sections = {c["label"]: set(entries(c)) for c in children}
        self.assertTrue(all("tests/fixtures/" in k for k in sections["测试与夹具"]))
        self.assertTrue(all("examples/run.py" in k for k in sections["示例与文档"]))
        self.assertEqual([len(sections[s]) for s in ("测试与夹具", "示例与文档")], [1, 1])
        self.assertEqual(len(list(entries(graph["sourceTree"]))), 3)
        self.assertEqual(graph["fileRoles"]["cli.py"], "production")
        self.assertEqual(source_role("tools/testimony.py"), "production")
        self.assertEqual(source_role("app/test_routes.py"), "test")

    def test_internal_responsibilities_merge_without_multiple_entry_groups(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "work.py").write_text("def read(): pass\ndef restore(): read()\ndef run(): restore()\n")
            graph = build_graph(root)
        names = {"read": "读取存档", "restore": "存档读取", "run": "作业编排"}
        items = [{"nodeId": n["id"], "capability": names[n["label"]], "text": n["label"], "title": n["label"]}
                 for n in graph["nodes"] if n["kind"] == "function"]
        def group(_client, data):
            self.assertTrue(data["shared"])
            ids = {row["name"]: row["id"] for row in data["groups"]}
            return {"domains": [{"name": "作业编排", "groupIds": [ids["作业编排"]]},
                                {"name": "存档读取", "groupIds": [ids["读取存档"], ids["存档读取"]]}]}
        grouped = merge_groups(graph, items, None, group)
        self.assertEqual([i["nodeId"] for i in grouped], [i["nodeId"] for i in items])
        self.assertEqual({i["capability"] for i in grouped}, {"作业编排", "存档读取"})
