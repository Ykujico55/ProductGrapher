"""Loopback-only viewer and deterministic, single-file HTML export."""

from __future__ import annotations

import json
import logging
import re
from contextlib import nullcontext
from errno import ENOSPC
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from inspect import signature
from urllib.parse import parse_qs, urlsplit

from .generation_progress import GenerationProgress
from .llm import ModelRequestError
from .project import canonical


ASSETS = files("how2trust").joinpath("assets")


def generation_error(exc, stage):
    """Only return local error descriptions, never the raw provider response."""
    if isinstance(exc, ModelRequestError):
        return 502, str(exc)
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return 502, "请求未完成，请检查模型服务连接和超时设置后重试。"
    if isinstance(exc, OSError):
        if exc.errno == ENOSPC:
            return 500, "磁盘空间不足，无法保存生成结果。请释放空间后重试。"
        return 500, "工作台无法读写本地讲解文件。请检查讲解目录的路径和读写权限后重试。"
    if isinstance(exc, ValueError):
        message = str(exc)
        if message == "请在工作台的模型设置中重新输入本次服务的 API Key。":
            return 400, message
        if message.startswith("此阶段尚未配置模型"):
            return 400, "请在模型设置中配置生产业务图使用的模型，再重新生成。"
        if stage == "prepare":
            return 400, "已有讲解未通过校验，请检查文件格式、源码快照和模型配置是否对应。"
        if "引用" in message or "节点" in message:
            return 400, "模型讲解未通过节点或源码引用校验。请重试，或更换模型重新生成。"
        return 400, "模型输出未满足当前阶段的结构或内容要求。请重试，并检查模型是否支持当前协议和输出格式。"
    return 500, "工作台未能完成当前阶段。请重试；如仍失败，请报告失败阶段和批次。"


def standalone(graph):
    html = ASSETS.joinpath("index.html").read_text(encoding="utf-8")
    js = ASSETS.joinpath("app.js").read_text(encoding="utf-8")
    css = ASSETS.joinpath("style.css").read_text(encoding="utf-8")
    data = canonical(graph).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    html, styles = re.subn(r'<link\b[^>]*href="/assets/style\.css"[^>]*>', lambda _: f"<style>{css}</style>", html)
    html, scripts = re.subn(r'<script\b[^>]*src="/assets/app\.js"[^>]*>\s*</script>',
                            lambda _: f"<script>window.H2T_GRAPH={data};</script>\n<script type=\"module\">{js}</script>", html)
    if (styles, scripts) != (1, 1):
        raise ValueError("HTML 模板缺少样式或脚本")
    return html.encode("utf-8")


