import contextlib
import io
import json
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.request import urlopen

from how2trust.__main__ import main
from how2trust.acceptance import load_contract, run_check
from how2trust.discussion import Discussion, Evidence, Snapshot
from how2trust.project import build_graph
from how2trust.review import build_report, graph_for_report
from how2trust.review_html import render_report
from how2trust.web import make_server


SAMPLE = Path(__file__).resolve().parent / "fixtures" / "delivery"


class DeliveryReviewTests(unittest.TestCase):
    def test_required_scenario_names_and_source_evidence_follow_the_delivery(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            before, after = root / "before", root / "after"
            for directory, state in [(before, "shipped"), (after, "partial")]:
                directory.mkdir()
                (directory / "shop.py").write_text(f"def ship(): return '{state}'\n", encoding="utf-8")
            scenario = {"name": "first warehouse", "given": "two warehouses", "when": "ship one", "then": "partial"}
            check = {"name": scenario["name"], "source": "shop.py::ship",
                     "argv": ["{python}", "-c", "import shop, json; print(json.dumps({'state':shop.ship()}))"],
                     "expect": {"json": {"/state": "partial"}}}
            req = {"id":"R1", "text":"allow split shipment", "scenarios":[scenario, "last warehouse"], "checks":[check]}
            spec = root / "requirements.json"
            spec.write_text(json.dumps({"task": "ship", "requirements": [req]}))
            report = build_report(after, spec, base_dir=before, run=True)
            row = report["requirements"][0]
            self.assertEqual(row["status"], "partial")
            self.assertEqual(row["scenarios"][0], scenario)
            self.assertEqual(row["missingScenarios"], ["last warehouse"])
            result = row["checks"][0]
            self.assertEqual(result["sourceReference"]["line"], 1)
            self.assertIn("'partial'", result["sourceReference"]["snippet"])
            self.assertEqual(result["baseline"]["observations"][0]["actual"], "shipped")
            self.assertEqual(result["observations"][0]["actual"], "partial")
            self.assertIn(b'"missingScenarios":["last warehouse"]', render_report(report))

    def test_declared_writes_and_unexplained_writes_reach_report_and_discussion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            before, after = root / "before", root / "after"
            before.mkdir()
            after.mkdir()
            (before / "orders.py").write_text("def cancel(): return True\n")
            (after / "orders.py").write_text(
                "def cancel():\n    open('orders.json', 'w').close()\n"
                "    open('telemetry.log', 'a').close()\n    return True\n")
            spec = root / "contract.json"
            spec.write_text(json.dumps({"task":"cancel", "requirements":[
                {"id":"R", "text":"store orders", "scope":["orders.py::cancel"], "writes":["orders.json"]}]}))
            report = build_report(after, spec, base_dir=before)
            effects = [row for row in report["scope"] if row.get("kind") == "file-write"]
            self.assertEqual([(r["target"], r["line"], r["status"]) for r in effects],
                             [("telemetry.log", 3, "unmapped")])
            self.assertEqual(report["requirements"][0]["writes"], ["orders.json"])
            html = render_report(report)
            self.assertIn(b'"writes":["orders.json"]', html)
            evidence = Evidence(Snapshot(graph_for_report(after, report), report))
            self.assertIn("orders.json", evidence.lookup({"lookup":"review", "query":"writes"}, [], 1000))
            self.assertIn("telemetry.log", evidence.lookup({"lookup":"review", "query":"file-write"}, [], 2000))

    def test_three_questions_and_two_revisions(self):
        report = build_report(SAMPLE / "submission", SAMPLE / "contract.json",
                              base_dir=SAMPLE / "baseline", run=True)
        self.assertEqual([r["status"] for r in report["requirements"]], ["passed", "failed", "unverified"])
        first = report["requirements"][0]["checks"][0]
        self.assertEqual(first["baseline"]["status"], "failed")
        self.assertEqual(first["baseline"]["output"].strip(), "accepted pending")
        self.assertEqual(first["output"].strip(), "accepted cancelled")
        self.assertEqual([(f["rule"], f["status"]) for f in report["defenses"]], [("swallowed-exception", "review")])
        by_symbol = {f["symbol"]: f["status"] for f in report["scope"]}
        self.assertEqual(by_symbol, {"cancel_order": "mapped", "export_ledger": "forbidden", "count_cancelled": "unmapped"})
        self.assertEqual(report["codeSnapshot"], build_graph(SAMPLE / "submission")["snapshot"])
        html = render_report(report)
        self.assertIn(b"report-data", html)
        self.assertNotIn(b'src="/assets/', html)

    def test_no_implicit_test_execution_and_explicit_prohibition(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            spec = json.loads((SAMPLE / "contract.json").read_text(encoding="utf-8"))
            spec["forbiddenDefenses"] = ["swallowed-exception"]
            contract = tmp / "contract.json"
            contract.write_text(json.dumps(spec), encoding="utf-8")
            report = build_report(SAMPLE / "submission", contract, base_dir=SAMPLE / "baseline")
            self.assertEqual(report["defenses"][0]["status"], "forbidden")
            self.assertTrue(all(r["status"] == "unverified" for r in report["requirements"]))
            self.assertNotIn("baseline", report["requirements"][0]["checks"][0])

    def test_markdown_contract_retains_qualifiers(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "task.md")
            path.write_text("Original task\n- Cancel order\n  only when pending\n- Reject shipped", encoding="utf-8")
            spec = load_contract(path)
            self.assertEqual([req["text"] for req in spec["requirements"]],
                             ["Original task", "Cancel order\nonly when pending", "Reject shipped"])

    def test_probe_in_worktree_imports_frozen_revision(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            before, after = root / "before", root / "after"
            before.mkdir()
            after.mkdir()
            for directory, value in ((before, 0), (after, 1)):
                (directory / "logic.py").write_text(f"value = {value}")
            (after / "probe.py").write_text("from logic import value\nprint(value)\n")
            check = {"name": "read value", "argv": ["{python}", "{spec_dir}/probe.py"], "expect": {"stdout": "1"}}
            spec = {"task": "read value", "requirements": [{"id": "R", "text": "returns 1", "checks": [check]}]}
            path = after / "accept.json"
            path.write_text(json.dumps(spec))
            report = build_report(after, path, base_dir=before, run=True)
            check = report["requirements"][0]["checks"][0]
            self.assertEqual((check["status"], check["baseline"]["output"].strip()), ("passed", "0"))
            self.assertTrue(any("accept.json" in d for d in report["diagnostics"]))
            (after / "settings.ini").write_text("enabled = false")
            with self.assertRaisesRegex(ValueError, "文件与验收报告不同"):
                graph_for_report(after, report)

    def test_head_checks_the_named_commit_even_when_worktree_disagrees(self):
        with tempfile.TemporaryDirectory() as t:
            temp = Path(t)
            root = temp / "repo"
            root.mkdir()

            def git(*args):
                return subprocess.check_output(["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                                                "-c", "commit.gpgsign=false", *args], cwd=root)

            git("init", "-q")
            for v in ("before", "delivered"):
                (root / "logic.py").write_bytes(f"print('{v}')\n".encode())
                git("add", "logic.py")
                git("commit", "-qm", v)
            (root / "logic.py").write_text("print('dirty')\n")
            contract = temp / "accept.json"
            contract.write_text(json.dumps({"task": "print delivered", "requirements": [
                {"id": "R", "text": "print delivered", "checks": [{"name": "observe",
                 "argv": ["{python}", "logic.py"], "expect": {"stdout": "delivered"}}]}]}))
            report = build_report(root, contract, base="HEAD~1", head="HEAD", run=True)
            check = report["requirements"][0]["checks"][0]
            self.assertEqual(check["baseline"]["output"].strip(), "before")
            self.assertEqual(check["output"].strip(), "delivered")
            self.assertEqual(check["status"], "passed")
            self.assertIn("dirty", (root / "logic.py").read_text())
            self.assertIn("delivered", graph_for_report(root, report)["files"]["logic.py"])

    def test_empty_runner_and_failed_start_never_pass(self):
        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            for output in ("Ran 0 tests in 0.00s", "Ran 1 test in 0.0s\n\nOK (skipped=1)"):
                check = {"name": "empty", "argv": ["{python}", "-c", f"print({output!r})"], "expect":{"exit": 0}}
                self.assertEqual(run_check(check, work, work)["status"], "unverified")
            check["argv"] = [str(work / "not-installed")]
            self.assertEqual(run_check(check, work, work)["status"], "error")

    def test_report_can_be_regenerated_inside_reviewed_directory(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            before, work = tmp / "before", tmp / "work"
            before.mkdir()
            work.mkdir()
            (work / "code.py").write_text("x = 1\n")
            spec = tmp / "task.md"
            spec.write_text("Check constant x")
            outputs = [work / "delivery.html", work / "delivery.json"]
            first = build_report(work, spec, base_dir=before, outputs=outputs)
            outputs[0].write_bytes(render_report(first))
            outputs[1].write_text(json.dumps(first))
            second = build_report(work, spec, base_dir=before, outputs=outputs)
            self.assertEqual(first["fingerprint"], second["fingerprint"])
            self.assertEqual(graph_for_report(work, first)["snapshot"], first["codeSnapshot"])
            outputs[1].write_text('{"real": "settings"}')
            with self.assertRaisesRegex(ValueError, "现有仓库文件"):
                build_report(work, spec, base_dir=before, outputs=outputs)

    def test_cli_report_server_and_model_lookup(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            with contextlib.redirect_stdout(io.StringIO()):
                result = main(["review", str(SAMPLE / "submission"), "--base-dir", str(SAMPLE / "baseline"),
                               "--spec", str(SAMPLE / "contract.json"), "-o", str(tmp / "report.html"),
                               "--json", str(tmp / "report.json")])
            self.assertEqual(result, 0)
            report = json.loads((tmp / "report.json").read_bytes())
            graph = build_graph(SAMPLE / "submission")
            discussion = Discussion(tmp / "state.db", graph, review=report)
            answer = Evidence(discussion.snapshot).lookup({"lookup": "review", "query": "R1"}, [], 5000)
            self.assertIn("unverified", answer)
            with make_server(graph, discussion, 0, review=report) as server:
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    base = f"http://127.0.0.1:{server.server_port}"
                    with urlopen(base + "/api/graph") as resp:
                        self.assertTrue(json.load(resp)["hasReview"])
                    with urlopen(base + "/review") as resp:
                        self.assertIn(b'"graphUrl":"/"', resp.read())
                finally:
                    server.shutdown()
                    thread.join()
