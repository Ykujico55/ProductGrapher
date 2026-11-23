"""Review one delivery against a contract supplied by the person accepting it."""

from __future__ import annotations

import configparser
import shutil
import tempfile
from pathlib import Path

from .acceptance import load_contract, pending, requirement_status, run_check, scenario_name
from .changes import compare, materialize
from .findings import assess
from .outputs import existing_export
from .project import build_graph, digest


COMPARISONS = {
    ("failed", "passed"): "基线未满足该检查，交付版本通过。",
    ("passed", "passed"): "基线已通过这项检查，交付版本继续通过。",
    ("passed", "failed"): "基线通过，交付版本出现回归。",
    ("failed", "failed"): "基线和交付版本均未满足这项检查。",
}


def _snapshot_review(check, snapshot, spec_dir, parent):
    with tempfile.TemporaryDirectory(dir=parent) as fresh:
        work = Path(fresh) / "repo"
        shutil.copytree(snapshot, work)
        return run_check(check, work, spec_dir)


def _output_paths(root, outputs):
    paths = []
    for output in outputs:
        path = Path(output).resolve()
        if not path.is_relative_to(root):
            continue
        if path.exists() and existing_export(path) != "review":
            raise ValueError(f"输出路径包含现有仓库文件，请另选报告文件名：{path}")
        paths.append(path.relative_to(root).as_posix())
    return sorted(paths)


def build_report(root, spec_path, *, base=None, head=None, base_dir=None, run=False, outputs=()):
    root = Path(root).resolve()
    spec_path = Path(spec_path).resolve()
    contract = load_contract(spec_path)
    excluded = _output_paths(root, outputs)
    diff = compare(root, base=base, head=head, base_dir=base_dir, exclude=excluded)
    facts = assess(diff["changes"], contract["requirements"], contract["forbidden"])
    diagnostics = list(facts["diagnostics"])
    if excluded:
        diagnostics.append("本次报告的输出文件单独保存，不计入交付改动：" + "、".join(excluded))
    if spec_path.is_relative_to(root):
        spec_file = spec_path.relative_to(root).as_posix()
        if any(change["file"] == spec_file for change in diff["changes"]):
            diagnostics.append(f"验收清单 {spec_file} 本身属于本次改动。请核对它与开工前约定的版本。")

    for finding in facts["defenses"]:
        if finding["rule"] in contract["forbiddenDefenses"]:
            finding["status"] = "forbidden"
            finding["detail"] += f" 清单 forbiddenDefenses 明确禁止 {finding['rule']}。"
    report = {"schema": "how2trust.review.v1", "task": contract["task"], "base": diff["base"],
              "target": diff["target"], "forbidden": contract["forbidden"],
              "forbiddenDefenses": contract["forbiddenDefenses"], "requirements": [],
              "defenses": facts["defenses"], "scope": facts["scope"], "diagnostics": diagnostics,
              "outputFiles": excluded,
              "changes": [{k: v for k, v in change.items() if k not in {"before", "after", "addedLines", "removedLines"}}
                          for change in diff["changes"]], "inputFingerprint": diff["fingerprint"]}
    running = run and any(req["checks"] for req in contract["requirements"])
    with tempfile.TemporaryDirectory(prefix="how2trust-review-") as tmp:
        folder = Path(tmp)
        target = folder / "delivery"
        target.mkdir()
        inventory = []
        missing = materialize(root, target, revision=diff.get("targetCommit") or head,
                              directory=base_dir is not None, inventory=inventory, exclude=excluded)
        report["targetFingerprint"] = digest(inventory)
        if missing:
            diagnostics.append("交付快照未复制以下特殊文件：" + "、".join(missing))
        source_symbols = {}
        try:
            graph = build_graph(target)
            report["codeSnapshot"] = graph["snapshot"]
            source_symbols = {f"{n['file']}::{n['qualname']}": n for n in graph["nodes"]
                              if n["kind"] in {"function", "method", "class"}}
        except (ValueError, OSError, configparser.Error) as exc:
            diagnostics.append(f"交付版本的架构图无法生成：{exc}")
        previous = folder / "baseline"
        if running:
            previous.mkdir()
            missing = materialize(Path(base_dir) if base_dir is not None else root, previous,
                                  revision=diff.get("baseCommit") or base, directory=base_dir is not None, exclude=excluded)
            if missing:
                diagnostics.append("基线快照未复制以下特殊文件：" + "、".join(missing))
        for req in contract["requirements"]:
            results = []
            for check in req["checks"]:
                if running:
                    baseline = _snapshot_review(check, previous, spec_path.parent, folder)
                    result = _snapshot_review(check, target, spec_path.parent, folder)
                    result["baseline"] = baseline
                    transition = (requirement_status([baseline]), requirement_status([result]))
                    explanation = COMPARISONS.get(transition)
                    if explanation:
                        result["detail"] += "。" + explanation
                else:
                    result = pending(check)
                if "source" in check:
                    ref = {"selector": check["source"], "resolved": False}
                    node = source_symbols.get(check["source"])
                    if node:
                        lines = graph["files"][node["file"]].splitlines()
                        ref.update(file=node["file"], symbol=node["qualname"], line=node["line"],
                                   end=node["end"], snippet="\n".join(lines[node["line"] - 1:node["end"]]), resolved=True)
                    result["sourceReference"] = ref
                results.append(result)
            scenarios = req.get("scenarios", [])
            names = {check["name"] for check in results}
            missing_scenarios = [scenario_name(s) for s in scenarios if scenario_name(s) not in names]
            status = requirement_status(results, missing_scenarios)
            baseline_status = requirement_status([r["baseline"] for r in results if "baseline" in r], missing_scenarios)
            report["requirements"].append({
                "id": req["id"], "text": req["text"], "scope": req["scope"],
                **({"writes": req["writes"]} if "writes" in req else {}),
                "evidence": facts["requirements"][req["id"]], "checks": results,
                "status": status, "scenarios": scenarios, "missingScenarios": missing_scenarios,
                "baselineStatus": baseline_status, "comparison": COMPARISONS.get((baseline_status, status)),
            })
    report["fingerprint"] = digest(report)
    return report


def graph_for_report(root, report):
    """Open the precise file inventory reviewed, including historical Git commits."""
    target = report["target"]
    revision = None if target in ("TARGET_DIR", "WORKTREE") else target
    with tempfile.TemporaryDirectory(prefix="how2trust-view-") as tmp:
        inventory = []
        materialize(root, tmp, revision=revision, directory=target == "TARGET_DIR", inventory=inventory,
                    exclude=report["outputFiles"])
        if report.get("targetFingerprint") != digest(inventory):
            raise ValueError("当前交付文件与验收报告不同。请打开报告对应的版本，或重新验收当前改动。")
        graph = build_graph(tmp)
    if report.get("codeSnapshot") != graph["snapshot"]:
        raise ValueError("架构图与报告中的代码快照不一致，请使用相同的工具与 Python 版本重新生成。")
    return graph
