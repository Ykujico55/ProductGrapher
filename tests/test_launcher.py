import os
import subprocess
import unittest
from pathlib import Path


@unittest.skipUnless(os.name == "nt", "Windows launcher")
class LauncherTests(unittest.TestCase):
    def test_top_level_help_from_batch_launcher(self):
        root = Path(__file__).resolve().parents[1]
        for flag in ("--help", "-h"):
            with self.subTest(flag=flag):
                result = subprocess.run(
                    ["cmd.exe", "/d", "/c", "How2Trust.cmd", flag], cwd=root,
                    stdin=subprocess.DEVNULL, capture_output=True, timeout=20,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(b"review-gui", result.stdout)
                self.assertIn(b"export", result.stdout)
