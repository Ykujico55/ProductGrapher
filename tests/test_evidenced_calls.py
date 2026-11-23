import tempfile
import textwrap
import unittest
from pathlib import Path

from how2trust.analyzer import analyze


def analyze_source(source):
    with tempfile.TemporaryDirectory() as directory:
        Path(directory, "app.py").write_text(textwrap.dedent(source).lstrip(), encoding="utf-8")
        return analyze(Path(directory))


class EvidencedCallsTest(unittest.TestCase):
    def test_contextmanager_factory_and_unknown_decorators(self):
        graph = analyze_source("""
            from contextlib import contextmanager as manager
            import contextlib
            @manager
            def rewrite():
                yield 1
            @contextlib.contextmanager
            def reader():
                yield 2
            def wrap(fn):
                return lambda: None
            @wrap
            @manager
            def decorated():
                yield 3
            def use():
                with rewrite(), reader():
                    pass
                decorated()
            def shadowed(manager):
                @manager
                def custom():
                    yield
                custom()
        """)
        calls = [e for e in graph["edges"] if e["kind"] == "calls"]
        self.assertCountEqual([(e["source"], e["target"], e["line"]) for e in calls], [
            ("function:app.py:use", "function:app.py:rewrite", 16),
            ("function:app.py:use", "function:app.py:reader", 16),
        ])
        self.assertTrue(all("contextmanager" in e["basis"] for e in calls))

    def test_first_iterable_uses_outer_binding_but_comprehension_body_does_not(self):
        graph = analyze_source("""
            def items():
                return {}
            def run():
                return {key: value for key, value in items().items()}
            def shadowed():
                return [items() for items in items()]
            def unknown(items):
                return [v for v in items()]
            def later():
                return [v for items in [] for v in items()]
        """)
        calls = [e for e in graph["edges"] if e["kind"] == "calls"]
        self.assertCountEqual([(e["source"], e["line"], e["column"]) for e in calls], [
            ("function:app.py:run", 4, 42),
            ("function:app.py:shadowed", 6, 34),
        ])
        self.assertEqual({e["target"] for e in calls}, {"function:app.py:items"})
        self.assertTrue(any("items().items" in d["message"] for d in graph["diagnostics"]))
