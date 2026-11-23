import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Event, Thread
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from how2trust.generation_progress import GenerationProgress
from how2trust.llm import ModelRequestError
from how2trust.web import make_server


@contextmanager
def serve(generate):
    client = SimpleNamespace(info=lambda: {})
    store = SimpleNamespace(client=client, generate=generate)
    discussion = SimpleNamespace(state=lambda: {}, snapshot=SimpleNamespace(set_semantics=lambda guide: None))
    with make_server({}, discussion, 0, semantics=store) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}/api/"

        def request(endpoint, body=None):
            req = Request(base + endpoint, data=None if body is None else json.dumps(body).encode(),
                          headers={"Content-Type": "application/json"})
            try:
                response = urlopen(req, timeout=5)
            except HTTPError as exc:
                response = exc
            with response:
                return response.status, json.load(response)

        try:
            yield request
        finally:
            server.shutdown()
            thread.join()


class GenerationHttpTests(unittest.TestCase):
    def test_observe_in_flight_error_then_retry(self):
        reviewing, resume = Event(), Event()
        attempts = []

        def generate(progress):
            attempts.append(1)
            progress("batches", total=2)
            progress("batches", batch=2, phase="cached", files=["store.py"])
            progress("batches", batch=1, phase="reading", files=["api.py"])
            progress("batches", batch=1, phase="reviewing", files=["api.py"])
            if len(attempts) == 1:
                reviewing.set()
                if not resume.wait(5):
                    raise AssertionError("polling failed to release the test generator")
                progress("batches", batch=1, phase="failed", files=["api.py"])
                error = ModelRequestError("模型服务返回 HTTP 429，请稍后重试。")
                error.semantic_batch = 1
                raise error
            progress("batches", batch=1, phase="done")
            progress("saving")
            return {"items": []}

        with serve(generate) as request, ThreadPoolExecutor(max_workers=1) as pool:
            self.assertEqual(request("semantics/progress")[1]["status"], "idle")
            first = pool.submit(request, "semantics", {})
            try:
                self.assertTrue(reviewing.wait(3))
                state = request("semantics/progress")[1]
                self.assertEqual(state, request("state")[1]["generationProgress"])
                self.assertEqual((state["runId"], state["status"], state["stage"]), (1, "running", "batches"))
                self.assertEqual((state["completed"], state["total"], state["cached"]), (1, 2, 1))
                self.assertEqual(state["active"], [{"batch": 1, "phase": "reviewing", "files": ["api.py"]}])
                self.assertEqual(request("semantics", {})[0], 409)
                self.assertEqual(len(attempts), 1)
            finally:
                resume.set()
            code, body = first.result(timeout=3)
            self.assertEqual(code, 502)
            failed = request("semantics/progress")[1]
            self.assertEqual(failed, body["generationProgress"])
            self.assertEqual(failed["error"], body["error"])
            self.assertIn("429", failed["error"])
            self.assertEqual(failed["failedBatch"], state["active"][0])
            self.assertEqual(failed["active"], [])
            self.assertEqual(failed["status"], "failed")
            self.assertEqual(request("semantics", {}), (200, {"items": []}))
            done = request("state")[1]["generationProgress"]
            self.assertEqual((done["runId"], done["status"], done["completed"]), (2, "succeeded", 2))
            self.assertNotIn("failedBatch", done)
            self.assertNotIn("error", done)

    def test_legacy_generate_and_unexpected_error(self):
        def generate():
            raise RuntimeError("raw provider diagnostic with a secret")

        with serve(generate) as request:
            code, body = request("semantics", {})
            self.assertEqual(code, 500)
            self.assertNotIn("secret", json.dumps(body))
            self.assertEqual(body["generationProgress"]["stage"], "prepare")

    def test_file_write_failure(self):
        def generate(progress):
            progress("saving")
            raise PermissionError("unreadable path")

        with serve(generate) as request:
            code, body = request("semantics", {})
            self.assertEqual(code, 500)
            self.assertIn("权限", body["error"])
            self.assertEqual(body["generationProgress"]["stage"], "saving")

    def test_parallel_failures_report_the_batch_of_the_raised_exception(self):
        status = GenerationProgress()
        status.start()
        for n in (2, 1):
            status.update("batches", batch=n, total=2, phase="reading", files=[f"{n}.py"])
            status.update("batches", batch=n, phase="failed")
            self.assertEqual(status.snapshot()["failedBatch"]["batch"], 2)
        state = status.finish("provider unavailable", batch=1)
        self.assertEqual(state["failedBatch"], {"batch": 1, "files": ["1.py"], "phase": "reading"})
