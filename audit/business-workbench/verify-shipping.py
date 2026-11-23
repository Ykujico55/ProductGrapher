"""Use the real How2Trust CLI to compare a correct version, a one-line regression and a weak check."""

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


CASE = Path(__file__).resolve().parent
WORKBENCH = CASE.parents[1]
PROJECT = CASE.parent / "terra-fulfillment" / "project"


def review(name, project, spec):
    output = CASE / f"review-{name}.html"
    data = output.with_suffix(".json")
    subprocess.run([sys.executable, "-B", "-X", "utf8", "-m", "how2trust", "review", str(project),
                    "--base-dir", str(PROJECT), "--spec", str(spec), "--run-checks",
                    "-o", str(output), "--json", str(data)], cwd=WORKBENCH, check=True)
    report = json.loads(data.read_bytes())
    return report["requirements"][0], report["codeSnapshot"]


def main():
    strong_spec = CASE / "contract.json"
    with tempfile.TemporaryDirectory() as tmp:
        scratch = Path(tmp)
        broken = scratch / "wrong"
        shutil.copytree(PROJECT, broken, ignore=shutil.ignore_patterns("__pycache__"))
        file = broken / "dispatchdesk" / "store.py"
        original = file.read_bytes()
        expression = b'"partial" if pending else "shipped"'
        assert original.count(expression) == 1
        file.write_bytes(original.replace(expression, b'"shipped"'))

        before, good_snapshot = review("correct", PROJECT, strong_spec)
        after, bad_snapshot = review("wrong", broken, strong_spec)

        weak_spec = scratch / "exit-only.json"
        weak = json.loads(strong_spec.read_bytes())
        del weak["requirements"][0]["checks"][0]["expect"]["json"]
        weak_spec.write_text(json.dumps(weak, ensure_ascii=False), encoding="utf-8")
        shutil.copyfile(CASE / "ship-probe.py", scratch / "ship-probe.py")
        weak_result, weak_snapshot = review("exit-only", broken, weak_spec)

    assert before["status"] == before["baselineStatus"] == "passed"
    assert after["status"] == "failed" and after["baselineStatus"] == "passed"
    assert weak_result["status"] == weak_result["baselineStatus"] == "unverified"
    assert good_snapshot != bad_snapshot == weak_snapshot
    check = after["checks"][0]
    assert check["sourceReference"]["resolved"]
    assert check["sourceReference"]["symbol"] == "Desk.ship"
    trace = check["traceEvidence"]
    assert trace["status"] == "observed" and trace["symbol"] == "Desk.ship" and trace["hits"] == 3
    assert 139 in trace["executed"]
    actual_line = next(row["text"] for row in trace["sourceLines"] if row["line"] == 139)
    assert '("shipped", oid)' in actual_line
    differences = {item["path"]: [item["expected"], item["actual"]] for item in check["observations"]
                   if item["status"] == "failed"}
    assert differences["/first/state"] == ["partial", "shipped"]
    assert differences["/last/exit"] == [0, 1]
    assert differences["/last/allocations"][0][1][-1] == 1
    assert differences["/last/allocations"][1][1][-1] == 0
    assert len(differences) == 3
    print(json.dumps({"correct": before["status"], "wrong": after["status"], "exitOnly": weak_result["status"],
                      "expectedAndActual": differences, "traced": trace}, ensure_ascii=False))


if __name__ == "__main__":
    main()
