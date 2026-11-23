"""Cross-file source context for the explanation of a shared workflow."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from how2trust.project import build_graph, canonical
from how2trust.semantics import _batches, generate_semantics


class SemanticContextTests(unittest.TestCase):
    def test_root_readme_vocabulary_is_bounded_and_cannot_support_a_claim(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "actions.py").write_text("def archive():\n    return True\n", encoding="utf-8")
            (root / "padding.py").write_text("# fill a separate source batch\n" * 500, encoding="utf-8")
            (root / "README.md").write_text("术语：归档。\n" + "仓库说明" * 2000, encoding="utf-8")
            graph = build_graph(root)
        with patch("how2trust.semantics.BATCH_CHARS", 6200):
            batches = list(_batches(graph))
            self.assertGreater(len(batches), 1)
            vocabulary, = [b["terminology"] for b in batches if "terminology" in b]
            self.assertEqual(vocabulary["file"], "README.md")
            self.assertTrue(vocabulary["text"].startswith("术语：归档。"))
            self.assertLessEqual(len(vocabulary["text"]), 310)
            self.assertTrue(all(len(canonical(b)) <= 6200 for b in batches))

        class DocumentOnly:
            def complete(self, messages):
                claim = {"text": "归档将数据永久移出列表。", "refs": [{"file": "README.md", "line": 1, "end": 1}]}
                return json.dumps({"overview": {"claims": [claim]}, "items": []})

        with self.assertRaisesRegex(ValueError, "未提供的源码"):
            generate_semantics(graph, DocumentOnly())

    def test_shared_operation_gets_both_callers_and_their_trigger_conditions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "actions.py").write_text(
                "from search_index import refresh\n"
                "def publish(is_public):\n"
                "    if is_public:\n"
                "        refresh()\n\n"
                "def archive(labels):\n"
                "    for label in labels:\n"
                "        label.strip()\n"
                "    refresh()\n", encoding="utf-8")
            (root / "middle.py").write_text("# Padding for a separate source batch\n" * 450, encoding="utf-8")
            (root / "search_index.py").write_text("def refresh():\n    pass\n", encoding="utf-8")
            graph = build_graph(root)

        with patch("how2trust.semantics.BATCH_CHARS", 6200):
            batches = list(_batches(graph))
        target = "function:search_index.py:refresh"
        batch = next(b for b in batches if any(n["id"] == target for n in b["nodes"]))
        self.assertFalse(any(n["file"] == "actions.py" for n in batch["nodes"]))
        self.assertEqual({tuple(row) for row in batch["relations"] if row[2] == target}, {
            ("function:actions.py:publish", "calls", target, "actions.py", 4),
            ("function:actions.py:archive", "calls", target, "actions.py", 9),
        })
        numbered = "\n".join(b["text"] for b in batch["sources"] if b["file"] == "actions.py")
        for line in ("3 |     if is_public:", "4 |         refresh()", "7 |     for label in labels:",
                     "8 |         label.strip()", "9 |     refresh()"):
            self.assertIn(line, numbered)
        self.assertLessEqual(len(canonical(batch)), 6200)
