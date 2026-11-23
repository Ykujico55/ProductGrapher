import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from how2trust.project import build_graph
from how2trust.semantics import finalize_capabilities, generate_semantics, validate_semantics


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
            return json.dumps({"domains": [{"name": "Orders", "groupIds": [1, 2]}, {"name": "Lookup", "groupIds": [3]}]})
        with patch.object(self.reader, "complete", side_effect=arrange) as profile:
            result = finalize_capabilities(self.graph, draft, self.reader)
        profile.assert_called_once()
        self.assertEqual(validate_semantics(self.graph, result), result)
        self.assertEqual(result["sourceGuide"]["fingerprint"], draft["fingerprint"])
        self.assertEqual(result["overview"], draft["overview"])
        self.assertEqual(result["coverage"], draft["coverage"])
        self.assertEqual([g["label"] for g in result["groups"]], ["Lookup", "Orders", "store"])
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
