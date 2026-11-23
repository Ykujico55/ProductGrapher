"""Run all model stages through independently configured local HTTP providers."""
from contextlib import contextmanager, redirect_stdout
from http.server import BaseHTTPRequestHandler, HTTPServer
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest

from how2trust.__main__ import main
from how2trust.discussion import Discussion
from how2trust.llm import profiles


@contextmanager
def provider_credentials(values):
    previous = {name: os.environ.get(name) for name in values}
    try:
        os.environ.update(values)
        yield
    finally:
        for name, old in previous.items():
            if old is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = old


@contextmanager
def provider(protocol, answers, tokens):
    requests = []

    class Endpoint(BaseHTTPRequestHandler):
        def do_POST(self):
            data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            model = data["model"]
            if protocol == "anthropic":
                auth = self.headers.get("x-api-key") == tokens.get(model) and "Authorization" not in self.headers
                envelope = {"content": [{"type": "text", "text": answers.get(model, "")}]}
            else:
                auth = self.headers.get("Authorization") == "Bearer " + tokens.get(model, "") and "x-api-key" not in self.headers
                task = json.loads(data["messages"][-1]["content"])["task"]
                envelope = {"choices": [{"message": {"content": answers[(model, task)]}}]}
            requests.append((self.path, data, auth, self.headers.get("anthropic-version")))
            content = json.dumps(envelope).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, *args):
            pass

    with HTTPServer(("127.0.0.1", 0), Endpoint) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}/v1", requests
        finally:
            server.shutdown()
            thread.join()


class ModelStageTests(unittest.TestCase):
    def test_configured_stages_preserve_requirements_and_reuse_frozen_guide(self):
        source = "跨仓订单发出第一仓后显示部分发货。"
        scene = {"name": "部分发货", "given": "两仓已预留", "when": "发出第一仓", "then": "状态为 partial"}
        symbol = "function:shipping.py:ship"
        ref = {"file": "shipping.py", "line": 1, "end": 2}
        guide = {"overview": {"claims": [{"text": "查询发货状态。", "refs": [ref]}]},
                 "items": [{"nodeId": symbol, "title": "发货", "aliases": ["部分发货"],
                            "text": "返回部分发货状态。", "refs": [ref]}]}
        verification = {"claimReplacements": [{"id": 1, "claims": [{"text": "发货返回部分发货状态。", "refs": [ref]}]}],
                        "itemReplacements": []}
        answers = {("guide-v1", "explain"): json.dumps(guide), ("guide-v1", "verify"): json.dumps(verification),
                   "qa-v2": "返回部分发货状态 [shipping.py:L2]。",
                   "planner-v3": json.dumps({"requirements": [{"id": "R1", "scenarios": [scene]}]})}
        # Only fabricated, per-stage credentials are used. Captures store verification booleans.
        tokens = {model: f"test-{model}" for model in ("guide-v1", "qa-v2", "planner-v3")}
        env_vars = {model: "H2T_TEST_" + model.split("-")[0].upper() for model in tokens}
        with (tempfile.TemporaryDirectory() as temp, provider("openai", answers, tokens) as (compat, a),
              provider("anthropic", answers, tokens) as (messages, b),
              provider_credentials({env_vars[m]: t for m, t in tokens.items()}), redirect_stdout(io.StringIO())):
            folder = Path(temp)
            repo = folder / "repo"
            repo.mkdir()
            (repo / "shipping.py").write_text("def ship():\n    return 'partial'\n\nif __name__ == '__main__':\n    ship()\n", encoding="utf-8")
            request_file = folder / "request.md"
            request_file.write_text(source, encoding="utf-8")
            config_file = folder / "providers.json"
            stage_models = {"semantics": "guide-v1", "scenarios": "planner-v3", "chat": "qa-v2"}
            configured = {model: {"base_url": compat if model == "guide-v1" else messages,
                                  "model": model, "api_key_env": env_vars[model],
                                  "protocol": "openai" if model == "guide-v1" else "anthropic"}
                          for model in tokens}
            config_file.write_text(json.dumps({"providers": configured, "stages": stage_models}), encoding="utf-8")
            frozen, graph_file = folder / "guide.json", folder / "graph.json"
            export = ["export", str(repo), "--llm-config", str(config_file), "--requirements", str(request_file),
                      "--semantics", str(frozen), "--json", str(graph_file), "-o", str(folder / "index.html")]
            self.assertEqual(main([*export, "--with-semantics"]), 0)
            initial = graph_file.read_bytes()
            self.assertEqual(main(export), 0)
            self.assertEqual(graph_file.read_bytes(), initial)
            self.assertEqual(len(a), 2)
            graph = json.loads(initial)
            self.assertEqual(graph["semantics"]["model"], "guide-v1")
            self.assertEqual(graph["semantics"]["provider"], "guide-v1")
            self.assertEqual(graph["semantics"]["overview"]["text"], "发货返回部分发货状态。")
            draft = folder / "draft.json"
            self.assertEqual(main(["draft", "--spec", str(request_file), "--llm-config", str(config_file),
                                   "-o", str(draft)]), 0)
            contract = json.loads(draft.read_bytes())
            self.assertEqual(contract["task"], source)
            self.assertEqual(contract["requirements"], [{"id": "R1", "text": source, "scenarios": [scene], "checks": []}])

            discussion = Discussion(folder / "chat.sqlite", graph, profiles(config_file, {})["chat"])
            state = discussion.chat([symbol], "", "什么时候部分发货？")
            self.assertEqual(state["llm"]["model"], "qa-v2")
            self.assertIn("返回部分发货状态", state["threads"][0]["messages"][-1]["content"])
            self.assertEqual([(p, d["model"], ok, v) for p, d, ok, v in a],
                             [("/v1/chat/completions", "guide-v1", True, None)] * 2)
            self.assertEqual([(p, d["model"], ok, v) for p, d, ok, v in b],
                             [("/v1/messages", "planner-v3", True, "2023-06-01"),
                              ("/v1/messages", "qa-v2", True, "2023-06-01")])
            self.assertEqual(a[0][1]["messages"][0]["role"], "system")
            self.assertEqual(json.loads(a[0][1]["messages"][-1]["content"])["requirements"], source)
            for _, body, _, _ in b:
                self.assertTrue(body["system"])
                self.assertGreater(body["max_tokens"], 0)
                self.assertNotIn("system", [m["role"] for m in body["messages"]])
