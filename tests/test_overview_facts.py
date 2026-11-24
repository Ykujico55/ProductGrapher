import tempfile
import unittest
from pathlib import Path

from how2trust.overview_facts import overview_facts
from how2trust.project import build_graph


class OverviewFactsTests(unittest.TestCase):
    def test_filter_conditional_call_and_recursive_return_in_cited_function(self):
        source = """def ask(changes): pass
def finish(): pass
def run(args, previous, replay):
    if replay:
        new = {k:v for k,v in args.items() if k not in previous}
    def nested():
        return [x for x in args if x > 0]
    if args['nested']:
        return run(args['nested'], previous, replay)
    if args['changes']:
        ask(args['changes'])
    finish()
"""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "sample.py").write_text(source, "utf8")
            graph = build_graph(root)
        ref = {"file": "sample.py", "line": 10, "end": 11}
        guide = {"overview": {"claims": [{"refs": [ref, ref]}, {"refs": [{**ref, "line": 2, "end": 2}]}]}}
        first, second = overview_facts(graph, guide)
        self.assertEqual(second, [])
        self.assertEqual([f["line"] for f in first], [4, 8, 10])
        self.assertIn("判断为真：replay", first[0]["text"])
        self.assertIn("k 不在 previous 中", first[0]["text"])
        self.assertIn("递归调用 run，直接返回其结果", first[1]["text"])
        self.assertIn("args['changes']（下标取值成功后，判断取出值的真假）", first[2]["text"])
        self.assertIn("调用 ask", first[2]["text"])
        self.assertNotIn("uncoveredEvidence", first[0])
        self.assertNotIn("uncoveredEvidence", first[1])
        self.assertEqual([r["line"] for r in first[2]["uncoveredEvidence"]], [8, 9])
        self.assertFalse(any("finish" in f["text"] or "x > 0" in f["text"] for f in first))
        self.assertEqual([first, second], overview_facts(graph, guide))

    def test_returning_a_different_function_is_not_recursion(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "actual.py").write_text("def run(): return 123\n", encoding="utf8")
            (root / "entry.py").write_text("import actual\ndef run(flag):\n    if flag:\n        return actual.run()\n",
                                          encoding="utf8")
            graph = build_graph(root)
        guide = {"overview": {"claims": [{"refs": [{"file": "entry.py", "line": 2, "end": 4}]}]}}
        rows, = overview_facts(graph, guide)
        self.assertEqual(len(rows), 1)
        self.assertIn("调用 actual.run，直接返回其结果", rows[0]["text"])
        self.assertNotIn("递归", rows[0]["text"])

    def test_citing_a_call_retains_earlier_rejection_and_separate_value_evidence(self):
        source = """def save(value): pass
def run(ready, damaged):
    if ready:
        pass
    else:
        raise ValueError('not ready')
    column = 'damaged' if damaged else 'available'
    save(
        'damaged'
        if damaged
        else 'available'
    )
"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "orders.py").write_text(source, encoding="utf8")
            graph = build_graph(root)
        guide = {"overview": {"claims": [{"refs": [{"file": "orders.py", "line": n, "end": n}]}
                                           for n in (8, 7)]}}
        (call,), (assignment,) = overview_facts(graph, guide)
        guard, = call["conditions"]
        self.assertEqual((guard["kind"], guard["predicate"]["truth"]), ("priorExit", False))
        self.assertEqual((guard["predicate"]["line"], guard["exit"]["line"], call["action"]["line"]), (3, 6, 8))
        self.assertIn("raise ValueError('not ready')", call["text"])
        choice, = call["valueChoices"]
        self.assertEqual([choice[k]["line"] for k in ("test", "whenTrue", "whenFalse")], [10, 9, 11])
        self.assertEqual(assignment["action"]["text"], "赋值给 column")
        self.assertEqual(assignment["valueChoices"][0]["whenFalse"]["text"], "'available'")
        self.assertEqual([(r["role"], r["line"]) for r in call["uncoveredEvidence"]],
                         [("condition", 3), ("exit", 6), ("value", 9), ("value", 10), ("value", 11)])
        self.assertEqual([r["line"] for r in assignment["uncoveredEvidence"]], [3, 6])

    def test_real_order_retry_reports_only_gaps_in_this_claims_citations(self):
        root = Path(__file__).resolve().parents[1] / "audit/terra-fulfillment/project"
        graph = build_graph(root)
        path = "dispatchdesk/store.py"
        citation_sets = [
            [(186, 186)],
            [(185, 186), (168, 168), (169, 170), (172, 172)],
            [(186, 186), (169, 169), (172, 172), (185, 185)],
        ]
        guide = {"overview": {"claims": [
            {"refs": [{"file": path, "line": start, "end": end} for start, end in spans]}
            for spans in citation_sets
        ]}}
        narrow, complete, partial = [next(row for row in rows if row["action"]["line"] == 186)
                                     for rows in overview_facts(graph, guide)]
        self.assertEqual([(r["role"], r["file"], r["line"], r["end"]) for r in narrow["uncoveredEvidence"]],
                         [("condition", path, 168, 170), ("exit", path, 172, 172), ("condition", path, 185, 185)])
        self.assertIn("return exceeds shipped quantity", narrow["uncoveredEvidence"][1]["text"])
        self.assertNotIn("uncoveredEvidence", complete)
        self.assertEqual([(r["line"], r["end"]) for r in partial["uncoveredEvidence"]], [(168, 168), (170, 170)])

    def test_same_line_proofs_share_one_link_and_other_files_do_not_cover_them(self):
        graph = {"files": {"flow.py": """def run(ready, damaged):
    if not ready: return
    ui.emit(
        'damaged' if damaged else 'available'
    )
