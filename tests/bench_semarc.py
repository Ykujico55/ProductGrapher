"""Compare file memberships in a SemArc architecture reference with a directory baseline and an optional How2Trust business graph."""

import argparse
from collections import defaultdict
from hashlib import sha256
from itertools import combinations
import json
from pathlib import Path, PurePosixPath
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from how2trust.claim_drift import read_previous_graph
from how2trust.project import build_graph
from how2trust.semantics import validate_semantics
from how2trust.source_roles import source_role


def reference_memberships(document, prefix):
    """A source file can have several components in SemArc's expert decomposition."""
    groups = document.get("structure")
    if not isinstance(groups, list) or not groups:
        raise ValueError("SemArc 标注缺少 structure 分组")
    memberships = defaultdict(set)
    for group in groups:
        if group.get("@type") != "group" or not group.get("name"):
            raise ValueError("SemArc 的顶层组件缺少名称或类型")
        for member in group.get("nested", []):
            if member.get("@type") != "item" or not member.get("name"):
                raise ValueError("SemArc 的组件成员应为带路径的 item")
            path = (prefix / member["name"].replace("\\", "/")).as_posix()
            memberships[path].add(group["name"])
    if not memberships:
        raise ValueError("SemArc 标注没有文件")
    return memberships


def coassigned_pairs(memberships, paths):
    groups = defaultdict(list)
    for path in sorted(paths):
        for label in memberships.get(path, ()):
            groups[label].append(path)
    return {pair for members in groups.values() for pair in combinations(members, 2)}


def shared(memberships):
    return {p: sorted(names) for p, names in sorted(memberships.items()) if len(names) > 1}


def score(prediction, aligned, expected_pairs):
    if not aligned:
        return {"status": "not_measured", "reason": "没有对齐的标注文件，请核对 --source-prefix 和仓库版本"}
    predicted = coassigned_pairs(prediction, aligned)
    matched = len(predicted & expected_pairs)
    ungrouped = sorted(p for p in aligned if not prediction.get(p))
    return {
        "status": "measured", "alignedFiles": len(aligned), "ungrouped": ungrouped,
        "groupedFiles": len(aligned) - len(ungrouped), "shared": shared({p: prediction.get(p, ()) for p in aligned}),
        "matchedPairs": matched, "predictedPairs": len(predicted), "referencePairs": len(expected_pairs),
        "extraPairs": len(predicted) - matched, "missedPairs": len(expected_pairs) - matched,
        "precision": matched / len(predicted) if predicted else None,
        "recall": matched / len(expected_pairs) if expected_pairs else None,
    }


def business_memberships(repo_graph, exported):
    """Use actual grouped symbols. A citation in a different file is not file membership."""
    if "semantics" not in exported:
        return None
    def python_files(graph):
        return {p: source for p, source in graph["files"].items() if p.endswith(".py")}

    if python_files(exported) != python_files(repo_graph):
        raise ValueError("业务图的 Python 源文件与待测仓库不一致，请用同一提交和源码根目录导出图")
    guide = validate_semantics(exported, exported["semantics"])
    node_files = {n["id"]: n["file"] for n in exported["nodes"]}
    memberships = defaultdict(set)
    for group in guide.get("groups", []):
        for member in group["children"]:
            for node_id in member["nodeIds"]:
                memberships[node_files[node_id]].add(group["label"])
    return guide, memberships


def evaluate(root, reference, source_prefix=".", export=None):
    prefix = PurePosixPath(source_prefix.replace("\\", "/"))
    graph = build_graph(root)
    if not graph["files"]:
        raise ValueError("请指定包含源码的仓库根目录")
    reference_bytes = reference.read_bytes()
    labels = reference_memberships(json.loads(reference_bytes.decode("utf-8-sig")), prefix)
    expected = {p for p in labels if p.endswith(".py") and source_role(p) == "production"}
    indexed = {p for p in graph["files"] if p.endswith(".py") and source_role(p) == "production"
               and PurePosixPath(p).is_relative_to(prefix)}
    aligned = expected & indexed
    expected_pairs = coassigned_pairs(labels, aligned)
    directory = {p: {PurePosixPath(p).parent.as_posix()} for p in aligned}
    missing_export = "未提供带业务讲解的 How2Trust 导出图" if export is None else "导出的图没有业务讲解"
    business = {"status": "not_measured", "reason": missing_export}
    if export is not None:
        result = business_memberships(graph, read_previous_graph(export.read_text(encoding="utf-8-sig")))
        if result is not None:
            guide, predictions = result
            measured = score(predictions, aligned, expected_pairs) if guide.get("groups") else {
                "status": "not_measured", "reason": "业务讲解中没有导出的业务分组"}
            business = {**measured,
                        "provider": guide["provider"], "model": guide["model"], "guideFingerprint": guide["fingerprint"]}
    return {
        "snapshot": graph["snapshot"], "sourcePrefix": prefix.as_posix(),
        "metric": "Distinct unordered pairs of aligned production files sharing at least one component",
        "reference": {"groups": len(set().union(*labels.values())), "files": len(labels),
                      "memberships": sum(map(len, labels.values())), "shared": shared(labels),
                      "sha256": sha256(reference_bytes).hexdigest()},
        "alignment": {"productionInScope": len(indexed), "aligned": sorted(aligned),
                      "missingFromSource": sorted(expected - indexed), "unlabelledInSource": sorted(indexed - expected),
                      "excludedNonProductionOrNonPython": sorted(labels.keys() - expected),
                      "productionReferencePairs": len(coassigned_pairs(labels, expected)),
                      "alignedReferencePairs": len(expected_pairs)},
        "directoryBaseline": {"method": "repository-relative immediate parent directory", **score(directory, aligned, expected_pairs)},
        "workbenchBusiness": business,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="Path to the Python repository used to export the graph")
    parser.add_argument("reference", type=Path, help="SemArc labeled reference JSON")
    parser.add_argument("--source-prefix", default=".", help="Directory prepended to reference paths, e.g. scrapy")
    parser.add_argument("--graph", type=Path, help="How2Trust export with embedded semantic groups (JSON or HTML)")
    parser.add_argument("--json", type=Path, help="Write full lists of aligned, missing, shared and ungrouped files")
    args = parser.parse_args()
    try:
        report = evaluate(args.root, args.reference, args.source_prefix, args.graph)
        if args.json:
            if args.json.resolve() in {p.resolve() for p in (args.reference, args.graph) if p}:
                raise ValueError("结果输出路径与输入文件相同")
            args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (ValueError, OSError) as exc:
        parser.exit(1, str(exc) + "\n")
    summary = {key: {k: len(v) if isinstance(v, list) else v for k, v in value.items()}
               if isinstance(value, dict) else value for key, value in report.items()}
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
