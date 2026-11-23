"""Named provider profiles shared by graph explanations, scenario drafts and chat."""

import json
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


STAGES = ("semantics", "scenarios", "chat")
FIELDS = {"base_url", "model", "api_key_env", "protocol", "timeout", "context_chars", "max_tokens", "json_mode"}


class ModelRequestError(RuntimeError):
    """A provider error suitable for display without exposing the response body."""


class _ConfiguredEndpointOnly(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Source excerpts and the key must stay with the selected provider.
        return None


def profiles(path, overrides):
    if not path:
        config = {"base_url": os.getenv("HOW2TRUST_BASE_URL", ""), "model": os.getenv("HOW2TRUST_MODEL", ""),
                  "api_key_env": "HOW2TRUST_API_KEY", **overrides}
        return {stage: config for stage in STAGES}
    if overrides:
        raise ValueError("使用 --llm-config 时请在配置文件里指定各阶段模型，或改用单供应商命令行选项。")
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict) or set(data) - {"providers", "default", "stages"}:
        raise ValueError("LLM 配置需要 providers、可选的 default 和 stages。")
    providers = data.get("providers")
    stages = data.get("stages", {})
    if not isinstance(providers, dict) or not providers or not isinstance(stages, dict) or set(stages) - set(STAGES):
        raise ValueError("请定义具名 providers；可选阶段为 semantics、scenarios、chat。")
    for name, profile in providers.items():
        if (not name.strip() or not isinstance(profile, dict) or set(profile) - FIELDS
                or not profile.get("base_url") or not profile.get("model")):
            raise ValueError(f"供应商 {name} 需要 base_url、model 及受支持的配置字段；密钥使用 api_key_env。")
    selected = {}
    for stage in STAGES:
        name = stages.get(stage, data.get("default"))
        if name is None:
            selected[stage] = {}
        elif not isinstance(name, str) or name not in providers:
            raise ValueError(f"{stage} 没有对应的供应商配置。")
        else:
            selected[stage] = {**providers[name], "provider": name}
    return selected


class ModelClient:
    def __init__(self, config=None, *, session_key=None):
        config = config or {}
        self.session_key = session_key
        self.requires_session_key = bool(config.get("workbench_session_key"))
        self.url = str(config.get("base_url") or "").rstrip("/")
        self.model = str(config.get("model") or "")
        self.protocol = config.get("protocol", "openai")
        self.provider = str(config.get("provider") or (urlsplit(self.url).hostname if self.url else "") or "")
        self.key_env = str(config.get("api_key_env") or "")
        self.timeout = config.get("timeout", 120)
        self.max_tokens = config.get("max_tokens")
        default_json_mode = self.protocol == "openai" and urlsplit(self.url).hostname == "api.deepseek.com"
        self.json_mode = config.get("json_mode", default_json_mode)
        self.custom_json_mode = self.json_mode != default_json_mode
        if not isinstance(self.json_mode, bool):
            raise ValueError("json_mode 应为布尔值")
        if self.protocol not in ("openai", "anthropic"):
            raise ValueError("protocol 支持 openai（Chat Completions）或 anthropic（Messages）。")
        if bool(self.url) != bool(self.model):
            raise ValueError("--base-url 与 --model 需要同时配置")
        if self.url:
            url = urlsplit(self.url)
            if url.scheme not in ("https", "http") or not url.hostname or url.username or url.query or url.fragment:
                raise ValueError("模型地址应为 http(s) API 基础 URL。")
        if (type(self.timeout) is not int or self.timeout <= 0 or
                self.max_tokens is not None and (type(self.max_tokens) is not int or self.max_tokens <= 0)):
            raise ValueError("timeout 和 max_tokens 应为正数。")

    def info(self):
        ready = bool(self.url and self.model and (not self.requires_session_key or self.session_key))
        return {"configured": ready, "provider": self.provider, "model": self.model}

    def complete(self, messages):
        return self._request(messages, json_output=False)

    def complete_json(self, messages):
        if self.protocol == "openai" and self.json_mode:
            return self._request(messages, json_output=True)
        return self.complete(messages)

    def _request(self, messages, *, json_output):
        if not self.url:
            raise ValueError("此阶段尚未配置模型，请指定 --llm-config 或 --base-url 与 --model。")
        if self.requires_session_key and not self.session_key:
            raise ValueError("请在工作台的模型设置中重新输入本次服务的 API Key。")
        headers = {"Content-Type": "application/json"}
        key = self.session_key or (os.getenv(self.key_env) if self.key_env else None)
        if self.protocol == "anthropic":
            endpoint = self.url + "/messages"
            headers["anthropic-version"] = "2023-06-01"
            if key:
                headers["x-api-key"] = key
            payload = {"model": self.model, "max_tokens": self.max_tokens or 8192,
                       "messages": [m for m in messages if m["role"] != "system"]}
            system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
            if system:
                payload["system"] = system
        else:
            endpoint = self.url + "/chat/completions"
            if key:
                headers["Authorization"] = "Bearer " + key
            payload = {"model": self.model, "messages": messages, "stream": False}
            if json_output:
                payload["response_format"] = {"type": "json_object"}
            if self.max_tokens:
                payload["max_tokens"] = self.max_tokens
        req = Request(endpoint, data=json.dumps(payload, ensure_ascii=False).encode(), headers=headers, method="POST")
        try:
            with build_opener(_ConfiguredEndpointOnly).open(req, timeout=self.timeout) as response:
                raw = response.read(2_000_001)
            if len(raw) > 2_000_000:
                raise ModelRequestError("模型回复超过大小限制，请减少模型的最大输出长度后重试。")
            data = json.loads(raw)
            if self.protocol == "anthropic":
                content = "\n".join(b["text"] for b in data["content"] if b.get("type") == "text")
            else:
                content = data["choices"][0]["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise ValueError()
            return content.strip()
        except HTTPError as exc:
            guidance = {
                401: "鉴权失败，请在模型设置中检查 API Key 或密钥环境变量。",
                402: "请检查供应商账户的可用余额。",
                403: "访问被拒绝，请检查 API Key 是否有权使用所选模型。",
                404: "未找到模型或接口，请检查 API 基础地址、协议和模型名。",
                429: "请求受到限制，请检查供应商的调用额度，或稍后重试。",
            }.get(exc.code, "请检查模型服务的状态后重试。" if exc.code >= 500 else "请检查模型、地址和请求配置。")
            raise ModelRequestError(f"模型服务 {self.provider} 返回 HTTP {exc.code}。{guidance}") from exc
        except (URLError, OSError) as exc:
            raise ModelRequestError(f"无法连接模型服务 {self.provider} 或请求超时，请检查服务是否可达后重试。") from exc
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ModelRequestError(f"供应商 {self.provider} 未返回与 {self.protocol} 协议兼容的文本，请检查接口协议和模型名。") from exc
