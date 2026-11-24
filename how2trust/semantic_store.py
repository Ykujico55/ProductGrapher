"""Reuse a frozen reading guide for the same source snapshot and model profile."""

import json
import os
import tempfile
from pathlib import Path
from threading import Lock

from .project import canonical, digest
from .collaboration import collaboration_graph
from .semantic_cache import SemanticRequestCache
from .semantics import PROMPT_VERSION, UPGRADABLE_OVERVIEWS, generate_semantics, generation_key, validate_semantics


def _save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    filename = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as output:
            filename = Path(output.name)
            output.write(canonical(data).encode("utf-8"))
        os.replace(filename, path)
    finally:
        if filename is not None:
            filename.unlink(missing_ok=True)


class SemanticStore:
    def __init__(self, graph, client, requirements, cache_dir, artifact_path=None, *, repository=None):
        self.graph = graph
        self.client = client
        self.requirements = requirements
        self.cache_dir = Path(cache_dir)
        self.repository = Path(repository).resolve() if repository is not None else None
        self.replace_current = False
        self.generation_key = generation_key(graph, client, requirements)
        self.path = Path(artifact_path) if artifact_path else self.cache_dir / (self.generation_key + ".json")
        self.cache_root = self.path.parent / ".how2trust" / "semantics" if artifact_path else self.cache_dir
        # Whole verified batches belong to this snapshot. Individual model
        # replies can cross snapshots after their exact inputs are reconstructed.
        self.checkpoint = self.cache_root / self.generation_key / "batches"
        self.local_views_dir = self.cache_dir / "local-views" / digest([str(self.repository or ""), graph["snapshot"]])
        self.lock = Lock()

    def _attach(self, guide):
        from .overview_facts import overview_facts
        collaboration = collaboration_graph(self.graph, guide)
        self.graph.update(semantics=guide, collaboration=collaboration, overviewFacts=overview_facts(self.graph, guide))

    def load(self):
        if self.path.is_file():
            data = json.loads(self.path.read_text(encoding="utf-8-sig"))
            self._attach(validate_semantics(self.graph, data))
        return self.graph.get("semantics")

    def load_local_views(self):
        views = []
        for path in sorted(self.local_views_dir.glob("*.json")):
            try:
                record = json.loads(path.read_bytes())
                view = record["view"]
                if (view["snapshot"] == self.graph["snapshot"] and record["fingerprint"] == digest(view)
                        and path.stem == digest(view["id"])):
                    views.append(view)
            except (KeyError, OSError, ValueError, TypeError):
                continue
        if views:
            self.graph["localBusinessViews"] = sorted(views, key=lambda v: v["id"])

    def generate_local(self, targets, include_neighbors=False):
        from .local_business import VERSION, generate_local_business
        with self.lock:
            cache = SemanticRequestCache(self.cache_dir, self.repository, self.client, "", "local-business-" + VERSION)
            view = generate_local_business(self.graph, self.client, targets, include_neighbors, cache=cache)
            path = self.local_views_dir / (digest(view["id"]) + ".json")
            _save_json(path, {"view": view, "fingerprint": digest(view)})
            existing = {item["id"]: item for item in self.graph.get("localBusinessViews", [])}
            existing[view["id"]] = view
            self.graph["localBusinessViews"] = [existing[key] for key in sorted(existing)]
            return view

    def generate(self, *, progress=None):
        with self.lock:
            if progress is not None:
                progress("prepare")
            if self.replace_current:
                existing = self.load() if self.path.is_file() else None
            else:
                existing = self.graph.get("semantics") or self.load()
            cache = SemanticRequestCache(self.cache_root, self.repository, self.client, self.requirements, PROMPT_VERSION)
            if existing:
                if existing.get("generationFingerprint") == self.generation_key:
                    if progress is not None:
                        progress("prepare", phase="cached")
                    return existing
                version = existing.get("promptVersion")
                if (version in UPGRADABLE_OVERVIEWS and existing.get("generationFingerprint") ==
                        generation_key(self.graph, self.client, self.requirements, version)):
                    from .semantics import upgrade_overview
                    artifact = upgrade_overview(self.graph, existing, self.client, self.requirements,
                                                progress=progress, checkpoint=self.checkpoint, request_cache=cache)
                else:
                    raise ValueError("无法确认已有讲解与本次供应商配置、需求和提示版本一致。请为新版本指定新的 --semantics 路径；重放旧版只需加载原文件。")
            else:
                artifact = generate_semantics(self.graph, self.client, self.requirements, checkpoint=self.checkpoint,
                                              progress=progress, request_cache=cache)
            if progress is not None:
                progress("saving")
            validate_semantics(self.graph, artifact)
            _save_json(self.path, artifact)
            self._attach(artifact)
            return artifact
