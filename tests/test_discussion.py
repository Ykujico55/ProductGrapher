import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

from how2trust.discussion import Discussion, Evidence, Snapshot


def fixture():
    src = {"file": "app.py", "line": 1, "end": 2}
    return {
        "name": "测试", "snapshot": "snap-a",
        "files": {"app.py": "def main():\n    return helper()\n",
                  "svc.py": "def helper():\n    return 42\n"},
        "nodes": [{"id": "main", "kind": "function", "label": "main", **src},
                  {"id": "helper", "kind": "function", "label": "helper", **dict(src, file="svc.py")}],
        "edges": [{"id": "link", "kind": "calls", "source": "main", "target": "helper",
                   "file": "app.py", "line": 2, "end": 2}],
        "tree": {"id": "root", "kind": "project", "label": "项目", "nodeIds": [], "evidence": [],
                 "children": [{"id": "entry", "kind": "entry", "label": "入口", "children": [],
                               "nodeIds": ["main"], "evidence": [src]}]},
    }


class TestDiscussion(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "db.sqlite"
        self.graph = fixture()
        self.discussion = Discussion(self.path, self.graph)

    def test_notes_survive_restart_and_follow_snapshot(self):
        note = self.discussion.note(["entry", "link", "entry"], "检查调用")["notes"][0]
        self.assertEqual(note["targets"], ["entry", "link"])
        self.assertEqual(Discussion(self.path, self.graph).state()["notes"], [note])
        other = Discussion(self.path, dict(self.graph, snapshot="different"))
        self.assertEqual(other.state()["notes"], [])
        with self.assertRaises(ValueError):
            other.delete_note(note["id"])
        self.assertEqual(self.discussion.delete_note(note["id"])["notes"], [])
        with self.assertRaises(ValueError):
            self.discussion.note(["unknown"], "text")
        with self.assertRaisesRegex(RuntimeError, "配置"):
            self.discussion.chat(["entry"], "", "问题")

    def test_lookup_uses_snapshot_and_marks_verified_sources(self):
        evidence = Evidence(Snapshot(self.graph))
        self.assertIn("helper", evidence.lookup({"lookup": "graph", "id": "main"}, [], 1000))
        self.assertIn("svc.py", evidence.lookup({"lookup": "find", "query": "42"}, [], 1000))
        self.assertIn("未收录", evidence.lookup({"lookup": "read", "file": "../external"}, [], 100))
        self.assertIn("旧问题", evidence.lookup({"lookup": "history", "query": "问题"},
                                             [{"role": "user", "content": "旧问题"}], 100))
        content, sources = evidence.checked_answer("[svc.py:L2] [svc.py:L1-L2] [outside.py:L1]")
        self.assertEqual(sources, [{"file": "svc.py", "line": 2, "end": 2}])
        self.assertEqual(content.count("未核验"), 2)

    def test_questions_follow_the_displayed_guide_from_root_or_leaf(self):
        graph = self.graph
        ref = {"file": "svc.py", "line": 2, "end": 2}
        graph["nodes"].append({"id": "command", "label": "ship", "kind": "entry", **ref})
        graph["tree"]["children"][0]["nodeIds"] = ["command"]
        graph["semantics"] = {
            "overview": {"text": "订单按仓分开发货", "refs": [ref]},
            "items": [{"nodeId": "command", "title": "发货", "text": "按仓记录实际出库量",
                       "aliases": ["出库"], "refs": [ref]}],
            "groups": [{"id": "cap-shipping", "kind": "capability", "label": "仓库履约", "evidence": [ref],
                        "basis": "模型归纳的业务分组", "nodeIds": [], "children": [
                            {"id": "cap-leaf", "kind": "entry", "label": "ship", "children": [],
                             "nodeIds": ["command"], "evidence": [ref]}
                        ]}],
        }
        app = Discussion(self.path, graph, {"base_url": "http://localhost/v1", "model": "fixture"})
        evidence = Evidence(app.snapshot)
        self.assertIn("cap-shipping", app.snapshot.describe("root")["children"])
        self.assertIn("cap-shipping", str(list(app.snapshot.neighborhood("root"))))
        self.assertIn("command", app.snapshot.describe("cap-shipping")["members"])
        for target, claim in (("root", "订单按仓分开发货"), ("cap-shipping", "仓库履约"),
                              ("cap-leaf", "按仓记录实际出库量"), ("entry", "按仓记录实际出库量"),
                              ("command", "按仓记录实际出库量")):
            self.assertIn(claim, evidence.lookup({"lookup": "graph", "id": target}, [], 3000))
            self.assertIn(claim, evidence.lookup({"lookup": "find", "query": claim}, [], 3000))
            with patch.object(app, "_complete", return_value="依据 [svc.py:L2]") as model:
                state = app.chat([target], "", "解释当前选区")
            self.assertIn(claim, model.call_args.args[0][-1]["content"])
            self.assertEqual(state["threads"][-1]["messages"][-1]["sources"], [ref])
        with app._db() as db:
            db.execute("UPDATE threads SET targets=? WHERE id=?", (json.dumps(["entry"]), state["activeThread"]))
        with patch.object(app, "_complete", return_value="继续"):
            result = app.chat(["command"], state["activeThread"], "继续")
        self.assertEqual(len(result["threads"][-1]["messages"]), 4)

    def test_model_transport_lookup_followup_and_restart(self):
        received = []
        answers = [
            '{"lookup":"read","file":"svc.py","line":1,"count":2}',
            "函数返回 42 [svc.py:L1-L2]。",
            "是的，入口调用了 helper [app.py:L2]。",
        ]

        class MockModel(BaseHTTPRequestHandler):
            def do_POST(self):
                request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                received.append((self.path, request))
                raw = json.dumps({"choices": [{"message": {"content": answers.pop(0)}}]}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *args):
                pass

        with HTTPServer(("127.0.0.1", 0), MockModel) as server:
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                config = {"base_url": f"http://127.0.0.1:{server.server_port}/v1/", "model": "fixture"}
                app = Discussion(self.path, self.graph, config)
                state = app.chat(["entry"], "", "下游返回什么？")
                thread = state["activeThread"]
                self.assertEqual(state["threads"][0]["messages"][1]["sources"],
                                 [{"file": "svc.py", "line": 1, "end": 2}])
                app = Discussion(self.path, self.graph, config)
                state = app.chat([], thread, "上面提到的入口是否调用它？")
            finally:
                server.shutdown()
                worker.join()
        self.assertEqual(len(state["threads"][0]["messages"]), 4)
        self.assertEqual([path for path, request in received], ["/v1/chat/completions"] * 3)
        self.assertIn("2 |     return 42", received[1][1]["messages"][-1]["content"])
        self.assertIn("函数返回 42", received[2][1]["messages"][-2]["content"])
        with self.assertRaisesRegex(ValueError, "另一选区"):
            app.chat(["link"], thread, "继续")

    def test_failed_turn_is_atomic(self):
        app = Discussion(self.path, self.graph, {"base_url": "http://localhost/v1", "model": "fixture"})
        with patch.object(app, "_complete", side_effect=RuntimeError("模拟中断")):
            with self.assertRaisesRegex(RuntimeError, "模拟中断"):
                app.chat(["main"], "", "问题")
        self.assertEqual(app.state()["threads"], [])

    def test_unreadable_lookup_is_corrected_before_saving_an_answer(self):
        app = Discussion(self.path, self.graph, {"base_url": "http://localhost/v1", "model": "fixture"})
        broken = '<｜DSML｜calls><｜DSML｜invoke name="lookup">'
        requests = []
        replies = iter([broken, '{"lookup":"read","file":"svc.py"}', "函数返回 42。[svc.py:L2]"])

        def respond(messages):
            requests.append([m["content"] for m in messages])
            return next(replies)

        with patch.object(app, "_complete", side_effect=respond):
            thread = app.chat(["main"], "", "下游做了什么？")["threads"][0]
        self.assertIn("上一条检索指令格式不完整", requests[1][-1])
        self.assertIn("2 |     return 42", requests[2][-1])
        self.assertNotIn(broken, str(requests))
        self.assertEqual([m["content"] for m in thread["messages"]], ["下游做了什么？", "函数返回 42。[svc.py:L2]"])
        self.assertEqual(thread["messages"][1]["sources"], [{"file": "svc.py", "line": 2, "end": 2}])
        with patch.object(app, "_complete", return_value=broken) as model:
            with self.assertRaisesRegex(RuntimeError, "未能生成回答"):
                app.chat([], thread["id"], "追问")
            self.assertEqual(model.call_count, 2)
        self.assertEqual(app.state()["threads"], [thread])

    def test_retry_saved_tool_reply_in_place_uses_original_question_and_context(self):
        config = {"base_url": "http://localhost/v1", "model": "fixture"}
        app = Discussion(self.path, self.graph, config)
        original = "解释这个模块"
        broken = '<｜DSML｜calls><｜DSML｜invoke name="lookup">'
        with patch.object(app, "_complete", return_value="普通回答"):
            thread = app.chat(["main"], "", original)["activeThread"]
            app.chat([], thread, "后来的问题")
        with app._db() as db:
            db.execute("UPDATE messages SET content=? WHERE thread=? AND seq=1", (broken, thread))
        saved = app.state()["threads"][0]["messages"]
        self.assertTrue(saved[1]["needsRetry"])
        self.assertNotIn(broken, saved[1]["content"])
        with patch.object(app, "_complete", return_value="后续回答") as model:
            app.chat([], thread, "继续讨论")
            self.assertNotIn(broken, str(model.call_args.args[0]))
        with self.assertRaisesRegex(ValueError, "另一选区"):
            app.chat(["helper"], thread, original, retry_message_id=1)
        with patch.object(app, "_complete", side_effect=RuntimeError("模拟掉线")):
            with self.assertRaisesRegex(RuntimeError, "模拟掉线"):
                app.chat([], thread, original, retry_message_id=1)
        before = app.state()["threads"][0]["messages"]
        with patch.object(app, "_complete", return_value="入口调用 helper。[app.py:L2]") as model:
            state = app.chat([], thread, "忽略客户端替换的问题", retry_message_id=1)
            self.assertIn(original, model.call_args.args[0][-1]["content"])
            self.assertIn("app.py", model.call_args.args[0][-1]["content"])
            self.assertNotIn("后来的问题", str(model.call_args.args[0]))
            self.assertNotIn("忽略客户端", str(model.call_args.args[0]))
        after = state["threads"][0]["messages"]
        self.assertEqual(before[:1] + before[2:], after[:1] + after[2:])
        self.assertNotIn("needsRetry", after[1])
        self.assertEqual(after[1]["sources"], [{"file": "app.py", "line": 2, "end": 2}])
        self.assertEqual(Discussion(self.path, self.graph, config).state()["threads"], state["threads"])
        with self.assertRaisesRegex(ValueError, "找不到需要重新回答"):
            app.chat([], thread, original, retry_message_id=1)

    def test_lookup_follows_the_same_claim_and_shared_paths_as_the_viewer(self):
        from how2trust.capabilities import shared_calls
        from how2trust.project import build_graph

        graph = build_graph(Path(__file__).resolve().parents[1] / "audit/terra-fulfillment/project")
        reference = {"file": "dispatchdesk/store.py", "line": 185, "end": 186}
        claim = {"text": "可再售退货会重试等待订单", "refs": [reference]}
        retry = next(n["id"] for n in graph["nodes"] if n["qualname"] == "Desk.retry")
        snapshot = Snapshot(graph)
        snapshot.set_semantics({"overview": {**claim, "claims": [claim]},
                                "sharedCalls": shared_calls(graph, [])})
        self.assertEqual(next(snapshot.matches("可再售")), {"overviewClaim": claim})
        paths = [row for row in snapshot.neighborhood(retry) if row.get("sharedTarget") == retry]
        self.assertEqual({snapshot.items[p["entry"]]["label"]: p["steps"][-1]["line"] for p in paths},
                         {"stock": 101, "cancel": 157, "return": 186})
        for path in paths:
            first, second = path["steps"]
            self.assertEqual(first["target"], second["source"])

    def test_recovers_old_turn_outside_model_context(self):
        app = Discussion(self.path, self.graph, {
            "base_url": "http://localhost/v1", "model": "fixture", "context_chars": 8000,
        })
        thread = ""
        with patch.object(app, "_complete", return_value="x" * 700):
            for i in range(18):
                question = "记住最初的术语橘子。" if i == 0 else f"第 {i} 轮"
                state = app.chat(["main"], thread, question)
                thread = state["activeThread"]

        def lookup_then_answer(messages):
            self.assertLessEqual(sum(len(m["content"]) + 16 for m in messages), 8000)
            if len(messages) >= 2 and messages[-1]["content"].startswith("只读查询结果"):
                self.assertIn("记住最初的术语橘子", messages[-1]["content"])
                return "你最初使用的术语是橘子。"
            self.assertFalse(any("记住最初的术语橘子" in m["content"] for m in messages))
            return '{"lookup":"history","query":"最初的术语"}'

        with patch.object(app, "_complete", side_effect=lookup_then_answer):
            state = app.chat(["main"], thread, "第一轮的术语是什么？")
        self.assertEqual(len(state["threads"][0]["messages"]), 38)
        self.assertIn("橘子", state["threads"][0]["messages"][-1]["content"])
