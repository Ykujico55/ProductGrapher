import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


WORKER = Path(__file__).resolve().parents[1] / "how2trust" / "pycg_worker.py"


class PyCGWorkerTests(unittest.TestCase):
    def test_isolated_worker_loads_stubs_and_finds_callback_without_executing_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "源码"
            root.mkdir()
            (root / "main.py").write_text(
                "from helper import invoke\nfrom pathlib import Path\n"
                "def 完成(): pass\ninvoke(完成)\nPath.cwd()\nraise RuntimeError()\n",
                encoding="utf-8",
            )
            (root / "helper.py").write_text("def invoke(f):\n    f()\n", encoding="utf-8")
            request = {"root": str(root), "files": ["main.py", "helper.py"]}
            process = subprocess.run(
                [sys.executable, "-I", "-S", str(WORKER)],
                input=json.dumps(request, ensure_ascii=False).encode("utf-8"),
                capture_output=True,
                check=True,
            )
        doc = json.loads(process.stdout)
        self.assertEqual(doc["format"], "pycg/1")
        self.assertEqual(doc["tool"]["version"], "2.0.0")
        self.assertEqual(doc["calls"]["helper.invoke"], [
            {"line": 2, "col": 4, "end": [2, 7], "callees": ["main.完成"]}
        ])
        self.assertEqual(doc["outside"]["pathlib.Path.cwd"]["known"], "stub")
