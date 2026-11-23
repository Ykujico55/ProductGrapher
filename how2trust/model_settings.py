"""Model settings edited from the local workbench."""

import json
import os
import tempfile
from pathlib import Path
from threading import Lock

from .llm import ModelClient
from .semantic_store import SemanticStore


STAGES = ("semantics", "chat")
FORM_FIELDS = ("provider", "protocol", "base_url", "model", "api_key_env")


class WorkbenchModels:
    def __init__(self, path, profiles, store, discussion):
        self.path = Path(path)
        self.profiles = {stage: profiles[stage] for stage in STAGES}
        self.store = store
        guide = store.graph.get("semantics")
        if guide and guide.get("generationFingerprint") != store.generation_key:
            self.store = SemanticStore(store.graph, store.client, store.requirements, store.cache_dir)
            self.store.replace_current = True
        self.discussion = discussion
        self.lock = Lock()

    def describe(self):
        result = {}
        for stage, client in (("semantics", self.store.client), ("chat", self.discussion.client)):
            result[stage] = dict(provider=client.provider, protocol=client.protocol,
                                 base_url=client.url, model=client.model, api_key_env=client.key_env,
                                 sessionKey=bool(client.session_key), useSessionKey=client.requires_session_key)
        return result

    def save(self, body):
        with self.lock:
            return self._save(body)

    def _save(self, body):
        target = body.get("stage")
        if target not in (*STAGES, "both"):
            raise ValueError("请选择业务解读或图上问答。")
        values = {k: body.get(k, "") for k in FORM_FIELDS}
        key = body.get("key", "")
        if not all(isinstance(v, str) for v in (*values.values(), key)):
            raise ValueError("模型设置字段需要填写文本。")
        values = {k: v.strip() for k, v in values.items()}
        if not values["model"] or not values["base_url"]:
            raise ValueError("请填写 API 地址和模型名。")
        if values["api_key_env"] and not values["api_key_env"].isidentifier():
            raise ValueError("密钥环境变量请填写变量名，例如 DEEPSEEK_API_KEY。")
        profiles = dict(self.profiles)
        clients = {}
        previous = self.discussion.client if target == "chat" else self.store.client
        keep_key = (body.get("keepKey") is True and previous.url == values["base_url"].rstrip("/")
                    and previous.protocol == values["protocol"])
        session_key = key or (previous.session_key if keep_key else None)
        for stage in (STAGES if target == "both" else (target,)):
            old = self.store.client if stage == "semantics" else self.discussion.client
            same_endpoint = (old.url, old.protocol) == (values["base_url"].rstrip("/"), values["protocol"])
            profiles[stage] = {**(profiles[stage] if same_endpoint else {}), **values}
            profiles[stage]["workbench_session_key"] = bool(session_key)
            clients[stage] = ModelClient(profiles[stage], session_key=session_key)
        next_store = self.store
        if "semantics" in clients:
            old = self.store
            candidate = SemanticStore(old.graph, clients["semantics"], old.requirements, old.cache_dir)
            if candidate.generation_key != old.generation_key or (
                    old.graph.get("semantics") and old.graph["semantics"].get("generationFingerprint") != candidate.generation_key):
                candidate.replace_current = True
                next_store = candidate
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=self.path.parent, delete=False) as temp:
            temp_path = Path(temp.name)
        try:
            temp_path.write_text(json.dumps(profiles, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temp_path, self.path)
        finally:
            temp_path.unlink(missing_ok=True)
        self.profiles = profiles
        if "chat" in clients:
            self.discussion.configure_model(clients["chat"], profiles["chat"])
        if "semantics" in clients:
            self.store = next_store
            self.store.client = clients["semantics"]
        self.discussion.semantic_llm = self.semantic_info()
        return {"settings": self.describe(), "llm": self.discussion.client.info(), "semanticLlm": self.semantic_info()}

    def semantic_info(self):
        guide = self.store.graph.get("semantics")
        return {**self.store.client.info(), "canRegenerate": bool(guide and self.store.replace_current and
                guide.get("generationFingerprint") != self.store.generation_key)}
