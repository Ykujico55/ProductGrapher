"""Live, in-memory progress of the workbench's current semantic generation."""

from threading import Lock


class GenerationProgress:
    def __init__(self):
        self._lock = Lock()
        self._run_id = 0
        self._reset("idle")

    def _reset(self, status):
        self._status = status
        self._stage = "prepare"
        self._total = 0
        self._active = {}
        self._completed = set()
        self._cached = set()
        self._cached_guide = False
        self._failures = {}
        self._failed_batch = None
        self._error = None

    def start(self):
        with self._lock:
            if self._status == "running":
                return False
            self._run_id += 1
            self._reset("running")
            return True

    def update(self, stage, *, batch=None, total=None, phase=None, files=()):
        with self._lock:
            if self._status != "running":
                return
            self._stage = stage
            if stage == "prepare" and phase == "cached":
                self._cached_guide = True
            if total is not None:
                self._total = total
            if stage != "batches" or batch is None:
                return
            if phase in {"reading", "reviewing"}:
                self._active[batch] = {"batch": batch, "phase": phase, "files": list(files)}
            elif phase in {"done", "cached"}:
                self._active.pop(batch, None)
                self._completed.add(batch)
                if phase == "cached":
                    self._cached.add(batch)
            elif phase == "failed":
                detail = self._active.pop(batch, {"batch": batch, "phase": "reading", "files": list(files)})
                self._failures[batch] = detail
                if self._failed_batch is None:
                    self._failed_batch = detail

    def finish(self, error=None, *, batch=None):
        with self._lock:
            self._status = "failed" if error else "succeeded"
            self._error = error
            if error and self._failures:
                self._failed_batch = self._failures.get(batch) or next(iter(self._failures.values()))
            self._active.clear()
            return self._snapshot()

    def snapshot(self):
        with self._lock:
            return self._snapshot()

    def _snapshot(self):
        def copy_batch(detail):
            return {**detail, "files": list(detail["files"])}

        result = {"runId": self._run_id, "status": self._status, "stage": self._stage,
                  "completed": len(self._completed), "total": self._total, "cached": len(self._cached),
                  "active": [copy_batch(self._active[k]) for k in sorted(self._active)]}
        if self._failed_batch:
            result["failedBatch"] = copy_batch(self._failed_batch)
        if self._error:
            result["error"] = self._error
        if self._cached_guide:
            result["cachedGuide"] = True
        return result
