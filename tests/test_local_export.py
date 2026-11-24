import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from how2trust.__main__ import main
from how2trust.llm import ModelClient
from how2trust.project import build_graph
from how2trust.semantic_store import SemanticStore


class LocalExportTests(unittest.TestCase):
    def test_selected_cli_export_and_snapshot_scoped_reopening(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            repo.mkdir()
            source = "def unrelated(): return 'OUTSIDE_SELECTION'\n\nclass Orders:\n    def send(self): return 7\n"
            (repo / "orders.py").write_text(source, encoding="utf8")
            html, data = root / "local.html", root / "local.json"
            args = ["export", str(repo), "--select", "orders.py::Orders.send", "-o", str(html), "--json", str(data),
                    "--base-url", "http://localhost:9991", "--model", "local-reader"]
            def respond(messages):
                self.assertNotIn("OUTSIDE_SELECTION", str(messages))
                payload = json.loads(messages[-1]["content"])
                node, = payload["nodes"]
                return json.dumps({"groups": [{"nodeIds": [node["id"]], "label": "发货", "text": "返回 7。",
                                               "refs": [{"file": "orders.py", "line": 4, "end": 4}]}]})

            previous = os.environ.get("LOCALAPPDATA")
            if previous is None:
                self.addCleanup(os.environ.pop, "LOCALAPPDATA", None)
            else:
                self.addCleanup(os.environ.__setitem__, "LOCALAPPDATA", previous)
            os.environ["LOCALAPPDATA"] = directory
            with patch.object(ModelClient, "complete", side_effect=respond) as calls:
                self.assertEqual(main(args), 0)
                self.assertEqual(main(args), 0)
                calls.assert_called_once()
            exported = json.loads(data.read_bytes())
            self.assertTrue(exported["localOnly"])
            self.assertEqual(exported["files"]["orders.py"].splitlines(), ["", "", "", "    def send(self): return 7"])
            self.assertNotIn("OUTSIDE_SELECTION", html.read_text(encoding="utf8"))
            self.assertNotIn("unrelated", data.read_text(encoding="utf8"))

            cache = root / "how2trust" / "semantics"
            graph = build_graph(repo)
            SemanticStore(graph, ModelClient(), "", cache, repository=repo).load_local_views()
            self.assertEqual(graph["localBusinessViews"][0]["id"], exported["initialLocalView"])
            (repo / "orders.py").write_text(source.replace("return 7", "return 9"), encoding="utf8")
            new_graph = build_graph(repo)
            SemanticStore(new_graph, ModelClient(), "", cache, repository=repo).load_local_views()
            self.assertNotIn("localBusinessViews", new_graph)
