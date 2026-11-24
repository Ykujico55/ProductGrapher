import json
import io
import shutil
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from how2trust.__main__ import main
from how2trust.discussion import Discussion, Evidence, Snapshot
from how2trust.project import build_graph, canonical
from how2trust.review_html import render_report
from how2trust.web import make_server, standalone


def descend(tree, *path):
    for segment in path:
        tree = next(item for item in tree["children"] if item["label"] == segment)
    return tree


class ProjectTests(unittest.TestCase):
    def test_supporting_text_can_be_searched_read_and_cited_from_the_same_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, source in {"app.js": "// start\nselectModule();", "template.html": "<main/>",
                                 "README.md": "# Demo", "config.json": '{"theme":"light"}',
                                 "module.py": "def start(): pass"}.items():
                (root / name).write_text(source, encoding="utf-8")
            (root / ".private").mkdir()
            (root / ".private" / "config.json").write_text("{}")
            (root / "binary.txt").write_bytes(b"abc\x00def")
            graph = build_graph(root)
            self.assertEqual(set(graph["files"]), {"app.js", "template.html", "README.md", "config.json", "module.py"})
            self.assertEqual({n["file"] for n in graph["nodes"]}, {"module.py"})
            (root / "app.js").write_text("changed()")
            evidence = Evidence(Snapshot(graph))
            self.assertIn("app.js", evidence.lookup({"lookup":"find", "query":"selectModule"}, [], 2000))
            self.assertIn("2 | selectModule();", evidence.lookup({"lookup":"read", "file":"app.js", "line":2}, [], 2000))
            _, citations = evidence.checked_answer("[app.js:L2]")
            self.assertEqual(citations, [{"file":"app.js", "line":2, "end":2}])
            self.assertNotEqual(graph["snapshot"], build_graph(root)["snapshot"])

    def test_repeated_in_repo_export_excludes_outputs_and_preserves_existing_inputs(self):
        with tempfile.TemporaryDirectory() as tmp, redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            root = Path(tmp)
            ordinary = '<h1>Sales report</h1>\n<script type="application/json" id="report-data">{"total":3}</script>'
            (root / "view.html").write_text(ordinary, encoding="utf-8")
            # The unrendered report template must remain available to model queries.
            (root / "template.html").write_text('<script type="application/json" id="report-data">@@DATA@@</script>')
            first = build_graph(root)
            report = {"schema": "how2trust.review.v1", "changes": []}
            (root / "audit.json").write_text(json.dumps(report))
            (root / "audit.html").write_bytes(render_report(report))
            html, data = root / "map.html", root / "map.json"
            args = ["export", tmp, "-o", str(html), "--json", str(data)]
            self.assertEqual(main(args), 0)
            original = (html.read_bytes(), data.read_bytes())
            self.assertEqual(main(args), 0)
            self.assertEqual(original, (html.read_bytes(), data.read_bytes()))
            self.assertEqual(first, build_graph(root))
            self.assertIn("template.html", first["files"])
            self.assertEqual(main(["export", tmp, "-o", str(root / "view.html")]), 1)
            self.assertEqual(first["files"]["view.html"].splitlines(), ordinary.splitlines())
            self.assertEqual((root / "view.html").read_text(), ordinary)

    def test_copy_is_byte_identical_including_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = Path(tmp, "orders")
            original.mkdir()
            (original / "how2trust.ini").write_text(
                "[project]\nname = 订单工作台\n[group:交易服务/订单管理/取消订单]\nentries = main.py::cancel_order\n",
                encoding="utf-8",
            )
            (original / "main.py").write_text(
                'from fastapi import FastAPI\napp = FastAPI()\n'
                'def cancel(order_id): return order_id\n'
                '@app.delete("/orders/{order_id}")\ndef cancel_order(order_id): return cancel(order_id)\n',
                encoding="utf-8",
            )
            clone = Path(tmp, "different-name")
            shutil.copytree(original, clone)
            first, second = build_graph(original), build_graph(clone)
            self.assertEqual(canonical(first), canonical(second))
            self.assertEqual(standalone(first), standalone(second))
            group = descend(first["tree"], "交易服务", "订单管理", "取消订单")
            self.assertEqual(group["evidence"][0]["file"], "how2trust.ini")
            self.assertEqual(group["children"][0]["label"], "DELETE /orders/{order_id}")
            self.assertEqual(len({e["id"] for e in first["edges"]}), len(first["edges"]))
            for item in first["nodes"] + first["edges"]:
                source = first["files"][item["file"]]
                self.assertTrue(1 <= item["line"] <= item["end"] <= max(1,len(source.splitlines())))
            with (clone / "main.py").open("a") as out:
                out.write("\n# changed\n")
            self.assertNotEqual(first["snapshot"], build_graph(clone)["snapshot"])

    def test_unmatched_business_mapping_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "how2trust.ini").write_text("[group:missing]\nentries = missing.py::f\n")
            graph = build_graph(root)
            self.assertFalse(graph["tree"]["children"])
            self.assertIn("missing.py::f", graph["diagnostics"][0]["message"])

    def test_html_source_is_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "markup.py").write_text('text = "</script><img onerror=boom()>"')
            html = standalone(build_graph(root))
            self.assertNotIn(b'</script><img onerror', html)
            self.assertIn(b'\\u003c/script', html)
            self.assertNotIn(b'src="/assets/', html)

    def test_local_server_annotation_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "main.py").write_text("def submit_order(): pass\n")
            graph = build_graph(Path(tmp))
            discussion = Discussion(Path(tmp, "state.db"), graph, {})
            with make_server(graph, discussion, 0) as server:
                worker = threading.Thread(target=server.serve_forever, daemon=True)
                worker.start()
                base = f"http://127.0.0.1:{server.server_port}"
                try:
                    with urlopen(base + "/api/graph") as r:
                        self.assertEqual(json.load(r)["snapshot"], graph["snapshot"])
                    headers = {"Content-Type": "application/json"}
                    body = json.dumps({"targets":[graph["tree"]["id"]],"text":"review"}).encode()
                    with urlopen(Request(base + "/api/notes", data=body, headers=headers)) as r:
                        note, = json.load(r)["notes"]
                    with urlopen(Request(base + "/api/notes/" + note["id"], method="DELETE")) as r:
                        self.assertEqual(json.load(r)["notes"], [])
                    with self.assertRaises(HTTPError) as error:
                        urlopen(Request(base + "/api/notes", data=body, headers={**headers,"Origin":"https://external.test"}))
                    self.assertEqual(error.exception.code, 403)
                finally:
                    server.shutdown()
                    worker.join()
