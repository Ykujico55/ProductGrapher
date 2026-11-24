import tempfile
import unittest
from pathlib import Path

from how2trust.capabilities import capability_groups
from how2trust.collaboration import collaboration_graph
from how2trust.project import build_graph, canonical


class CollaborationTests(unittest.TestCase):
    def setUp(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "store.py").write_text("def persist(value=None):\n    return value\n", encoding="utf8")
            (root / "flow.py").write_text(
                "import sys\nfrom store import persist\n\n"
                "def run(allow, replay, rows):\n"
                "    if not allow:\n        sys.exit(1)\n"
                "    if replay:\n        return persist('old')\n"
                "    for row in rows:\n"
                "        if row is None:\n            continue\n"
                "        persist(row) if allow else persist(0)\n"
                "    persist('done')\n", encoding="utf8")
            tests = root / "tests"
            tests.mkdir()
            (tests / "test_x.py").write_text("from store import persist\npersist()\n", encoding="utf8")
            self.graph = build_graph(root)

    def test_calls_retain_branch_direction_and_original_edges(self):
        view = collaboration_graph(self.graph)
        self.assertEqual(view, collaboration_graph(self.graph))
        self.assertEqual([m["label"] for m in view["modules"]], ["flow.py", "store.py"])
        self.assertEqual(len(view["relations"]), 1)
        sites = view["relations"][0]["sites"]
        self.assertEqual([s["line"] for s in sites], [8, 12, 12, 13])
        mapping = {e["id"]: e for e in self.graph["edges"]}
        for s in sites:
            self.assertEqual(mapping[s["edgeId"]]["source"], s["caller"])
            self.assertEqual(mapping[s["edgeId"]]["target"], s["callee"])
            self.assertEqual(s["file"], mapping[s["edgeId"]]["file"])
            self.assertTrue(all(1 <= f["line"] <= f["end"] <= 13 for f in s["conditions"]))
        at = [[f["text"] for f in s["conditions"]] for s in sites]
        self.assertIn("sys.exit", at[0][0])
        self.assertIn("判断为真：replay", at[0][-1])
        self.assertIn("判断为真：allow", at[1][-1])
        self.assertIn("判断为假：allow", at[2][-1])
        self.assertTrue(any("row in rows" in c for c in at[1]))
        self.assertTrue(any("continue" in c for c in at[1]))
        self.assertEqual(len(at[3]), 2)
        self.assertIn("return", at[3][-1])

    def test_business_projection_aggregates_ids_without_changing_source_graph(self):
        original = canonical(self.graph)
        items = []
        for node in self.graph["nodes"]:
            if node["kind"] == "module" and node["file"] in ("flow.py", "store.py"):
                items.append({"nodeId": node["id"], "capability": node["file"].split(".")[0].upper(),
                              "refs": [{k: node[k] for k in ("file", "line", "end")}]})
        groups = capability_groups(self.graph, items)
        guide = {"groups": groups, "items": items}
        built = collaboration_graph(self.graph, guide)
        modules = {m["id"]: m for m in built["modules"]}
        self.assertEqual(sorted(m["label"] for m in modules.values()), ["FLOW", "STORE"])
        link, = built["relations"]
        self.assertEqual([modules[link[k]]["label"] for k in ("source", "target")], ["FLOW", "STORE"])
        self.assertEqual(len(link["sites"]), 4)
        self.assertEqual(canonical(self.graph), original)

    def test_return_in_with_still_affects_later_calls_but_suppressed_raise_may_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "target.py").write_text("def save(): pass\n", encoding="utf8")
            (root / "caller.py").write_text(
                "from target import save\n"
                "def run(ctx, nested, fail):\n"
                "    with ctx:\n"
                "        if nested:\n            return\n"
                "        if fail:\n            raise RuntimeError()\n"
                "    save()\n", encoding="utf8")
            view = collaboration_graph(build_graph(root))
            site, = view["relations"][0]["sites"]
            self.assertEqual(len(site["conditions"]), 1)
            guard, = site["conditions"]
            self.assertIn("return：nested", guard["text"])
            self.assertEqual((guard["line"], guard["end"]), (4, 5))

    def test_entry_binding_is_distinct_from_a_method_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "cli.py").write_text("import click\n@click.command()\ndef start(): pass\n", encoding="utf8")
            graph = build_graph(root)
            items = [{"nodeId": n["id"], "capability": n["kind"],
                      "refs": [{k: n[k] for k in ("file", "line", "end")}]}
                     for n in graph["nodes"] if n["kind"] in {"entry", "function"}]
            view = collaboration_graph(graph, {"groups": capability_groups(graph, items)})
            relation, = view["relations"]
            site, = relation["sites"]
            self.assertEqual((site["kind"], site["line"], site["conditions"]), ("handles", 2, []))
            self.assertEqual(site["entryPaths"], [{"entry": site["caller"], "edges": [site["edgeId"]]}])

    def test_assertions_only_reach_subsequent_calls_on_the_same_path(self):
        source = """def trace(label): pass
def run(ready, other, test):
    trace('before')
    if other:
        assert test
        trace('conditional')
    else:
        trace('sibling')
    trace('after branch')
    assert (
        ready
    ), 'missing state'
    trace('after check')
"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "flow.py").write_text(source, encoding="utf8")
            view = build_graph(root)["collaboration"]
        checks = {site["line"]: [c for c in site["conditions"] if c.get("kind") == "assertion"]
                  for site in view["internalSites"]}
        self.assertEqual({line: [c["line"] for c in conditions] for line, conditions in checks.items()},
                         {3: [], 6: [5], 8: [], 9: [], 13: [10]})
        check, = checks[13]
        self.assertEqual((check["file"], check["line"], check["end"]), ("flow.py", 10, 12))
        self.assertEqual(check["predicate"], {"file": "flow.py", "line": 11, "end": 11, "text": "ready",
                                            "truth": True})
        self.assertIn("若为假则抛出 AssertionError", check["text"])
        self.assertIn("-O 下跳过此检查", check["text"])

    def test_order_retry_call_sites_have_independent_entry_paths_and_rejections(self):
        root = Path(__file__).resolve().parents[1] / "audit/terra-fulfillment/project"
        graph = build_graph(root)
        view = graph["collaboration"]
        nodes = {n["id"]: n for n in graph["nodes"]}
        edges = {e["id"]: e for e in graph["edges"]}
        internal = {s["edgeId"] for s in view["internalSites"]}
        self.assertFalse(internal.intersection(s["edgeId"] for r in view["relations"] for s in r["sites"]))
        by_entry = {}
        for site in view["internalSites"]:
            if nodes[site["callee"]]["qualname"] != "Desk.retry":
                continue
            path, = site["entryPaths"]
            start, end = path["edges"]
            self.assertEqual(edges[start]["source"], path["entry"])
            self.assertEqual(edges[start]["target"], site["caller"])
            self.assertEqual(end, site["edgeId"])
            by_entry[nodes[path["entry"]]["label"]] = site
        self.assertEqual({k: s["line"] for k, s in by_entry.items()}, {"stock": 101, "cancel": 157, "return": 186})
        self.assertEqual(by_entry["stock"]["conditions"], [])
        cancel, = by_entry["cancel"]["conditions"]
        self.assertIn("cannot be cancelled", cancel["exit"]["text"])
        prior, sellable = by_entry["return"]["conditions"]
        self.assertEqual(prior["kind"], "priorExit")
        self.assertTrue(prior["predicate"]["truth"])
        self.assertEqual((prior["predicate"]["line"], prior["predicate"]["end"], prior["exit"]["line"]), (168, 170, 172))
        self.assertIn("return exceeds shipped quantity", prior["exit"]["text"])
        self.assertEqual((sellable["kind"], sellable["truth"], sellable["line"]), ("predicate", True, 185))

    def test_argument_choice_is_distinct_from_call_conditions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "sink.py").write_text("def save(value): pass\n", encoding="utf8")
            (root / "flow.py").write_text(
                "from sink import save\n"
                "def run(pending):\n"
                "    save(('partial' if pending else 'shipped',))\n"
                "    save(lambda: 'a' if pending else 'b')\n"
                "    save(save('retry' if pending else 'done'))\n", encoding="utf8")
            view = collaboration_graph(build_graph(root))
        sites = view["relations"][0]["sites"]
        self.assertEqual(len(sites), 4)
        self.assertTrue(all(s["conditions"] == [] and s["entryPaths"] == [] for s in sites))
        choice, = sites[0]["valueChoices"]
        self.assertEqual([choice[k]["text"] for k in ("test", "whenTrue", "whenFalse")],
                         ["pending", "'partial'", "'shipped'"])
        self.assertEqual((sites[1]["valueChoices"], sites[2]["valueChoices"]), ([], []))
        self.assertEqual(sites[3]["valueChoices"][0]["whenFalse"]["text"], "'done'")

    def test_registration_allows_two_call_steps_from_the_bound_handler(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "flow.py").write_text(
                "import click\n"
                "def flush(): pass\n"
                "def save(): flush()\n"
                "def stage(): save()\n"
                "@click.command()\n"
                "def send(active):\n"
                "    if active:\n        stage()\n"
                "@click.command()\n"
                "def receive(blocked):\n"
                "    if blocked:\n        return\n"
                "    stage()\n", encoding="utf8")
            graph = build_graph(root)
        edges = {edge["id"]: edge for edge in graph["edges"]}
        calls = {s["line"]: s for s in graph["collaboration"]["internalSites"] if s["kind"] == "calls"}
        routes = []
        for path in calls[4]["entryPaths"]:
            evidence = [edges[edge_id] for edge_id in path["edges"]]
            self.assertEqual([e["kind"] for e in evidence], ["handles", "calls", "calls"])
            self.assertEqual(evidence[0]["source"], path["entry"])
            self.assertEqual(evidence[-1]["id"], calls[4]["edgeId"])
            routes.append([e["line"] for e in evidence])
        self.assertCountEqual(routes, [[5, 8, 4], [9, 13, 4]])
        self.assertEqual([c["kind"] for c in calls[8]["conditions"]], ["predicate"])
        self.assertEqual([c["kind"] for c in calls[13]["conditions"]], ["priorExit"])
        self.assertEqual(calls[4]["conditions"], [])
        self.assertEqual(calls[3]["entryPaths"], [])

    def test_prior_judgment_and_nested_exits_do_not_assert_current_state(self):
        src = """from target import save
def exit(): pass
def run(flag, rows, nested):
    if flag:
        flag = False
        save('modified')
    if flag:
        exit()
    if nested:
        if flag:
            return
    try:
        if flag:
            raise KeyError()
    except KeyError:
        save('caught')
    for row in rows:
        for item in row:
            if not item:
                break
        save('outer')
    save('end')
"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "target.py").write_text("def save(tag): pass\n", encoding="utf8")
            (root / "caller.py").write_text(src, encoding="utf8")
            view = collaboration_graph(build_graph(root))
            sites = {src.splitlines()[s["line"] - 1].strip(): [c["text"] for c in s["conditions"]]
                     for link in view["relations"] for s in link["sites"]}
            self.assertEqual(sites["save('modified')"], ["在第 4 行判断为真：flag"])
            self.assertEqual(sites["save('caught')"], ["进入异常处理：KeyError"])
            self.assertEqual(sites["save('outer')"], ["循环每次取得一项：row in rows"])
            self.assertEqual(sites["save('end')"], [])


if __name__ == "__main__":
    unittest.main()
