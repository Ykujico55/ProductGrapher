import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from how2trust.project import build_graph, canonical, digest
from how2trust.semantic_store import SemanticStore
from how2trust.semantics import _batches, generate_semantics, validate_semantics


class Reader:
    provider = "test supplier"
    model = "reader-test"

    def __init__(self):
        self.requests = []

    def complete(self, messages):
        data = json.loads(messages[-1]["content"])
        self.requests.append(data)
        if data["task"] == "overview":
            return json.dumps({"claimIds": [data["claims"][-1]["id"]]})
        block = data["sources"][0]
        ref = {key: block[key] for key in ("file", "line", "end")}
        overview = {"claims": [{"text": "本批可读取订单状态。", "refs": [ref]}]}
        items = []
        for node in data["nodes"]:
            if node["kind"] == "function":
                items.append({"nodeId": node["id"], "title": "发货状态", "aliases": ["部分发货"],
                              "text": "返回订单发货状态。", "refs": [{**ref, "file": node["file"],
                                  "line": node["line"], "end": node["line"]}]})
        return "```json\n" + json.dumps({"overview": overview, "items": items}) + "\n```"


class SemanticTests(unittest.TestCase):
    def graph(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "shipping.py").write_text("def ship():\n    return 'partial'\n", encoding="utf-8")
            (root / "status.py").write_text("def status():\n    return 'shipped'\n", encoding="utf-8")
            (root / "__init__.py").write_text("", encoding="utf-8")
            return build_graph(root)

    def test_round_trip_preserves_static_graph_and_configured_provider(self):
        graph, client = self.graph(), Reader()
        frozen = canonical(graph)
        artifact = generate_semantics(graph, client, "支持部分发货")
        self.assertEqual(client.requests[0]["requirements"], "支持部分发货")
        self.assertEqual(artifact["model"], client.model)
        self.assertEqual(artifact["provider"], client.provider)
        self.assertEqual(artifact["coverage"], {"eligible": 4, "explained": 2})
        self.assertEqual(artifact["items"][0]["aliases"], ["部分发货"])
        self.assertEqual(validate_semantics(graph, json.loads(json.dumps(artifact))), artifact)
        self.assertEqual(canonical(graph), frozen)

    def test_large_input_visits_last_module_and_checks_per_batch_citations(self):
        graph, client = self.graph(), Reader()
        with patch("how2trust.semantics.BATCH_CHARS", 220):
            artifact = generate_semantics(graph, client)
        explains = [d for d in client.requests if d["task"] == "explain"]
        self.assertGreater(len(explains), 1)
        self.assertEqual(client.requests[-1]["task"], "overview")
        selected = client.requests[-1]["claims"][-1]
        self.assertEqual(artifact["overview"]["claims"], [{k: selected[k] for k in ("text", "refs")}])
        seen_lines = {(s["file"], line) for d in explains for s in d["sources"]
                      for line in range(s["line"], s["end"] + 1)}
        self.assertEqual(seen_lines, {(file, line) for file, source in graph["files"].items()
                                     for line in range(1, len(source.splitlines()) + 1)})
        self.assertEqual(validate_semantics(graph, artifact), artifact)
        original = client.complete
        def ungrounded_summary(messages):
            if json.loads(messages[-1]["content"])["task"] == "overview":
                return json.dumps({"claimIds": [999], "text": "新的产品能力。"})
            return original(messages)
        with patch("how2trust.semantics.BATCH_CHARS", 220), patch.object(client, "complete", ungrounded_summary):
            with self.assertRaisesRegex(ValueError, "有效结论编号"):
                generate_semantics(graph, client)

        def bad_reference(messages):
            answer = original(messages)
            data = json.loads(answer.split("\n")[1])
            data["overview"]["claims"][0]["refs"] = [{"file": "status.py", "line": 1, "end": 1}]
            return json.dumps(data)
        with patch("how2trust.semantics.BATCH_CHARS", 220), patch.object(client, "complete", bad_reference):
            with self.assertRaisesRegex(ValueError, "未提供的源码"):
                generate_semantics(graph, client)

    def test_overview_keeps_each_claim_paired_with_its_source(self):
        graph, client = self.graph(), Reader()
        a = {"file": "shipping.py", "line": 2, "end": 2}
        b = {"file": "status.py", "line": 2, "end": 2}
        claims = [{"text": "发货返回 partial。", "refs": [a]},
                  {"text": "状态查询返回 shipped。", "refs": [b]}]
        guide = generate_semantics(graph, client)
        reply = {"overview": {"claims": claims, "text": "额外未引证的断言。"}, "items": guide["items"]}
        with patch.object(client, "complete", return_value=json.dumps(reply)):
            guide = generate_semantics(graph, client)
        self.assertEqual(guide["overview"], {"claims": claims, "text": "发货返回 partial。 状态查询返回 shipped。", "refs": [a, b]})
        self.assertEqual(validate_semantics(graph, guide), guide)
        guide["overview"]["text"] += "额外未引证的断言。"
        guide.pop("fingerprint")
        guide["fingerprint"] = digest(guide)
        with self.assertRaisesRegex(ValueError, "逐条结论"):
            validate_semantics(graph, guide)
        reply["overview"]["claims"][1]["refs"] = []
        with patch.object(client, "complete", return_value=json.dumps(reply)):
            with self.assertRaisesRegex(ValueError, "缺少源码引用"):
                generate_semantics(graph, client)

    def test_verifier_cannot_attach_unrelated_source_or_unknown_claims(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            (repo / "app.py").write_text(
                "def submit():\n    return 1\n\ndef other():\n    return 2\n"
                "if __name__ == '__main__':\n    submit()\n")
            graph = build_graph(repo)
        client = Reader()
        initial = client.complete
        def run_with(claims, items):
            def respond(messages):
                if json.loads(messages[-1]["content"])["task"] == "verify":
                    return json.dumps({"claimReplacements": claims, "itemReplacements": items})
                return initial(messages)
            with patch.object(client, "complete", respond):
                return generate_semantics(graph, client)
        wrong_ref = {"file": "app.py", "line": 5, "end": 5}
        with self.assertRaisesRegex(ValueError, "该节点"):
            run_with([], [{"nodeId": "function:app.py:submit", "refs": [wrong_ref]}])
        with self.assertRaisesRegex(ValueError, "总览结论"):
            run_with([{"id": 12, "claims": []}], [])
        with self.assertRaisesRegex(ValueError, "未提供的源码"):
            run_with([{"id": 1, "claims": [{"text": "说明", "refs": [{**wrong_ref, "end": 99}]}]}], [])

    def test_rejects_wrong_snapshot_modified_contents_and_bad_anchor(self):
        graph = self.graph()
        artifact = generate_semantics(graph, Reader())
        with self.assertRaisesRegex(ValueError, "快照"):
            validate_semantics({**graph, "snapshot": "changed"}, artifact)
        broken = copy.deepcopy(artifact)
        broken["items"][0]["title"] = "changed"
        with self.assertRaisesRegex(ValueError, "指纹"):
            validate_semantics(graph, broken)
        broken["items"][0]["refs"][0]["end"] = 100
        del broken["fingerprint"]
        broken["fingerprint"] = digest(broken)
        with self.assertRaisesRegex(ValueError, "未提供的源码"):
            validate_semantics(graph, broken)
        client = Reader()
        with patch.object(client, "complete", return_value="No JSON here"):
            with self.assertRaisesRegex(ValueError, "JSON"):
                generate_semantics(graph, client)

    def test_unrelated_same_batch_function_cannot_be_the_only_node_citation(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / "desk.py").write_text("def ship():\n    return 'sent'\n\ndef status():\n    return 'ready'\n")
            graph = build_graph(repo)
        client = Reader()
        guide = generate_semantics(graph, client)
        self.assertEqual(len(client.requests), 1)
        wrong = {"file": "desk.py", "line": 5, "end": 5}
        item = next(i for i in guide["items"] if i["nodeId"].endswith(":ship"))
        item["refs"].append(wrong)

        def sign(artifact):
            artifact.pop("fingerprint", None)
            artifact["fingerprint"] = digest(artifact)
            return artifact

        # An extra reference is permitted while the declaration is also cited.
        self.assertEqual(validate_semantics(graph, sign(guide)), guide)
        item["refs"] = [wrong]
        answer = {"items": [item], "overview": guide["overview"]}
        with patch.object(client, "complete", return_value=json.dumps(answer)):
            with self.assertRaisesRegex(ValueError, "该节点"):
                generate_semantics(graph, client)
        with self.assertRaisesRegex(ValueError, "该节点"):
            validate_semantics(graph, sign(guide))
        guide["promptVersion"] = "2"
        self.assertEqual(validate_semantics(graph, sign(guide)), guide)

    def test_entry_can_cite_cross_file_handler_and_group_it_by_capability(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / "cli.py").write_text(
                "import argparse\nfrom warehouse import ship\n"
                "p = argparse.ArgumentParser()\ns = p.add_subparsers(dest='cmd')\n"
                "s.add_parser('ship')\na = p.parse_args()\nif a.cmd == 'ship':\n    ship()\n")
            (repo / "middle.py").write_text("# unrelated context padding\n" * 220)
            (repo / "warehouse.py").write_text("def ship():\n    return 'partial'\n")
            graph = build_graph(repo)
        client = Reader()
        implementation = {"file": "warehouse.py", "line": 1, "end": 2}

        def explain(messages):
            data = json.loads(messages[-1]["content"])
            if data["task"] == "overview":
                return json.dumps({"claimIds": [1]})
            if data["task"] == "verify":
                self.assertIn("return 'partial'", "\n".join(s["text"] for s in data["sources"]))
                first = data["draft"]["claims"][0]
                entry = next(n for n in data["nodes"] if n["kind"] == "entry")
                return json.dumps({"claimReplacements": [{"id": first["id"], "claims": [
                    {"text": "该入口跨模块调用发货。", "refs": [implementation]}]}],
                    "itemReplacements": [{"nodeId": entry["id"], "aliases": ["部分发货"], "refs": [implementation]}]})
            ref = {k: data["sources"][0][k] for k in ("file", "line", "end")}
            overview = {"claims": [{"text": "模块导读", "refs": [ref]}]}
            rows = [{"nodeId": n["id"], "title": "按仓发货", "aliases": ["发货"],
                     "capability": "  履约  ", "text": "发货入口将操作交给仓库模块。", "refs": [implementation]}
                    for n in data["nodes"] if n["kind"] == "entry" or n["qualname"] == "ship"]
            return json.dumps({"overview": overview, "items": rows})

        with patch("how2trust.semantics.BATCH_CHARS", 2200), patch.object(client, "complete", explain):
            batches = list(_batches(graph))
            self.assertGreater(len(batches), 1)
            self.assertEqual(batches, list(_batches(graph)))
            self.assertLessEqual(max(len(canonical(batch)) for batch in batches), 2200)
            entry_batch = next(b for b in batches if any(n["kind"] == "entry" for n in b["nodes"]))
            self.assertFalse(any(n["file"] == "warehouse.py" for n in entry_batch["nodes"]))
            self.assertIn("2 |     return 'partial'", "\n".join(s["text"] for s in entry_batch["sources"]))
            guide = generate_semantics(graph, client)
        self.assertEqual(len(guide["items"]), 2)
        self.assertIn("该入口跨模块调用发货。", guide["overview"]["text"])
        self.assertIn("部分发货", [alias for item in guide["items"] for alias in item["aliases"]])
        self.assertEqual([g["label"] for g in guide["groups"]], ["履约"])
        self.assertEqual({c["nodeIds"][0] for c in guide["groups"][0]["children"]},
                         {item["nodeId"] for item in guide["items"]})
        self.assertEqual(validate_semantics(graph, guide), guide)
        broken = copy.deepcopy(guide)
        broken["groups"][0]["children"].pop()
        del broken["fingerprint"]
        broken["fingerprint"] = digest(broken)
        with self.assertRaisesRegex(ValueError, "分组"):
            validate_semantics(graph, broken)

    def test_generation_requires_matching_profile_while_replay_accepts_legacy(self):
        client = Reader()
        client.url, client.protocol, client.max_tokens = "http://localhost:9100", "openai", 4000
        graph = self.graph()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "guide.json"
            def store(requirements):
                return SemanticStore(copy.deepcopy(graph), client, requirements, root, path)
            events = []
            def progress(stage, **fields):
                events.append((stage, fields))
            first = store("original").generate(progress=progress)
            self.assertEqual(events[0], ("prepare", {}))
            self.assertEqual(events[-1], ("saving", {}))
            events.clear()
            self.assertEqual(store("original").generate(progress=progress), first)
            self.assertEqual(events, [("prepare", {}), ("prepare", {"phase": "cached"})])
            calls = len(client.requests)
            client.url = "http://localhost:9200"
            with self.assertRaisesRegex(ValueError, "配置"):
                store("original").generate()
            self.assertEqual(len(client.requests), calls)
            legacy = {k: v for k, v in first.items() if k not in (
                "fingerprint", "promptVersion", "requirementsFingerprint", "generationFingerprint")}
            legacy["fingerprint"] = digest(legacy)
            path.write_text(json.dumps(legacy), encoding="utf-8")
            self.assertEqual(store("").load(), legacy)
            with self.assertRaisesRegex(ValueError, "重放"):
                store("new request").generate()


    def test_shared_paths_are_verified_against_real_call_sites_on_import(self):
        from how2trust.capabilities import shared_calls

        root = Path(__file__).resolve().parents[1]
        # Import this frozen guide against the exact graph it describes. Newer
        # analyzers can resolve more calls and therefore have a different snapshot.
        html = (root / "examples/fulfillment-architecture.html").read_text(encoding="utf-8")
        graph, _ = json.JSONDecoder().raw_decode(html.split("window.H2T_GRAPH=", 1)[1])
        guide = json.loads((root / "examples/fulfillment-guide.json").read_bytes())
        guide["promptVersion"] = "4"
        guide["overview"]["claims"] = [{key: guide["overview"][key] for key in ("text", "refs")}]
        guide["sharedCalls"] = shared_calls(graph, guide["items"])
        guide.pop("fingerprint")
        guide["fingerprint"] = digest(guide)
        self.assertEqual(validate_semantics(graph, guide), guide)
        first, second = guide["sharedCalls"][0]["paths"][:2]
        first["edges"] = second["edges"]
        guide.pop("fingerprint")
        guide["fingerprint"] = digest(guide)
        with self.assertRaisesRegex(ValueError, "共享调用路径"):
            validate_semantics(graph, guide)


if __name__ == "__main__":
    unittest.main()
