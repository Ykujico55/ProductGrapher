import json, os, subprocess, sys, tempfile, unittest


class TestCLI(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "db")

    def tearDown(self):
        self.tmp.cleanup()

    def cmd(self, *args, ok=True):
        p = subprocess.run(
            [sys.executable, "-m", "dispatchdesk", "--db", self.db, *map(str, args)],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if ok:
            self.assertEqual(p.returncode, 0, p.stderr)
            return json.loads(p.stdout)
        self.assertNotEqual(p.returncode, 0)

    def test_flow(self):
        self.cmd("stock", "B", "x", 4)
        self.cmd("stock", "A", "x", 2)
        o = self.cmd("order", "o", "A", "x=5")
        self.assertEqual(
            [(a["warehouse"], a["qty"]) for a in o["allocations"]], [("A", 2), ("B", 3)]
        )
        self.assertEqual(self.cmd("ship", "o", "A")["state"], "partial")
        self.cmd("ship", "o", "A", ok=False)
        self.cmd("cancel", "o", ok=False)
        self.assertEqual(self.cmd("ship", "o", "B")["state"], "shipped")
        self.cmd("return", "o", "A", "x", 1, "--quality", "damaged")
        self.cmd("return", "o", "A", "x", 2, "--quality", "sellable", ok=False)
        self.cmd("return", "o", "A", "x", 1, "--quality", "sellable")
        self.assertEqual(
            self.cmd("report")["inventory"][0],
            {"warehouse": "A", "sku": "x", "available": 1, "reserved": 0, "damaged": 1},
        )

    def test_retry(self):
        self.cmd("stock", "A", "x", 2)
        self.assertEqual(
            self.cmd("order", "first", "A", "x=2", "y=1")["state"], "waiting"
        )
        self.assertEqual(self.cmd("order", "second", "A", "x=2")["state"], "reserved")
        self.cmd("stock", "A", "y", 1)
        self.assertEqual(self.cmd("status", "first")["state"], "waiting")
        self.cmd("cancel", "second")
        self.assertEqual(self.cmd("status", "first")["state"], "reserved")
        self.cmd("order", "second", "A", "x=1", ok=False)

    def test_bulk_and_return(self):
        self.cmd("stock", "B", "x", 3)
        self.cmd("stock", "A", "x", 3)
        o = self.cmd("order", "one", "B", "x=4", "--policy", "bulk")
        self.assertEqual(
            [(a["warehouse"], a["qty"]) for a in o["allocations"]], [("A", 3), ("B", 1)]
        )
        self.cmd("ship", "one", "A")
        self.cmd("order", "two", "A", "x=3")
        self.cmd("return", "one", "A", "x", 1, "--quality", "sellable")
        self.assertEqual(self.cmd("status", "two")["state"], "reserved")
        self.cmd("stock", "A", "x", 0, ok=False)


if __name__ == "__main__":
    unittest.main()
