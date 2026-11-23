import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from how2trust.changes import compare, materialize


class ChangeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.temp = Path(temp.name)
        self.root = self.temp / "target"
        self.root.mkdir()

    def git(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.root, stderr=subprocess.PIPE).decode().strip()

    def init_git(self):
        self.git("init", "-q")
        self.git("config", "user.name", "How2Trust Test")
        self.git("config", "user.email", "how2trust@example.invalid")
        self.git("config", "core.autocrlf", "false")
        self.git("config", "commit.gpgsign", "false")

    def test_worktree_commit_and_subdirectory(self):
        self.init_git()
        sub = self.root / "feature"
        sub.mkdir()
        text = sub / "logic.py"
        text.write_bytes(b"a = 1\nb = 2\n")
        (sub / "gone.py").write_bytes(b"# old\n")
        (self.root / ".gitignore").write_bytes(b"*.ignored\n")
        self.git("add", ".")
        self.git("commit", "-qm", "baseline")
        baseline = self.git("rev-parse", "HEAD")
        text.write_bytes(b"a = 1\nb = 3\n")
        self.git("add", ".")
        self.git("commit", "-qm", "delivery")
        commit = self.git("rev-parse", "HEAD")
        text.write_bytes(b"a = 1\nb = 4\n")
        self.git("add", "feature/logic.py")
        text.write_bytes(b"a = 1\nb = 5\nc = 6\n")
        (sub / "new file.md").write_bytes(b"notes\n")
        (sub / "quiet.ignored").write_bytes(b"notes\n")
        (sub / "gone.py").unlink()
        (self.root / "elsewhere").write_bytes(b"unrelated")

        report = compare(sub, base=baseline)
        changes = {c["file"]: c for c in report["changes"]}
        self.assertEqual(set(changes), {"gone.py", "logic.py", "new file.md"})
        self.assertEqual(report["baseCommit"], baseline)
        self.assertIsNone(report["targetCommit"])
        self.assertEqual(changes["gone.py"]["removedLines"], [1])
        self.assertEqual(changes["logic.py"]["removedLines"], [2])
        self.assertEqual(changes["logic.py"]["addedLines"], [2, 3])
        self.assertEqual(changes["logic.py"]["hunks"][0]["lines"], [" a = 1", "-b = 2", "+b = 5", "+c = 6"])
        self.assertEqual(changes["new file.md"]["status"], "added")
        self.assertEqual(compare(sub, base=baseline), report)
        committed = compare(sub, base=baseline, head=commit)
        self.assertEqual(committed["targetCommit"], commit)
        self.assertEqual([c["file"] for c in committed["changes"]], ["logic.py"])
        self.assertEqual(committed["changes"][0]["after"], "a = 1\nb = 3\n")
        self.assertEqual(materialize(sub, self.temp / "committed", revision=commit), [])
        self.assertEqual((self.temp / "committed/logic.py").read_bytes(), b"a = 1\nb = 3\n")
        self.assertEqual(materialize(sub, self.temp / "work"), [])
        self.assertEqual({p.name for p in (self.temp / "work").iterdir()}, {"logic.py", "new file.md"})
        self.assertEqual((self.temp / "work/logic.py").read_bytes(), text.read_bytes())

    def test_folder_binary_empty_line_ranges_and_materialization(self):
        before = self.temp / "before"
        before.mkdir()
        (before / "app.py").write_bytes(b"first\npass\n")
        (self.root / "app.py").write_bytes(b"first\nreturn 4\n")
        (before / "app.dat").write_bytes(b"\0abc")
        (self.root / "app.dat").write_bytes(b"\0xyz")
        (self.root / "empty").touch()
        (self.root / ".git").mkdir()
        report = compare(self.root, base_dir=before)
        changes = {c["file"]: c for c in report["changes"]}
        self.assertEqual(set(changes), {"app.py", "app.dat", "empty"})
        self.assertEqual(changes["app.py"]["addedLines"], [2])
        self.assertEqual(changes["app.py"]["hunks"][0]["newStart"], 1)
        self.assertTrue(changes["app.dat"]["binary"])
        self.assertNotEqual(changes["app.dat"]["beforeHash"], changes["app.dat"]["afterHash"])
        self.assertEqual(changes["app.dat"]["hunks"], [])
        self.assertEqual(changes["empty"]["status"], "added")
        self.assertEqual(changes["empty"]["after"], "")
        self.assertEqual(materialize(self.root, self.temp / "copy", directory=True), [])
        self.assertEqual(sorted(p.name for p in (self.temp / "copy").iterdir()), sorted(changes))
        with self.assertRaises(ValueError):
            materialize(self.root, self.temp / "copy", directory=True)

    def test_autocrlf_and_ignored_paths(self):
        self.init_git()
        path = self.root / "text.txt"
        path.write_bytes(b"a\nb\n")
        self.git("add", "text.txt")
        self.git("commit", "-qm", "baseline")
        self.git("config", "core.autocrlf", "true")
        path.write_bytes(b"a\r\nb\r\n")
        self.assertEqual(compare(self.root)["changes"], [])
        path.write_bytes(b"a\r\nnew\r\n")
        change, = compare(self.root)["changes"]
        self.assertEqual(change["removedLines"], [2])
        self.assertEqual(change["addedLines"], [2])

    def test_invalid_refs_and_conflicting_modes(self):
        self.init_git()
        self.git("commit", "--allow-empty", "-qm", "baseline")
        with self.assertRaises(ValueError):
            compare(self.root, base="not-a-commit")
        with self.assertRaises(ValueError):
            compare(self.root, base="HEAD", base_dir=self.temp)

    def test_executable_index_mode_when_filemode_is_disabled(self):
        self.init_git()
        self.git("config", "core.filemode", "false")
        (self.root / "tool").write_bytes(b"echo hi\n")
        self.git("add", "--chmod=+x", "tool")
        self.git("commit", "-qm", "executable")
        self.assertEqual(compare(self.root)["changes"], [])
        (self.root / "tool").write_bytes(b"echo updated\n")
        change, = compare(self.root)["changes"]
        self.assertEqual((change["beforeMode"], change["afterMode"]), ("100755", "100755"))

    def test_compare_links_without_copying_target(self):
        outside = self.temp / "private"
        outside.write_bytes(b"unrelated bytes")
        link = self.root / "link"
        try:
            os.symlink(outside, link)
        except OSError:
            self.skipTest("OS does not permit symlink creation")
        before = self.temp / "before"
        before.mkdir()
        change, = compare(self.root, base_dir=before)["changes"]
        self.assertEqual(change["afterMode"], "120000")
        self.assertNotIn("unrelated bytes", change["after"])
        self.assertEqual(materialize(self.root, self.temp / "copy", directory=True), ["link"])
        self.assertFalse((self.temp / "copy/link").exists())
