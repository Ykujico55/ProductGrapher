"""Reuse a frozen reading guide for the same source snapshot and model profile."""

import json
import os
import tempfile
from pathlib import Path
from threading import Lock

from .project import canonical
from .collaboration import collaboration_graph
from .semantics import GROUP_VERSION, UPGRADABLE_OVERVIEWS, generate_semantics, generation_key, validate_semantics


class SemanticStore:
    def __init__(self, graph, client, requirements, cache_dir, artifact_path=None):
        self.graph = graph
        self.client = client
        self.requirements = requirements
        self.cache_dir = cache_dir
        self.replace_current = False
        self.generation_key = generation_key(graph, client, requirements)
        self.path = Path(artifact_path) if artifact_path else cache_dir / (self.generation_key + ".json")
        # A change confined to overview selection must not invalidate verified
        # source batches. The per-batch file is also keyed by its exact input.
        batch_key = generation_key(graph, client, requirements, GROUP_VERSION)
        shared = Path(cache_dir) / batch_key / "batches"
        self.checkpoint = shared
        if artifact_path and not shared.is_dir():
            self.checkpoint = self.path.parent / ".how2trust" / "semantics" / batch_key / "batches"
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

    def generate(self, *, progress=None):
        with self.lock:
            if progress is not None:
                progress("prepare")
            if self.replace_current:
                existing = self.load() if self.path.is_file() else None
            else:
                existing = self.graph.get("semantics") or self.load()
            if existing:
                if existing.get("generationFingerprint") == self.generation_key:
                    if progress is not None:
                        progress("prepare", phase="cached")
                    return existing
                version = existing.get("promptVersion")
                if (version in UPGRADABLE_OVERVIEWS and existing.get("generationFingerprint") ==
                        generation_key(self.graph, self.client, self.requirements, version)):
                    from .semantics import upgrade_overview
                    artifact = upgrade_overview(self.graph, existing, self.client, self.requirements, progress=progress)
                else:
                    raise ValueError("无法确认已有讲解与本次供应商配置、需求和提示版本一致。请为新版本指定新的 --semantics 路径；重放旧版只需加载原文件。")
            else:
                artifact = generate_semantics(self.graph, self.client, self.requirements, checkpoint=self.checkpoint,
                                              progress=progress)
            if progress is not None:
                progress("saving")
            validate_semantics(self.graph, artifact)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=self.path.parent, delete=False) as output:
                filename = output.name
            try:
                Path(filename).write_bytes(canonical(artifact).encode())
                os.replace(filename, self.path)
            finally:
                Path(filename).unlink(missing_ok=True)
            self._attach(artifact)
            return artifact
