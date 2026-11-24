from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from how2trust.business_groups import group_decisions, validate_group_evidence
from how2trust.capabilities import capability_groups
from how2trust.project import build_graph, digest
from how2trust.semantic_store import SemanticStore
from how2trust.semantics import PROMPT_VERSION, finalize_capabilities, generate_semantics, generation_key, validate_semantics


class DomainsTests(unittest.TestCase):
    def setUp(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            (repo / "cli.py").write_text(
                "import argparse\np = argparse.ArgumentParser()\ns = p.add_subparsers(dest='cmd')\n"
                "s.add_parser('buy')\ns.add_parser('cancel')\ns.add_parser('query')\n"
                "def store():\n    pass\n")
            self.graph = build_graph(repo)

    def draft(self, grouped=False):
        class LocalReader:
            provider = "mock"
            model = "test"

            def complete(self, messages):
                data = json.loads(messages[-1]["content"])
                if data["task"] == "verify":
                    return json.dumps({"claimReplacements": [], "itemReplacements": []})
                self.items = []
                for node in data["nodes"]:
                    if node["kind"] not in {"entry", "function"}:
                        continue
                    label = node["label"]
                    capability = "orders" if grouped and label in {"buy", "cancel"} else label
                    ref = {key: node[key] for key in ("file", "line", "end")}
                    self.items.append({"nodeId": node["id"], "text": label, "title": label,
                                       "capability": capability, "aliases": [], "refs": [ref]})
                return json.dumps({"overview": {"claims": [{"text": "cli", "refs": [ref]}]}, "items": self.items})

        self.reader = LocalReader()
        with patch("how2trust.semantics.finalize_capabilities", side_effect=lambda graph, guide, *args, **kwargs: guide):
            return generate_semantics(self.graph, self.reader)

    def test_rearranges_members_only_and_keeps_provenance(self):
        draft = self.draft()
        def arrange(messages):
            body = json.loads(messages[-1]["content"])
            self.assertEqual(set(body), {"task", "groups", "shared", "reservedNames"})
            self.assertEqual(body["reservedNames"], ["store"])
            self.assertEqual([g["entry"] for g in body["groups"]], ["buy", "cancel", "query"])
            ids = [g["members"][0]["nodeId"] for g in body["groups"][:2]]
            return json.dumps({"domains": [{"name": "Orders", "groupIds": [1, 2],
                "reason": {"text": "两个公开命令分别购买与取消。", "nodeIds": ids}},
                {"name": "Lookup", "groupIds": [3]}]})
        with patch.object(self.reader, "complete", side_effect=arrange) as profile:
            result = finalize_capabilities(self.graph, draft, self.reader)
        profile.assert_called_once()
        self.assertEqual(validate_semantics(self.graph, result), result)
        self.assertEqual(result["sourceGuide"]["fingerprint"], draft["fingerprint"])
        self.assertEqual(result["overview"], draft["overview"])
        self.assertEqual(result["coverage"], draft["coverage"])
        self.assertEqual([g["label"] for g in result["groups"]], ["Lookup", "Orders", "store"])
        evidence = result["groupEvidence"]["Orders"]
        self.assertEqual([origin["label"] for origin in evidence["origins"]], ["buy", "cancel"])
        self.assertEqual([r["line"] for r in evidence["reason"]["refs"]], [4, 5])
        altered = deepcopy(result)
        altered["groupEvidence"]["Orders"]["reason"]["refs"][0]["line"] += 1
        altered.pop("fingerprint")
        altered["fingerprint"] = digest(altered)
        with self.assertRaisesRegex(ValueError, "源码依据"):
            validate_semantics(self.graph, altered)
        for before, after in zip(draft["items"], result["items"]):
            self.assertEqual({k: v for k, v in before.items() if k != "capability"},
                             {k: v for k, v in after.items() if k != "capability"})
        for name, group_ids in [("Orders", [1, 2]), ("Orders", [1, 2, 20]), ("Orders", [1, 2, 2, 3]),
                                ("store", [1, 2, 3])]:
            with self.subTest(name=name, ids=group_ids), patch.object(self.reader, "complete", return_value=json.dumps({
                "domains": [{"name": name, "groupIds": group_ids}]})), self.assertRaises(ValueError):
                finalize_capabilities(self.graph, draft, self.reader)

    def test_skips_existing_multientry_group(self):
        draft = self.draft(grouped=True)
        with patch.object(self.reader, "complete") as profile:
            result = finalize_capabilities(self.graph, draft, self.reader)
        profile.assert_not_called()
        self.assertEqual(result["groups"], draft["groups"])

    def test_shared_calls_are_counterevidence_and_owned_actions_can_support_a_merge(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "stock.py").write_text(
                "def audit(qty): return qty\n"
                "def online(stock, qty):\n    stock.available -= qty\n    audit(qty)\n"
                "def manual(stock, qty):\n    audit(qty)\n    stock.available -= qty\n"
                "def forward(qty): return audit(qty)\n", encoding="utf8")
            (root / "test_store.py").write_text(
                "from stock import audit\ndef check(): audit(1)\n", encoding="utf8")
            graph = build_graph(root)
        nodes = {node["label"]: node for node in graph["nodes"] if node["kind"] == "function" and node["file"] == "stock.py"}
        items = [{"nodeId": node["id"], "capability": name, "title": name, "text": name,
                  "refs": [{k: node[k] for k in ("file", "line", "end")}]} for name, node in nodes.items()]

        def propose(domain_names, proof_names):
            def reply(client, payload):
                groups = {g["name"]: g for g in payload["groups"]}
                self.assertNotIn("proof", groups["forward"]["members"][0])
                self.assertIn("stock.available -=", groups["manual"]["members"][0]["proof"]["code"])
                separate = [{"name": name, "groupIds": [group["id"]]} for name, group in groups.items()
                            if name not in domain_names]
                return {"domains": [*separate, {"name": "库存", "groupIds": [groups[n]["id"] for n in domain_names],
                    "reason": {"text": "扣除可用库存。", "nodeIds": [nodes[n]["id"] for n in proof_names]}}]}
            return group_decisions(graph, items, None, reply)

        for proposed, refs in [(["forward", "audit"], ["forward", "audit"]),
                               (["online", "manual"], ["audit"])]:
            with self.subTest(proposed=proposed, refs=refs):
                separate, why = propose(proposed, refs)
                self.assertEqual(separate, items)
                self.assertEqual({r["target"] for r in why["audit"]["sharedTargets"]}, {nodes["audit"]["id"]})
                edges = {e["id"]: e for e in graph["edges"]}
                sharing = [edges[k] for k in why["online"]["sharedTargets"][0]["edgeIds"]]
                self.assertEqual(len({e["source"] for e in sharing}), 2)
                self.assertTrue(all(e["target"] == nodes["audit"]["id"] and e["file"] == "stock.py" for e in sharing))
                self.assertEqual(edges[why["forward"]["boundaryCalls"][0]]["line"], 8)
        merged, why = propose(["online", "manual"], ["online", "manual"])
        self.assertEqual({i["capability"] for i in merged}, {"库存", "audit", "forward"})
        self.assertEqual(sorted(r["line"] for r in why["库存"]["reason"]["refs"]), [3, 7])
        validate_group_evidence(graph, merged, why)
        why["库存"]["sharedTargets"][0]["edgeIds"].append("invented")
        with self.assertRaisesRegex(ValueError, "共享调用"):
            validate_group_evidence(graph, merged, why)

    def test_v12_offline_replay_and_explicit_regeneration_from_verified_batches(self):
        self.draft()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "frozen.json"
            store = SemanticStore(self.graph, self.reader, "", Path(tmp), path)
            with patch("how2trust.semantics.finalize_capabilities", side_effect=lambda graph, draft, *a, **kw: draft):
                draft = generate_semantics(self.graph, self.reader, checkpoint=store.checkpoint)
            old = {**draft, "promptVersion": "12", "generationFingerprint": generation_key(self.graph, self.reader, "", "12")}
            old["items"] = [{**i, "capability": "旧组"} for i in old["items"]]
            old["groups"] = capability_groups(self.graph, old["items"])
            old.pop("fingerprint")
            old["fingerprint"] = digest(old)
            path.write_text(json.dumps(old), encoding="utf8")
            with patch.object(self.reader, "complete") as reader:
                self.assertEqual(store.load(), old)
                reader.assert_not_called()
                def domains_only(messages):
                    body = json.loads(messages[-1]["content"])
                    self.assertEqual(body["task"], "domains")
                    self.assertEqual({g["name"] for g in body["groups"]}, {"buy", "cancel", "query"})
                    return json.dumps({"domains": [{"name": g["name"], "groupIds": [g["id"]]} for g in body["groups"]]})
                reader.side_effect = domains_only
                fresh = store.generate()
            reader.assert_called_once()
            self.assertEqual(fresh["promptVersion"], PROMPT_VERSION)
            self.assertEqual(set(fresh["groupEvidence"]), {"buy", "cancel", "query", "store"})