def make_server(graph, discussion, port, review=None, semantics=None, models=None):
    generation = GenerationProgress()
    if semantics:
        discussion.semantic_llm = models.semantic_info() if models else semantics.client.info()
    static = {
        "/": ("index.html", "text/html"),
        "/assets/style.css": ("style.css", "text/css"),
        "/assets/app.js": ("app.js", "text/javascript"),
        "/assets/chat-markdown.js": ("chat-markdown.js", "text/javascript"),
        "/assets/vendor/marked.esm.js": ("vendor/marked.esm.js", "text/javascript"),
    }
    if review:
        from .review_html import render_report
        review_page = render_report({**review, "graphUrl": "/"})

    class Handler(BaseHTTPRequestHandler):
        def send_bytes(self, status, data, content_type):
            self.send_response(status)
            self.send_header("Content-Type", content_type + "; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)

        def send_json(self, status, value):
            self.send_bytes(status, canonical(value).encode("utf-8"), "application/json")

        def allowed(self):
            hosts = {f"localhost:{self.server.server_port}", f"127.0.0.1:{self.server.server_port}"}
            host = self.headers.get("Host")
            origin = self.headers.get("Origin")
            if host not in hosts or (origin and origin != f"http://{host}"):
                self.send_json(403, {"error": "仅允许来自本机查看器的请求"})
                return False
            return True

        def do_GET(self):
            if not self.allowed():
                return
            path = urlsplit(self.path).path
            if path == "/api/graph":
                self.send_json(200, {**graph, "hasReview": True} if review else graph)
            elif path == "/review" and review:
                self.send_bytes(200, review_page, "text/html")
            elif path == "/api/state":
                state = discussion.state()
                if semantics:
                    state = {**state, "generationProgress": generation.snapshot()}
                self.send_json(200, state)
            elif path == "/api/semantics/progress" and semantics:
                self.send_json(200, generation.snapshot())
            elif path == "/api/models" and models:
                self.send_json(200, models.describe())
            elif path == "/export.html":
                local_ids = parse_qs(urlsplit(self.path).query, keep_blank_values=True).get("local")
                if local_ids is None:
                    self.send_bytes(200, standalone(graph), "text/html")
                else:
                    from .local_export import local_export_graph
                    view = next((v for v in graph.get("localBusinessViews", []) if [v["id"]] == local_ids), None)
                    if view is None:
                        self.send_json(404, {"error": "当前快照中没有这张选区业务图。"})
                    else:
                        self.send_bytes(200, standalone(local_export_graph(graph, view)), "text/html")
            elif path in static:
                filename, content_type = static[path]
                self.send_bytes(200, ASSETS.joinpath(filename).read_bytes(), content_type)
            else:
                self.send_json(404, {"error": "路径不存在"})

        def do_POST(self):
            if not self.allowed():
                return
            if self.headers.get_content_type() != "application/json":
                self.send_json(415, {"error": "需要 application/json"})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                # A user-selected offline export can contain the full indexed repository.
                if size <= 0 or (self.path != "/api/semantics/compare" and size > 1_000_000):
                    raise ValueError("无效的请求长度")
                body = json.loads(self.rfile.read(size))
                if not isinstance(body, dict):
                    raise ValueError("请求需要 JSON 对象")
                if self.path == "/api/notes":
                    result = discussion.note(body.get("targets"), body.get("text"))
                elif self.path == "/api/chat":
                    result = discussion.chat(body.get("targets"), body.get("thread"), body.get("question"),
                                             retry_message_id=body.get("retryMessageId"))
                elif self.path == "/api/semantics/compare":
                    from .claim_drift import compare_claim_evidence, read_previous_graph
                    previous = body.get("previous")
                    if not isinstance(previous, str):
                        raise ValueError("请选择旧版工作台 HTML 或架构 JSON 文件。")
                    result = compare_claim_evidence(read_previous_graph(previous), graph)
                elif self.path == "/api/models" and models:
                    try:
                        result = models.save(body)
                    except OSError as exc:
                        raise ValueError("无法写入本机模型设置，请检查用户数据目录的写入权限。") from exc
                elif self.path == "/api/semantics" and semantics:
                    self.generate(body)
                    return
                elif self.path == "/api/semantics/local" and semantics:
                    from .local_business import LocalSelectionError
                    try:
                        with models.lock if models else nullcontext():
                            store = models.store if models else semantics
                            result = store.generate_local(body.get("targets"), body.get("includeNeighbors", False))
                    except LocalSelectionError:
                        raise
                    except (ValueError, OSError, RuntimeError) as exc:
                        status, message = generation_error(exc, "local")
                        self.send_json(status, {"error": message})
                        return
                else:
                    self.send_json(404, {"error": "路径不存在"})
                    return
                self.send_json(200, result)
            except (ValueError, TypeError) as exc:
                self.send_json(400, {"error": str(exc)})
            except RuntimeError as exc:
                self.send_json(502, {"error": str(exc)})

        def generate(self, body):
            if not generation.start():
                self.send_json(409, {"error": "已有生产业务图正在生成。", "generationProgress": generation.snapshot()})
                return
            try:
                with models.lock if models else nullcontext():
                    generate = (models.store if models else semantics).generate
                    if "progress" in signature(generate).parameters:
                        result = generate(progress=generation.update)
                    else:
                        result = generate()
                    discussion.snapshot.set_semantics(result)
                    if models:
                        discussion.semantic_llm = models.semantic_info()
                    if body.get("includeCollaboration"):
                        result = {"semantics": result, "collaboration": graph["collaboration"],
                                  "overviewFacts": graph["overviewFacts"]}
            except Exception as exc:
                status, message = generation_error(exc, generation.snapshot()["stage"])
                progress = generation.finish(message, batch=getattr(exc, "semantic_batch", None))
                self.send_json(status, {"error": message, "generationProgress": progress})
                return
            generation.finish()
            self.send_json(200, result)

        def do_DELETE(self):
            if not self.allowed():
                return
            prefix = "/api/notes/"
            if self.path.startswith(prefix):
                try:
                    self.send_json(200, discussion.delete_note(self.path[len(prefix):]))
                except ValueError as exc:
                    self.send_json(400, {"error": str(exc)})
            else:
                self.send_json(404, {"error": "路径不存在"})

        def log_message(self, format, *args):
            logging.getLogger(__name__).debug(format, *args)

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)
