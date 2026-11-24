import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from how2trust.local_business import LocalSelectionError, VERSION, generate_local_business, local_material
from how2trust.project import build_graph, canonical, ref
from how2trust.semantic_cache import SemanticRequestCache


class Reader:
    provider, model = "fixture", "local"

    def __init__(self):
        self.requests = []

    def complete(self, messages):
        p = json.loads(messages[-1]["content"])
        self.requests.append(p)
        groups = []
        for node in p["nodes"]:
            block = next(b for b in p["sources"] if b["file"] == node["file"]
                         and node["line"] <= b["end"] and b["line"] <= node["end"])
            line = max(block["line"], node["line"])
            groups.append({"label": node["label"], "text": node["qualname"], "nodeIds": [node["id"]],
                           "refs": [{"file": node["file"], "line": line, "end": line}]})
        return json.dumps({"groups": groups})


class LocalBusinessTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.cache = root / "cache"
        (root / "orders").mkdir()
        (root / "orders/shop.py").write_text(
            "from stock import reserve\n"
            "class Desk:\n"
            "    def place(self, order):\n"
            "        if order.paid:\n"
            "            return reserve(order)\n\n"
            "    def report(self): return 'UNRELATED_METHOD'\n\n"
            "def export(): return 'UNRELATED_FILE_LEVEL'\n", encoding="utf-8")
        (root / "stock.py").write_text("from warehouse import save\ndef reserve(order):\n    return save(order)\n"
                                      "def audit(): return 'UNRELATED_STOCK_IMPL'\n", encoding="utf-8")
        (root / "warehouse.py").write_text("def save(order): return 'SECOND_HOP'\n", encoding="utf-8")
        (root / "README.md").write_text("UNRELATED_README", encoding="utf-8")
        self.graph = build_graph(root)
        self.place = "method:orders/shop.py:Desk.place"
        self.reserve = "function:stock.py:reserve"
        self.reader = Reader()

    def test_exact_method_and_optional_neighbors_offer_only_the_promised_code(self):
        original = canonical(self.graph)
        first = generate_local_business(self.graph, self.reader, [self.place])
        self.assertEqual(first["selectedNodeIds"], [self.place])
        self.assertEqual(first["neighborNodeIds"], [])
        self.assertEqual(first["relations"], [])
        self.assertEqual(first["sourceRanges"], [{"file": "orders/shop.py", "line": 3, "end": 5}])
        with_neighbors = generate_local_business(self.graph, self.reader, [self.place], True)
        self.assertEqual(with_neighbors["neighborNodeIds"], [self.reserve])
        self.assertNotEqual(first["id"], with_neighbors["id"])
        edge = next(e for e in self.graph["edges"] if e["source"] == self.place and e["target"] == self.reserve)
        link, = with_neighbors["relations"]
        self.assertEqual(link["edgeIds"], [edge["id"]])
        self.assertEqual(link["refs"], [ref(edge)])
        sent = canonical(self.reader.requests[-1])
        self.assertIn("return save(order)", sent)
        self.assertIn("order.paid", sent)
        for marker in ["UNRELATED_", "SECOND_HOP", "warehouse.py", "nodeReadings"]:
            self.assertNotIn(marker, sent)
        self.assertEqual(canonical(self.graph), original)

        # Reading an incoming neighbor offers the guard and invocation, without
        # all the other code in its file or even the entire caller function.
        _, incoming = local_material(self.graph, [self.reserve], True)
        shop, = [s for s in incoming["sources"] if s["file"] == "orders/shop.py"]
        self.assertEqual((shop["line"], shop["end"]), (4, 5))
        self.assertNotIn("UNRELATED_", canonical(incoming))

    def test_classes_and_directory_tree_can_be_selected_from_the_static_graph(self):
        _, cls = local_material(self.graph, ["class:orders/shop.py:Desk"])
        self.assertIn("UNRELATED_METHOD", canonical(cls))
        self.assertNotIn("UNRELATED_FILE_LEVEL", canonical(cls))
        source = next(b for b in self.graph["sourceTree"]["children"] if b["label"] == "源码模块")
        folder = next(b for b in source["children"] if b["label"] == "orders")
        view = generate_local_business(self.graph, self.reader, [folder["id"]])
        self.assertEqual(view["title"], "orders")
        self.assertEqual(view["targets"], [folder["id"]])
        self.assertEqual({r["file"] for r in view["sourceRanges"]}, {"orders/shop.py"})
        self.assertIn("UNRELATED_FILE_LEVEL", canonical(self.reader.requests[-1]))
        self.assertNotIn("UNRELATED_STOCK_IMPL", canonical(self.reader.requests[-1]))

    def test_decorators_follow_their_own_declaration_in_a_partial_module(self):
        root = self.cache.parent
        (root / "billing.py").write_text(
            "@require_paid\ndef ship(): return 1\n\n@track('OTHER_DECORATOR')\ndef log(): return 2\n",
            encoding="utf8")
        graph = build_graph(root)
        ship = "function:billing.py:ship"
        _, strict = local_material(graph, [ship])
        self.assertIn("@require_paid", canonical(strict))
        self.assertNotIn("OTHER_DECORATOR", canonical(strict))
        key = "collab:billing-part"
        graph["collaboration"]["modules"].append({"id": key, "label": "发货", "nodeIds": ["module:billing.py", ship]})
        _, part = local_material(graph, [key])
        self.assertNotIn("OTHER_DECORATOR", canonical(part))
        _, whole = local_material(graph, ["module:billing.py"])
        self.assertIn("OTHER_DECORATOR", canonical(whole))

    def test_empty_unknown_root_and_oversized_selection_send_zero_requests(self):
        for targets in ([], ["node:bad"], [self.graph["tree"]["id"]], [self.place, 1]):
            with self.subTest(targets=targets), self.assertRaises(LocalSelectionError):
                generate_local_business(self.graph, self.reader, targets)
        with patch("how2trust.local_business.BATCH_CHARS", 100), self.assertRaisesRegex(LocalSelectionError, "缩小"):
            generate_local_business(self.graph, self.reader, [self.place], True)
        self.assertEqual(self.reader.requests, [])

    def test_false_refs_and_foreign_members_and_fabricated_edges_are_rejected(self):
        g = {"label": "订单", "text": "写单", "nodeIds": [self.place],
             "refs": [{"file": "orders/shop.py", "line": 4, "end": 4}]}
        invalid = [
            {"groups": [{**g, "refs": [{"file": "orders/shop.py", "line": 7, "end": 7}]}]},
            {"groups": [{**g, "refs": [{"file": "stock.py", "line": 2, "end": 2}]}]},
            {"groups": [{**g, "nodeIds": [self.reserve]}]},
            {"groups": [g, g]},
            {"groups": [g], "relations": [{"source": "a", "target": "b"}]},
        ]
        for reply in invalid:
            with self.subTest(reply=reply), patch.object(self.reader, "complete", return_value=json.dumps(reply)), self.assertRaises(ValueError):
                generate_local_business(self.graph, self.reader, [self.place])

    def test_unchanged_local_request_reuses_model_reply_on_a_new_snapshot(self):
        cache = SemanticRequestCache(self.cache, self.cache.parent, self.reader, "", VERSION)
        before = generate_local_business(self.graph, self.reader, [self.place], cache=cache)
        (self.cache.parent / "warehouse.py").write_text("def save(order): return order\n", encoding="utf-8")
        graph = build_graph(self.cache.parent)
        after = generate_local_business(graph, self.reader, [self.place, self.place], cache=cache)
        self.assertEqual(len(self.reader.requests), 1)
        self.assertNotEqual(before["snapshot"], after["snapshot"])
        self.assertEqual(before["groups"], after["groups"])
