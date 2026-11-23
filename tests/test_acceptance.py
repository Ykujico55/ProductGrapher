import json
import tempfile
import unittest
from pathlib import Path

from how2trust.acceptance import load_contract, requirement_status, run_check


class AcceptanceTests(unittest.TestCase):
    def test_optional_method_trace_follows_python_child_independently_of_its_output(self):
        source = ("class Other:\n    def run(self): return 'other'\n"
                  "class Ship:\n    def run(self):\n        return 'partial'\n")
        child = "import logic; logic.Other().run(); print(logic.Ship().run())"
        parent = f"import subprocess,sys; subprocess.run([sys.executable, '-c', {child!r}], check=True)"
        check = {"name": "first shipment", "argv": ["{python}", "-c", parent], "expect": {"stdout": "partial"},
                 "source": "logic.py::Ship.run"}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "logic.py").write_text(source)
            plain = run_check(check, root, root)
            self.assertEqual(plain["status"], "passed")
            self.assertNotIn("traceEvidence", plain)
            result = run_check({**check, "trace": True}, root, root)
            self.assertEqual(result["status"], plain["status"])
            trace = result["traceEvidence"]
            self.assertEqual((trace["status"], trace["interpreters"], trace["hits"]), ("observed", 2, 1))
            self.assertEqual(trace["executed"], [5])
            self.assertEqual(trace["sourceLines"], [{"line": 5, "text": "        return 'partial'"}])
            for args, source_ref, expected in [
                (["-c", "print('partial')"], check["source"], "not_observed"),
                (["-S", "-c", "print('partial')"], check["source"], "unavailable"),
                (["-c", "print('partial')"], "logic.py::Missing.run", "unavailable"),
            ]:
                result = run_check({**check, "argv": ["{python}", *args], "source": source_ref, "trace": True}, root, root)
                self.assertEqual(result["status"], "passed")
                self.assertEqual(result["traceEvidence"]["status"], expected)
            self.assertEqual([p.name for p in root.iterdir()], ["logic.py"])

    def test_exit_only_and_broad_suite_are_execution_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            basic = {"name": "partial shipment", "argv": ["{python}", "-c", "pass"], "expect": {"exit": 0}}
            check = run_check(basic, root, root)
            self.assertEqual((check["status"], requirement_status([check])), ("passed", "unverified"))
            self.assertEqual(requirement_status([run_check({**basic, "verifies": "exit"}, root, root)]), "passed")
            (root / "test_ship.py").write_text("import unittest\nclass Ship(unittest.TestCase):\n    def test_partial(self): pass\n")
            suite = {**basic, "argv": ["{python}", "-m", "unittest", "discover"]}
            self.assertEqual(requirement_status([run_check(suite, root, root)]), "unverified")
            self.assertEqual(requirement_status([run_check({**suite, "verifies": "tests"}, root, root)]), "passed")
            no_runner = {**basic, "argv": ["{python}", "-c", "print('=== 1 passed in 0.02s ===')"],
                         "verifies": "tests"}
            self.assertEqual(requirement_status([run_check(no_runner, root, root)]), "unverified")
            failing_process = {**basic, "argv": ["{python}", "-c", "raise SystemExit(1)"]}
            failed_exit = run_check(failing_process, root, root)
            self.assertEqual((failed_exit["status"], requirement_status([failed_exit])), ("failed", "unverified"))
            named = {**basic, "argv": ["{python}", "-m", "unittest", "test_ship.Ship.test_partial"]}
            self.assertEqual(requirement_status([run_check(named, root, root)]), "passed")

    def test_json_pointer_observations_keep_null_missing_and_mismatch_separate(self):
        payload = {"a/b": {"~": [True, None]}, "state": "shipped"}
        rows = {"/state": "partial", "/a~1b/~0/0": 1, "/a~1b/~0/1": None, "/missing": None, "": payload}
        check = {"name": "read state", "argv": ["{python}", "-c", f"print({json.dumps(payload)!r})"],
                 "expect": {"json": rows}}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            result = run_check(check, root, root)
            self.assertEqual(result["status"], "failed")
            self.assertEqual([o["status"] for o in result["observations"]], ["failed", "failed", "passed", "failed", "passed"])
            self.assertEqual(result["observations"][0]["actual"], "shipped")
            self.assertTrue(result["observations"][3]["missing"])
            self.assertNotIn("actual", result["observations"][3])
            bad_json = run_check({**check, "argv": ["{python}", "-c", "print('bad')"]}, root, root)
            self.assertEqual(bad_json["status"], "failed")
            self.assertEqual(bad_json["observations"][0]["status"], "unverified")

    def test_prose_and_list_items_remain_in_original_order(self):
        source = ("Shipped orders retain their state.\n\n"
                  "- Allow cancellation\n\n  of pending orders.\n"
                  "- Keep the order ID.\n\nAlso preserve the original currency.\n")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "task.md")
            path.write_text(source, encoding="utf-8")
            contract = load_contract(path)
        self.assertEqual(contract["task"], source.strip())
        self.assertEqual([r["text"] for r in contract["requirements"]],
                         ["Shipped orders retain their state.", "Allow cancellation\nof pending orders.",
                          "Keep the order ID.", "Also preserve the original currency."])

    def test_unittest_reports_unverified_and_partial_coverage(self):
        cases = ("import unittest\nclass Discount(unittest.TestCase):\n"
                 "    @unittest.expectedFailure\n"
                 "    def test_discount(self):\n"
                 "        import sys\n"
                 "        print('x' * 130000, file=sys.stderr)\n"
                 "        self.assertEqual(101, 90)\n"
                 "    @unittest.skip('pending integration')\n"
                 "    def test_remote(self): pass\n")
        check = {"name": "discount", "argv": ["{python}", "-m", "unittest", "test_discount"], "expect": {"exit": 0}}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "test_discount.py"
            path.write_text(cases, encoding="utf-8")
            result = run_check(check, root, root)
            self.assertEqual(result["actualExit"], 0)
            self.assertTrue(result["outputTruncated"])
            self.assertEqual(result["status"], "unverified")
            self.assertEqual(result["testSummary"], {"runner": "unittest", "run": 2, "passed": 0,
                                                     "skipped": 1, "expectedFailures": 1})
            result = run_check({**check, "expect": {"exit": 0, "stdout": ""}}, root, root)
            self.assertEqual(result["status"], "unverified")
            path.write_text(cases + "    def test_valid(self): self.assertEqual(1, 1)\n", encoding="utf-8")
            result = run_check(check, root, root)
            self.assertEqual(result["status"], "partial")
            self.assertEqual(requirement_status([result]), "partial")
            self.assertEqual(result["testSummary"]["passed"], 1)
            self.assertIn("部分验证", result["detail"])
            with path.open("a", encoding="utf-8") as source:
                source.write("    def test_broken(self): self.fail('wrong state')\n")
            self.assertEqual(run_check(check, root, root)["status"], "failed")

    def test_negative_runner_evidence_is_distinct_from_the_probe_checking_its_output(self):
        script = ("import sys\nprint('Ran 1 test in 0.0s\\n\\nOK (expected failures=1)', file=sys.stderr)\n"
                  "print('observed_expected_failure')")
        check = {"name": "check runner integration", "argv": ["{python}", "-c", script],
                 "expect": {"stdout": "observed_expected_failure"}}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = run_check(check, root, root)
        self.assertEqual(result["status"], "passed")

    def test_pytest_summary_requires_a_passing_case(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for summary, status, passed in (("1 xfailed", "unverified", 0),
                                            ("2 skipped, 1 xfailed, 1 warning", "unverified", 0),
                                            ("1 passed, 2 skipped", "partial", 1),
                                            ("1 passed, 1 xfailed", "partial", 1),
                                            ("1 passed", "passed", 1)):
                with self.subTest(summary=summary):
                    argv = ["{python}", "-c", f"print('=== {summary} in 0.02s ===')"]
                    result = run_check({"name": "suite", "argv": argv, "expect": {"exit": 0}}, root, root)
                    self.assertEqual(result["status"], status)
                    self.assertEqual(result["testSummary"]["passed"], passed)

    def test_partial_check_rolls_up_to_requirement(self):
        for other in ("passed", "partial", "unverified", "error", "failed"):
            with self.subTest(other=other):
                expected = other if other in {"error", "failed"} else "partial"
                self.assertEqual(requirement_status([{"status": "partial"}, {"status": other}]), expected)

    def test_large_stdout_comparison_includes_end_and_crosses_block_boundaries(self):
        script = ("import sys\n"
                  "sys.stdout.buffer.write(b'x' * 65535 + '完成'.encode() + b'y' * 65530 + b'\\r\\nORDER_OK\\r\\n')")
        check = {"name": "marker", "argv": ["{python}", "-c", script]}
        complete = "x" * 65535 + "完成" + "y" * 65530 + "\nORDER_OK\n"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for expected, status in (({"contains": "x完成y"}, "passed"), ({"contains": "y\r\nORDER_OK"}, "passed"),
                                     ({"contains": "MISSING"}, "failed"), ({"stdout": "ORDER_OK"}, "failed"),
                                     ({"stdout": complete.rstrip("\n"), "contains": "ORDER_OK"}, "passed"),
                                     ({"stdout": complete + "\n"}, "failed")):
                with self.subTest(expect=list(expected), status=status):
                    result = run_check({**check, "expect": expected}, root, root)
                    self.assertTrue(result["outputTruncated"])
                    self.assertNotIn("ORDER_OK", result["output"])
                    self.assertEqual(result["status"], status)

    def test_filesystem_write_allowance_is_optional_and_typed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "spec.json")
            req = {"id": "R1", "text": "Save receipt"}
            contract = {"task": "Save receipt", "requirements": [req]}
            for value in (None, ["receipt.json"], [""]):
                if value is not None:
                    req["writes"] = value
                path.write_text(json.dumps(contract), encoding="utf-8")
                if value == [""]:
                    with self.assertRaisesRegex(ValueError, "writes"):
                        load_contract(path)
                else:
                    self.assertEqual(load_contract(path)["requirements"][0].get("writes", []), value or [])
