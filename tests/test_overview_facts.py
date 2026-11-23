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
        self.assertIn("空字典或空列表不会进入此分支", first[2]["text"])
        self.assertIn("调用 ask", first[2]["text"])
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

    def test_legacy_citation_to_unrendered_template_can_remain_empty(self):
        graph = {"files": {"hook.py": "{% if project.enabled %}\nrun()\n{% endif %}"}, "edges": []}
        ref = {"file": "hook.py", "line": 1, "end": 2}
        self.assertEqual(overview_facts(graph, {"overview": {"claims": [{"refs": [ref]}]}}), [[]])
        self.assertEqual(overview_facts(graph, None), [])

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
