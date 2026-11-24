import ast
import tempfile
import textwrap
import unittest
from pathlib import Path

from how2trust.analyzer import Index
from how2trust.pycg_backend import supplement_calls


class ExceptionCallTargetsTest(unittest.TestCase):
    def analyze_calls(self, source, errors=""):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app.py").write_text(textwrap.dedent(source).lstrip(), encoding="utf-8")
            if errors:
                (root / "errors.py").write_text(textwrap.dedent(errors).lstrip(), encoding="utf-8")
            index = Index(root)
            index.load()
            index.connect()
            if index.unresolved_calls:
                supplement_calls(index)
        return [(edge["expression"], edge["target"]) for edge in index.edges
                if edge["kind"] == "calls" and edge["expression"].startswith("e.")]

    def test_separate_handlers_and_imported_identity_decorator(self):
        calls = self.analyze_calls("""
            import errors
            def run():
                try: pass
                except errors.First as e: e.report()
                except errors.Second as e:
                    e.report()
                    def deferred():
                        e.report()
                e.report()
        """, """
            def unchanged(cls): return cls
            @unchanged
            class First(Exception):
                def report(self): pass
            class Second(Exception):
                def report(self): pass
        """)
        self.assertEqual(calls, [("e.report", "method:errors.py:First.report"),
                                 ("e.report", "method:errors.py:Second.report")])

    def test_rebinding_and_dynamic_decorator(self):
        calls = self.analyze_calls("""
            class First(Exception):
                def report(self): pass
            def replace(cls): return First
            @replace
            class Second(Exception):
                def report(self): pass
            def run(other):
                try: pass
                except First as e:
                    e.report()
                    e = other
                    e.report()
                except Second as e:
                    e.report()
        """)
        self.assertEqual(calls, [("e.report", "method:app.py:First.report")])

    def test_ambiguous_subclass_and_tuple(self):
        calls = self.analyze_calls("""
            class Parent(Exception):
                def report(self): pass
                def inherited(self): pass
            class Child(Parent):
                def report(self): pass
            def run():
                try: pass
                except Parent as e:
                    e.report()
                    e.inherited()
                try: pass
                except (Parent, Child) as e:
                    e.report()
        """)
        self.assertEqual(calls, [("e.inherited", "method:app.py:Parent.inherited")])

    @unittest.skipUnless(hasattr(ast, "TryStar"), "except* requires Python 3.11")
    def test_except_star_binds_a_group(self):
        calls = self.analyze_calls("""
            class Problem(Exception):
                def report(self): pass
            def run():
                try: pass
                except* Problem as e: e.report()
        """)
        self.assertEqual(calls, [])
