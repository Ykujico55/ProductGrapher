import difflib
import textwrap
import unittest

from how2trust.findings import assess


def change(path, before, after):
    before = textwrap.dedent(before).lstrip("\n") if before is not None else None
    after = textwrap.dedent(after).lstrip("\n") if after is not None else None
    matcher = difflib.SequenceMatcher(None, (before or "").splitlines(), (after or "").splitlines())
    added, removed = [], []
    for tag, a, b, c, d in matcher.get_opcodes():
        if tag != "equal":
            removed.extend(range(a + 1, b + 1))
            added.extend(range(c + 1, d + 1))
    return {"file": path, "before": before, "after": after, "addedLines": added, "removedLines": removed,
            "status": "added" if before is None else "deleted" if after is None else "modified", "binary": False}


def requirement(ident, *scope):
    return {"id": ident, "text": ident, "scope": list(scope)}


class FindingsTests(unittest.TestCase):
    def test_attributes_multiple_edits_and_deletions_to_the_right_symbols(self):
        before = """
            class Service:
                state = "off"
                def cancel(self):
                    return "no"
                def legacy(self):
                    return False
            def untouched():
                return 0
        """
        after = """
            class Service:
                state = "on"
                def cancel(self):
                    return "yes"
                def queue(self):
                    return 100
            def untouched():
                return 0
        """
        report = assess([change("src/service.py", before, after)],
                        [requirement("R1", "src/service.py::Service.cancel")],
                        ["**/service.py::Service.queue"])
        units = {s["symbol"]: s for s in report["scope"]}
        self.assertEqual(set(units), {"Service", "Service.cancel", "Service.legacy", "Service.queue"})
        self.assertEqual((units["Service.cancel"]["status"], units["Service.cancel"]["mapping"]), ("mapped", "symbol"))
        self.assertEqual((units["Service.legacy"]["change"], units["Service.legacy"]["side"]), ("deleted", "before"))
        self.assertEqual((units["Service.queue"]["status"], units["Service.queue"]["line"]), ("forbidden", 5))
        self.assertEqual(units["Service"]["status"], "unmapped")
        self.assertEqual([l["id"] for l in report["requirements"]["R1"]], [units["Service.cancel"]["id"]])
        self.assertTrue(all(s["afterRanges"] or s["beforeRanges"] for s in report["scope"]))

    def test_globs_match_anchored_path_segments_and_class_scope(self):
        changes = [
            change("app.py", None, "def ready(): return True"),
            change("pkg/deep/code.py", None, "class Handler:\n    def dispatch(self): pass"),
            change("pkg/data.txt", "keep\nold\n", None),
        ]
        report = assess(changes, [
            requirement("R1", "*.py"), requirement("R2", "pkg/**/*.py::Handler"),
            requirement("R3", "pkg/*"), requirement("R4", "**/app.py"),
        ], [])
        per = {(s["file"], s["symbol"]): s for s in report["scope"]}
        self.assertEqual(per[("app.py", "ready")]["requirementIds"], ["R1", "R4"])
        self.assertEqual(per[("pkg/deep/code.py", "Handler.dispatch")]["requirementIds"], ["R2"])
        self.assertEqual(per[("pkg/data.txt", "")]["mapping"], "file")
        self.assertEqual(per[("pkg/data.txt", "")]["snippet"], "keep\nold")
        self.assertEqual(per[("pkg/data.txt", "")]["side"], "before")

    def test_detects_retry_swallow_fallback_and_repeated_guard_with_coordinates(self):
        current = """
            def cancel(db, order):
                if order is None:
                    return False
                if order is None:
                    return False
                for attempt in range(3):
                    try:
                        return db.save(order)
                    except Exception:
                        continue
                try:
                    return db.lookup(order)
                except KeyError:
                    return {}
        """
        report = assess([change("shop.py", "", current)], [requirement("R", "shop.py::cancel")], [])
        facts = {d["rule"]: d for d in report["defenses"]}
        self.assertEqual(set(facts), {"retry", "swallowed-exception", "duplicate-guard", "fallback"})
        self.assertEqual([facts[k]["line"] for k in ("duplicate-guard", "retry", "swallowed-exception", "fallback")],
                         [4, 6, 9, 13])
        for item in facts.values():
            self.assertEqual(item["status"], "review")
            self.assertEqual(item["requirementIds"], ["R"])
            self.assertTrue(item["question"] and item["cost"] and item["snippet"])

    def test_untouched_or_moved_defenses_do_not_get_attributed_to_delivery(self):
        before = """
            def save():
                try:
                    submit()
                except Exception:
                    pass
            def retrieve():
                return 1
        """
        after = before.replace("submit()", "submit_order()").replace("return 1", "return 2")
        c = change("service.py", before, "# delivery\n" + textwrap.dedent(after))
        self.assertEqual(assess([c], [], [])["defenses"], [])

    def test_deleted_reraise_reveals_new_fallback(self):
        before = "try:\n    save()\nexcept Exception:\n    pass\n    raise\n"
        c = change("app.py", before, before.removesuffix("    raise\n"))
        report = assess([c], [], [])
        self.assertFalse(c["addedLines"])
        self.assertEqual(report["defenses"][0]["rule"], "swallowed-exception")
        self.assertEqual(report["scope"][0]["side"], "before")

    def test_invalid_python_and_binary_remain_accounted_for(self):
        malformed = change("app.py", "def ok(): return 1", "def (")
        binary = {**change("asset.png", None, None), "binary": True, "status": "added"}
        report = assess([binary, malformed], [], ["*.png"])
        self.assertTrue(any("app.py:1" in text for text in report["diagnostics"]))
        self.assertEqual(next(s for s in report["scope"] if s["file"] == "asset.png")["status"], "forbidden")
        self.assertTrue(any(s["file"] == "app.py" for s in report["scope"]))
        self.assertFalse(report["defenses"])

    def test_single_iteration_and_guard_with_side_effect_do_not_trigger(self):
        after = """
            def read():
                if poll():
                    return 1
                if poll():
                    return 2
                for i in range(0, 1):
                    try:
                        return fetch()
                    except TimeoutError:
                        continue
        """
        self.assertEqual(assess([change("app.py", "", after)], [], [])["defenses"], [])

    def test_spacing_between_definitions_stays_in_diff_without_inventing_unmapped_behavior(self):
        old = "def one(): pass\n"
        c = change("app.py", old, old + "\n\ndef two(): pass\n")
        facts = assess([c], [requirement("R1", "app.py::two")], [])
        self.assertEqual([(s["symbol"], s["status"]) for s in facts["scope"]], [("two", "mapped")])
        blocked = assess([c], [], ["app.py"])
        self.assertEqual({s["symbol"] for s in blocked["scope"]}, {"", "two"})

    def test_assigned_fallback_returned_after_handler(self):
        after = """
            def load(fn):
                try:
                    value = fn()
                except Exception:
                    value = None
                return value
        """
        old = after.replace("value = None", "raise")
        report = assess([change("service.py", old, after)], [], [])
        finding, = report["defenses"]
        self.assertEqual((finding["rule"], finding["line"], finding["end"]), ("fallback", 4, 6))
        self.assertIn("return value", finding["snippet"])
        direct = after.replace("value = None", "return None")
        self.assertFalse(assess([change("service.py", direct, after)], [], [])["defenses"])
        for unrelated in [
            after.replace("return value", "return 5"),
            after.replace("except Exception:", "except Exception as value:"),
            after.replace("return value", "value = 5\n                return value"),
            after.replace("return value", "finally:\n                    value = 5\n                return value"),
        ]:
            report = assess([change("service.py", old, unrelated)], [], [])
            self.assertEqual(report["diagnostics"], [])
            self.assertEqual(report["defenses"], [])

    def test_new_file_effect_in_mapped_symbol_needs_its_own_justification(self):
        before = "def cancel(item):\n    return item\n"
        after = """\
            def cancel(item):
                with open('telemetry.log', 'a') as log:
                    log.write('cancelled')
                return item
        """
        req = requirement("R1", "code.py::cancel")
        result = assess([change("code.py", before, after)], [req], [])
        self.assertEqual([i["status"] for i in result["scope"]], ["mapped", "unmapped"])
        site = result["scope"][1]
        self.assertEqual((site["symbol"], site["line"], site["end"]), ("cancel", 2, 2))
        self.assertEqual(site["requirementIds"], ["R1"])
        self.assertIn("telemetry.log", site["snippet"])
        self.assertNotEqual(site["id"], result["scope"][0]["id"])
        self.assertEqual(len(result["requirements"]["R1"]), 2)
        # A rule in a different part of the repository cannot authorize this write.
        other = {**requirement("R2", "other.py"), "writes": ["telemetry.log"]}
        self.assertIn("unmapped", [i["status"] for i in assess([change("code.py", before, after)], [req, other], [])["scope"]])
        allowed = {**req, "writes": ["telemetry.log"]}
        self.assertTrue(all(i["status"] == "mapped" for i in assess([change("code.py", before, after)], [allowed], [])["scope"]))

    def test_effects_compare_revisions_and_distinguish_static_from_dynamic_targets(self):
        old = textwrap.dedent("""
            from pathlib import Path as P
            from io import open as file_open
            def run(name):
                P("old.log").write_text("same behavior")
                file_open("read.txt", "r")
        """).lstrip()
        new = old + ('    P("old.log").write_text("second write")\n'
                     '    with file_open(name, mode="w"): pass\n'
                     '    with P("a.bin").open("wb"): pass\n')
        req = {**requirement("R", "app.py::run"), "writes": ["name"]}
        findings = assess([change("app.py", old, new)], [req], [])["scope"]
        added = [f for f in findings if f["status"] == "unmapped"]
        self.assertEqual([f["line"] for f in added], [6, 7, 8])
        self.assertEqual([f["target"] for f in added], ["old.log", None, "a.bin"])
        self.assertTrue(all(f["symbol"] == "run" for f in added))
        relocated = old.replace('    file_open("read.txt", "r")', '    answer = 1\n    file_open("read.txt", "r")')
        self.assertTrue(all(f["status"] == "mapped" for f in assess([change("app.py", old, relocated)], [req], [])["scope"]))

    def test_local_open_and_read_only_calls_are_not_file_writes(self):
        source = """
            def first(open):
                return open("fake", "w")
            def second():
                open("unused", "read")
                with open("input", mode="rb") as f:
                    return f.read()
            def third():
                return open(file="output", mode="x")
        """
        req = requirement("R", "pkg.py")
        report = assess([change("pkg.py", "", source)], [req], [])
        self.assertEqual([f["symbol"] for f in report["scope"] if f["status"] == "unmapped"], ["third"])

    def test_path_variable_keeps_target_evidence_and_write_permission(self):
        source = """
            from pathlib import Path as P
            def cancel(name):
                path = P("telemetry.log")
                path.write_text("cancelled")
                allowed = P("orders.log")
                allowed.write_bytes(b"cancelled")
                data: P = P(name)
                with data.open(mode="wb"): pass
                with path.open("r"): pass
        """
        req = {**requirement("R", "orders.py"), "writes": ["orders.log", "name"]}
        report = assess([change("orders.py", None, source)], [req], [])
        effects = [row for row in report["scope"] if row.get("kind") == "file-write"]
        self.assertEqual([(r["line"], r["target"], r["status"]) for r in effects],
                         [(4, "telemetry.log", "unmapped"), (8, None, "unmapped")])
        self.assertEqual(effects[0]["afterRanges"], [{"line": 4, "end": 4}, {"line": 3, "end": 3}])
        self.assertIn("path.write_text", effects[0]["snippet"])

    def test_file_write_comparison_follows_target_across_local_variable_refactor(self):
        direct = 'import pathlib\ndef run():\n    pathlib.Path("old.bin").write_bytes(b"x")\n'
        alias = 'import pathlib\ndef run():\n    out = pathlib.Path("old.bin")\n    out.write_bytes(b"x")\n'
        req = requirement("R", "app.py::run")
        for before, after, targets in ((direct, alias, []), (alias, direct, []),
                                       (alias, alias.replace("old.bin", "extra.bin"), ["extra.bin"])):
            with self.subTest(before=before, after=after):
                report = assess([change("app.py", before, after)], [req], [])
                self.assertEqual([r["target"] for r in report["scope"] if r.get("kind") == "file-write"], targets)

    def test_unknown_path_receivers_do_not_create_file_write_findings(self):
        source = """
            from pathlib import Path
            def shadowed(Path):
                obj = Path("fake")
                obj.write_text("data")
            def reassigned(other):
                obj = Path("unused")
                obj = other
                obj.write_bytes(b"data")
            def conditional(ok, other):
                if ok:
                    obj = other("fake")
                    obj.open("w")
        """
        report = assess([change("app.py", None, source)], [requirement("R", "app.py")], [])
        self.assertFalse([r for r in report["scope"] if r.get("kind") == "file-write"])


if __name__ == "__main__":
    unittest.main()