""", "other.py": "a = 1\nb = 2\nc = 3\nd = 4\n"}, "edges": []}
        guide = {"overview": {"claims": [{"refs": [{"file": "flow.py", "line": 3, "end": 3},
                                                   {"file": "other.py", "line": 2, "end": 4}]}]}}
        (call,), = overview_facts(graph, guide)
        branch, choice = call["uncoveredEvidence"]
        self.assertEqual((branch["file"], branch["line"], branch["end"]), ("flow.py", 2, 2))
        self.assertIn("not ready", branch["text"])
        self.assertIn("return", branch["text"])
        self.assertEqual((choice["role"], choice["line"], choice["end"]), ("value", 4, 4))
        self.assertIn("为真时取 'damaged'", choice["text"])
        self.assertIn("为假时取 'available'", choice["text"])

    def test_legacy_citation_to_unrendered_template_can_remain_empty(self):
        graph = {"files": {"hook.py": "{% if project.enabled %}\nrun()\n{% endif %}"}, "edges": []}
        ref = {"file": "hook.py", "line": 1, "end": 2}
        self.assertEqual(overview_facts(graph, {"overview": {"claims": [{"refs": [ref]}]}}), [[]])
        self.assertEqual(overview_facts(graph, None), [])

    def test_cited_action_links_to_a_preceding_assertion_independently(self):
        graph = {"files": {"flow.py": """def run(data):
    ui.emit("before")
    assert data is not None
    ui.emit(data)
"""}, "edges": []}
        guide = {"overview": {"claims": [{"refs": [{"file": "flow.py", "line": n, "end": n}]}
                                         for n in (2, 4)]}}
        before, (after,) = overview_facts(graph, guide)
        self.assertEqual(before, [])
        self.assertEqual((after["line"], after["end"], after["action"]["line"]), (4, 4, 4))
        check, = after["conditions"]
        self.assertEqual((check["kind"], check["predicate"]["line"], check["predicate"]["text"]),
                         ("assertion", 3, "data is not None"))
        self.assertIn(check["text"], after["text"])
        gap, = after["uncoveredEvidence"]
        self.assertEqual((gap["role"], gap["file"], gap["line"], gap["end"]), ("condition", "flow.py", 3, 3))
        self.assertIn("-O", gap["text"])

    def test_cited_branches_can_use_external_calls_without_creating_edges(self):
        graph = {"files": {"ui.py": """def show(value, mode):
    if mode == 'json':
        ui.echo(json.dumps(value))
    else:
        if value:
            ui.echo(value)
        else:
            exit(1)
"""}, "edges": []}
        ref = {"file": "ui.py", "line": 2, "end": 8}
        rows, = overview_facts(graph, {"overview": {"claims": [{"refs": [ref]}]}})
        self.assertEqual([row["end"] for row in rows], [3, 6, 8])
        self.assertIn("判断为真：mode == 'json'", rows[0]["text"])
        self.assertIn("ui.echo(json.dumps(value))", rows[0]["text"])
        self.assertIn("判断为假：value", rows[2]["text"])
        self.assertIn("exit(1)", rows[2]["text"])
        self.assertEqual(graph["edges"], [])
