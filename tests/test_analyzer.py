import json
import tempfile
import textwrap
import unittest
from pathlib import Path

from how2trust.analyzer import analyze
from how2trust.project import build_graph


class AnalyzerTest(unittest.TestCase):
    def index(self, files):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for path, src in files.items():
                target = root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(textwrap.dedent(src).lstrip(), encoding="utf-8")
            return analyze(root)

    def relations(self, graph, kind):
        names = {n["id"]: n["qualname"] for n in graph["nodes"]}
        return {(names[e["source"]], names[e["target"]]) for e in graph["edges"] if e["kind"] == kind}

    def test_routes_classes_calls_and_evidence(self):
        graph = self.index({"src/shop/__init__.py": "", "src/shop/store.py": """
            class Base:
                pass
            class Store(Base):
                def save(self): pass
            class Service:
                def __init__(self):
                    self.store = Store()
                def cancel(self):
                    self.store.save()
        """, "src/shop/routes.py": """
            from fastapi import APIRouter as Router
            from .store import Service
            router = Router(prefix="/orders", tags=["Orders"])
            @router.post("/{id}/cancel")
            async def cancel(id):
                service = Service()
                service.cancel()
        """})
        entry, = [n for n in graph["nodes"] if n["kind"] == "entry"]
        self.assertEqual((entry["label"], entry["group"], entry["group_evidence"]["line"]),
                         ("POST /orders/{id}/cancel", "Orders", 3))
        self.assertEqual(self.relations(graph, "inherits"), {("Store", "Base")})
        self.assertEqual(self.relations(graph, "associates"), {("Service", "Store")})
        self.assertEqual(self.relations(graph, "calls"), {
            ("cancel", "Service"), ("cancel", "Service.cancel"), ("Service.cancel", "Store.save"),
            ("Service.__init__", "Store")})
        for e in graph["edges"]:
            lines = graph["files"][e["file"]].splitlines()
            self.assertTrue(1 <= e["line"] <= e["end"] <= len(lines))
            if e["kind"] == "calls":
                self.assertIn(e["expression"], lines[e["line"] - 1])

    def test_external_main_handler_and_click(self):
        graph = self.index({"app.py": """
            import click
            @click.group(name="admin")
            def cli(): pass
            @cli.command("report")
            def report(): pass
            def main(): pass
        """, "run.py": """
            from app import main
            if __name__ == '__main__':
                raise SystemExit(main())
        """})
        nodes = {n["label"]: n for n in graph["nodes"] if n["kind"] == "entry"}
        self.assertEqual(nodes["report"]["group"], "admin")
        self.assertEqual(nodes["run.py · __main__"]["file"], "run.py")
        for e in graph["edges"]:
            if e["source"] == nodes["run.py · __main__"]["id"]:
                self.assertEqual((e["file"], e["line"], e["target"]), ("run.py", 3, "function:app.py:main"))

    def test_local_framework_and_ambiguous_bindings(self):
        graph = self.index({"fastapi/__init__.py": "", "app.py": """
            from fastapi import APIRouter
            router = APIRouter()
            @router.get("/")
            def index(): pass
            def save(): pass
            def action(save):
                save()
            def conditional(flag):
                if flag:
                    fn = save
                fn()
            class One:
                def work(self): pass
            one = One()
            one.work = lambda: 5
            def patch():
                one.work()
            def wrapper(fn): return lambda: None
            @wrapper
            def decorated(): pass
            def user():
                decorated()
        """})
        self.assertFalse(any(n["kind"] == "entry" for n in graph["nodes"]))
        self.assertEqual(self.relations(graph, "calls"), {("app", "One")})
        callers = {d.get("caller", "").split(":")[-1] for d in graph["diagnostics"]}
        self.assertTrue({"action", "conditional", "patch", "user"} <= callers)

    def test_relative_imports_deterministic_and_syntax_errors_visible(self):
        files = {"__init__.py": "from . import lib", "lib.py": "def action(): pass",
                 "service.py": "from . import lib\ndef run():\n    lib.action()",
                 "broken.py": "def (", "venv/third_party.py": "raise RuntimeError()",
                 ".git/dead.py": "print('skip')"}
        first = self.index(files)
        second = self.index(dict(reversed(list(files.items()))))
        self.assertEqual(json.dumps(first), json.dumps(second))
        self.assertEqual(self.relations(first, "calls"), {("run", "action")})
        self.assertEqual({d["file"] for d in first["diagnostics"]}, {"broken.py"})
        self.assertEqual(set(first["files"]), {"__init__.py", "lib.py", "service.py", "broken.py"})

    def test_module_literally_named_src(self):
        graph = self.index({"src.py": "def run(): pass", "cli.py": "from src import run\nrun()"})
        self.assertEqual(self.relations(graph, "calls"), {("cli", "run")})

    def test_factory_returns_and_inherited_methods(self):
        graph = self.index({"base.py": """
            class Base:
                def save(self): pass
        """, "app.py": """
            from base import Base as Parent
            class Child(Parent): pass
            class Override(Child):
                def save(self): pass
            class Factory:
                def __new__(cls): return Child()
                def save(self): pass
            class DerivedFactory(Factory): pass
            class Unknown:
                def __new__(cls, target): return target()
                def save(self): pass
            class FieldBase:
                def __init__(self, replacement): self.save = replacement
            class FieldChild(FieldBase):
                def save(self): pass
            class Holder:
                def __init__(self): self.child = Factory()
                def store(self): self.child.save()
            def run(target):
                alias = DerivedFactory()
                alias.save()
                Child().save()
                Override().save()
                Unknown(target).save()
                FieldChild(target).save()
        """})
        self.assertEqual({(e["expression"], e["target"].rsplit(":", 1)[1])
                          for e in graph["edges"] if e.get("expression", "").endswith(".save")}, {
                              ("alias.save", "Base.save"), ("Child().save", "Base.save"),
                              ("Override().save", "Override.save"), ("self.child.save", "Base.save")})
        self.assertEqual(self.relations(graph, "associates"), {("Holder", "Child")})
        self.assertTrue(any("Unknown(target).save" in d["message"] for d in graph["diagnostics"]))
        self.assertTrue(any("FieldChild(target).save" in d["message"] for d in graph["diagnostics"]))

    def test_star_import_respects_order_at_call_and_in_function(self):
        graph = self.index({"symbols.py": "def old(): pass", "app.py": """
            def old(): pass
            old()
            from symbols import *
            old()
            def fresh(): pass
            def run():
                old()
                fresh()
        """})
        self.assertEqual({(e["line"], e["target"]) for e in graph["edges"] if e["kind"] == "calls"},
                         {(8, "function:app.py:fresh"), (2, "function:app.py:old")})
        self.assertEqual({d["line"] for d in graph["diagnostics"]}, {4, 7})

    def test_argparse_command_dispatch_has_registration_and_call_evidence(self):
        graph = self.index({"handlers.py": "def outside(): pass", "app.py": """
            from argparse import ArgumentParser as Parser
            def callback(args): pass
            def main(other):
                parser = Parser(prog="tool")
                sub = parser.add_subparsers(dest="cmd")
                bound = sub.add_parser("bound")
                bound.set_defaults(run=callback)
                sub.add_parser("branch")
                sub.add_parser("unresolved")
                other.add_parser("unrelated")
                args = parser.parse_args()
                if args.cmd == "branch":
                    from handlers import outside
                    return outside()
                return args.run(args)
        """})
        entries = {n["label"]: n for n in graph["nodes"] if n["kind"] == "entry"}
        self.assertEqual(set(entries), {"bound", "branch", "unresolved"})
        self.assertEqual((entries["bound"]["line"], entries["bound"]["group_evidence"]["line"]), (6, 4))
        self.assertEqual({e["line"] for e in entries["bound"]["evidence"]}, {5, 15})
        self.assertEqual({e["line"] for e in entries["branch"]["evidence"]}, {5, 12})
        edges = [(e["kind"], e["target"], e["line"]) for e in graph["edges"]
                 if e["source"] in {n["id"] for n in entries.values()}]
        self.assertCountEqual(edges, [("handles", "function:app.py:callback", 7),
                                      ("calls", "function:handlers.py:outside", 14)])
        self.assertIn("未解析", entries["unresolved"]["basis"])

    def test_try_assignment_only_reaches_following_statements_of_that_suite(self):
        graph = self.index({"app.py": """
            class Desk:
                def save(self): pass
                def close(self): pass
            def safe():
                try:
                    desk = Desk()
                    try:
                        desk.save()
                    finally:
                        desk.close()
                except ValueError:
                    desk.save()
                finally:
                    desk.close()
                desk.save()
            def rebound(flag):
                try:
                    desk = Desk()
                    if flag: desk = object()
                    desk.save()
                finally: pass
            def sibling(flag):
                try:
                    if flag:
                        desk = Desk()
                    else:
                        desk.save()
                finally: pass
        """})
        method_calls = [(e["line"], e["expression"]) for e in graph["edges"]
                        if e.get("expression", "").startswith("desk.")]
        self.assertEqual(sorted(method_calls), [(8, "desk.save"), (10, "desk.close")])

    def test_argparse_final_else_requires_a_closed_command_set(self):
        source = """
            import argparse
            def ship(args): pass
            def report(args): pass
            def main(hook):
                parser = argparse.ArgumentParser()
                subs = parser.add_subparsers(dest="cmd", required=True)
                subs.add_parser("ship")
                subs.add_parser("report")
                args = parser.parse_args()
                if args.cmd == "ship":
                    ship(args)
                else:
                    report(args)
        """
        def default_edges(src):
            graph = self.index({"cli.py": src})
            entry = next(n for n in graph["nodes"] if n["kind"] == "entry" and n["label"] == "report")
            return [edge for edge in graph["edges"] if edge["source"] == entry["id"]]

        edge, = default_edges(source)
        self.assertEqual((edge["target"], edge["expression"], edge["line"]), ("function:cli.py:report", "report", 13))
        self.assertIn("else", edge["basis"])
        for old, new in (
            ("required=True", "required=False"),
            ('subs.add_parser("ship")', 'subs.add_parser("ship", aliases=["send"])'),
            ('subs.add_parser("ship")', 'subs.add_parser("ship"); subs.add_parser("extra")'),
            ('subs.add_parser("ship")', 'subs.add_parser("ship"); subs.add_parser(hook())'),
            ('subs.add_parser("ship")', 'subs.add_parser("ship"); hook(subs)'),
            ("args = parser.parse_args()", "args = parser.parse_args(); hook(args)"),
            ('args.cmd == "ship"', 'args.cmd == "ship" and hook()'),
            ("args = parser.parse_args()", "args = parser.parse_args(); args.cmd = hook()"),
        ):
            with self.subTest(change=new):
                self.assertEqual(default_edges(source.replace(old, new)), [])

    def test_example_entries_in_tree_and_explicit_mapping(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            example = root / "examples"
            example.mkdir()
            script = "import click\n@click.command('run')\ndef run(): pass\n"
            (root / "app.py").write_text(script)
            (example / "demo.py").write_text(script)
            children = {c["label"]: c for c in build_graph(root)["tree"]["children"]}
            self.assertEqual(children["run"]["evidence"][0]["file"], "app.py")
            self.assertEqual(children["examples/"]["children"][0]["evidence"][0]["file"], "examples/demo.py")
            self.assertIn("run", {c["label"] for c in build_graph(example)["tree"]["children"]})
            (root / "how2trust.ini").write_text("[group:Mapped]\nentries = examples/demo.py::run\n")
            children = {c["label"]: c for c in build_graph(root)["tree"]["children"]}
            self.assertNotIn("examples/", children)
            self.assertEqual(children["Mapped"]["children"][0]["label"], "run")


if __name__ == "__main__":
    unittest.main()
