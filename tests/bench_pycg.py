"""Compare exported static calls with the ICSE 2021 PyCG micro-benchmark."""

import argparse
import json
from pathlib import Path
import sys

# Allow running this file directly against a local checkout without installation.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from how2trust.project import build_graph


def compare_case(oracle, root):
    expected = {(caller, target)
                for caller, targets in json.loads(oracle.read_text(encoding="utf-8")).items()
                for target in targets}
    graph = build_graph(oracle.parent)
    modules = {n["file"]: n["qualname"] for n in graph["nodes"] if n["kind"] == "module"}
    names = {n["id"]: n["qualname"] if n["kind"] == "module"
             else ".".join(filter(None, [modules[n["file"]], n["qualname"]])) for n in graph["nodes"]}
    predicted = {(names[e["source"]], names[e["target"]]) for e in graph["edges"] if e["kind"] == "calls"}
    return {"case": oracle.parent.relative_to(root).as_posix(), "matched": len(expected & predicted),
            "extra": sorted(predicted - expected), "missing": sorted(expected - predicted)}


def evaluate(root):
    oracles = sorted(root.rglob("callgraph.json"))
    if not oracles:
        raise ValueError("目录中没有 PyCG 的 callgraph.json 参考答案")
    cases = [compare_case(oracle, root) for oracle in oracles]
    matched = sum(row["matched"] for row in cases)
    extra = sum(len(row["extra"]) for row in cases)
    missing = sum(len(row["missing"]) for row in cases)
    predicted, expected = matched + extra, matched + missing
    return {"cases": len(cases), "exactCases": sum(not row["extra"] and not row["missing"] for row in cases),
            "matched": matched, "predicted": predicted, "expected": expected,
            "precision": matched / predicted if predicted else None,
            "recall": matched / expected if expected else None, "results": cases}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="Path to the original data/micro-benchmark directory")
    parser.add_argument("--json", type=Path, help="Write per-case missing and extra call pairs")
    args = parser.parse_args()
    try:
        report = evaluate(args.root)
    except (OSError, ValueError) as exc:
        parser.exit(1, str(exc) + "\n")
    if args.json:
        args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "results"}, ensure_ascii=False))
