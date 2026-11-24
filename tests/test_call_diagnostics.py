import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

from how2trust.analyzer import analyze


class CallDiagnosticsTest(unittest.TestCase):
    def records(self, source, files=None):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for path, text in {"main.py": source, **(files or {})}.items():
                file = root / path
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_text(textwrap.dedent(text).strip() + "\n", encoding="utf8")
            result = analyze(root)
        return {d["expression"]: d for d in result["diagnostics"]
                if d.get("file") == "main.py" and "expression" in d}

    def test_direct_and_inferred_outside_apis(self):
        records = self.records("""
            import logging
            from graspologic.partition import hierarchical_leiden as partition
            logger = logging.getLogger(__name__)
            def run(x):
                groups = []
                logger.warning("empty")
                groups.append(len(x))
                return partition(sorted(x))
        """)
        for expr, category, name in [
            ("logging.getLogger", "stdlib", "logging.getLogger"),
            ("logger.warning", "stdlib", "logging.Logger.warning"),
            ("groups.append", "builtin", "builtins.list.append"),
            ("len", "builtin", "builtins.len"),
            ("sorted", "builtin", "builtins.sorted"),
            ("partition", "external", "graspologic.partition.hierarchical_leiden"),
        ]:
            with self.subTest(expr=expr):
                record = records[expr]
                self.assertEqual((record["category"], record["targetName"]), (category, name))
                self.assertNotIn("未解析", record["message"])
        self.assertEqual(records["len"]["column"], 19)

    def test_ambiguous_receiver_shadowed_builtin_and_absent_local_modules(self):
        records = self.records("""
            from orders.missing import calculate
            from .not_here import other
            from broken import parse
            def run(len, store):
                len(store)
                store.append(1)
                calculate()
                other()
                parse()
        """, {"src/orders/sub.py": "x = 1", "broken.py": "def missing(:"})
        self.assertEqual(set(records), {"len", "store.append", "calculate", "other", "parse"})
        for record in records.values():
            self.assertEqual(record["category"], "unresolved")
            self.assertNotIn("targetName", record)

    def test_unknown_and_rebound_targets_do_not_inherit_old_type(self):
        records = self.records("""
            import logging
            from something import patch as replace
            logger = logging.getLogger(__name__)
            def run(value):
                logging.warning = value
                logging.warning()
                logger.warning = value
                logger.warning()
                data = []
                data = replace(value)
                data.append(1)
        """)
        for expr in ("logging.warning", "logger.warning", "data.append"):
            self.assertEqual(records[expr]["category"], "unresolved")

    def test_container_constructor_and_rebound_builtin(self):
        records = self.records("""
            import builtins
            def f(delegate):
                items = list()
                items.append(1)
                builtins.len = delegate
                return len(items)
        """)
        self.assertEqual(records["items.append"]["targetName"], "builtins.list.append")
        self.assertEqual(records["len"]["category"], "unresolved")

    def test_only_import_roots_shadow_the_stdlib(self):
        source = "import logging\nlogger = logging.getLogger(__name__)\nlogger.warning('empty')"
        records = self.records(source, {"app/logging/__init__.py": "", "app/__init__.py": ""})
        self.assertEqual(records["logger.warning"]["targetName"], "logging.Logger.warning")
        local = self.records(source, {"src/logging/__init__.py": ""})
        self.assertEqual(local["logger.warning"]["category"], "unresolved")

    def test_no_pycg_still_preserves_import_and_location(self):
        with patch("how2trust.pycg_backend.supplement_calls", return_value=None):
            records = self.records("""
                import json as j
                from vendor.alg import partition
                def f(x):
                    return partition(j.loads(x))
            """)
        self.assertEqual(records["partition"]["targetName"], "vendor.alg.partition")
        self.assertEqual(records["j.loads"]["targetName"], "json.loads")
        self.assertEqual(records["partition"]["line"], 4)
