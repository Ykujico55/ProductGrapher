"""Compare the source evidence of a frozen overview to a different repository snapshot."""

import ast
from collections import defaultdict
from difflib import SequenceMatcher

from .collaboration import _CallConditions
from .outputs import export_data
from .project import digest
from .semantics import validate_semantics


SCHEMA = "how2trust.claim-drift.v1"
STATUS_LABELS = {
    "missing": "旧证据已缺失",
    "changed": "证据发生变化",
    "unresolved": "需要人工定位",
    "moved": "证据位置移动",
    "unchanged": "引用仍匹配",
}
SNAPSHOT_FIELDS = ("schema", "name", "files", "nodes", "edges", "diagnostics", "tree")


def read_previous_graph(text):
    """Read a local export as data, including the JSON embedded in offline HTML."""
    graph = export_data(text.lstrip("\ufeff"))
    if not isinstance(graph, dict) or graph.get("schema") != 1:
        raise ValueError("请选择导出的旧架构 HTML 或包含源码的图 JSON。单独的讲解文件没有旧版源码。")
    return graph


def _range(path, line, end):
    return {"file": path, "line": line, "end": end}


def _ref_key(ref):
    return ref["file"], ref["line"], ref["end"]


class _Index:
    def __init__(self, graph):
        self.files = graph["files"]
        self.lines = {}
        self.definitions = defaultdict(list)
        self.modules = set()
        nodes = {node["id"]: node for node in graph["nodes"]}
        for node in nodes.values():
            if node["kind"] in {"class", "function", "method"}:
                self.definitions[node["file"]].append(node)
            elif node["kind"] == "module":
                self.modules.add(node["file"])
        self.calls = defaultdict(list)
        for edge in graph["edges"]:
            if edge["kind"] in {"calls", "handles"}:
                target = nodes[edge["target"]]
                self.calls[edge["file"]].append((edge, target))

    def rows(self, path):
        if path not in self.lines:
            self.lines[path] = self.files[path].splitlines()
        return self.lines[path]

    def targets(self, ref, renames):
        found = []
        for edge, node in self.calls[ref["file"]]:
            if edge["line"] <= ref["end"] and ref["line"] <= edge["end"]:
                path = renames.get(node["file"], node["file"])
                found.append((edge["kind"], path, node["kind"], node["qualname"]))
        return sorted(found)


class _LineMap:
    """Locate a cited interval within the same named definition or module."""

    def __init__(self, old, new):
        self.old = old
        self.new = new
        self.diff = None

    def interval(self, start, end):
        # start and end are zero-based, end-exclusive within their definitions.
        if self.old == self.new:
            return start, end, "match"
        snippet = self.old[start:end]

        def matches(lines):
            return [i for i, line in enumerate(lines)
                    if line == snippet[0] and lines[i:i + len(snippet)] == snippet]

        hits = matches(self.new)
        if hits:
            if len(hits) == 1 and len(matches(self.old)) == 1:
                return hits[0], hits[0] + len(snippet), "match"
            return None, None, "ambiguous"
        if self.diff is None:
            self.diff = SequenceMatcher(None, self.old, self.new).get_opcodes()
        # Use common surrounding lines as anchors. When the old reference is
        # only a small piece of a rewritten block, its new extent is unknown.
        spans = []
        for tag, a, b, c, d in self.diff:
            if tag == "insert":
                if start < a < end:
                    spans.append((c, d))
            elif a < end and start < b:
                if tag == "equal":
                    spans.append((c + max(a, start) - a, c + min(b, end) - a))
                elif tag == "replace":
                    if a < start or end < b:
                        return None, None, "ambiguous"
                    spans.append((c, d))
        if not spans:
            return None, None, "removed"
        return spans[0][0], spans[-1][1], "edited"


def _guard_signature(guard):
    kind = guard.get("kind")
    if kind == "predicate":
        return kind, guard["truth"], guard["expression"]
    if kind in {"assertion", "priorExit"}:
        test = guard["predicate"]
        signature = kind, test["truth"], test["text"]
        if kind == "priorExit":
            signature += (guard["exit"]["text"], guard["inWith"])
        return signature
    return kind or "context", guard["text"]


def _conditions(index, refs):
    """Use the same control-flow reader as the graph. Ignore absolute row numbers in comparisons."""
    by_file = defaultdict(list)
    for ref in refs:
        if ref["file"].endswith(".py"):
            by_file[ref["file"]].append(ref)
    result = {}
    for path, ranges in by_file.items():
        wanted = {i for ref in ranges for i in range(ref["line"], ref["end"] + 1)}
        tree = ast.parse(index.files[path], filename=path)
        watched = {node for node in ast.walk(tree) if isinstance(node, (ast.stmt, ast.expr)) and node.lineno in wanted}
        contexts = _CallConditions(path, tree, set(), watch=watched).contexts
        by_line = defaultdict(list)
        for node, guards in contexts.items():
            if guards:
                by_line[node.lineno].append((node.col_offset, type(node).__name__, guards))
        for ref in ranges:
            signature, sources = [], {}
            for i in range(ref["line"], ref["end"] + 1):
                for column, kind, guards in by_line[i]:
                    labels = []
                    for guard in guards:
                        labels.append(_guard_signature(guard))
                        sources[(*_ref_key(guard), guard["text"])] = guard
                    signature.append((i - ref["line"], column, kind, tuple(labels)))
            result[_ref_key(ref)] = (tuple(sorted(signature)), list(sources.values()))
    return result


def _compare_refs(old, current, refs):
    # A byte-identical file at one newly added path can be followed without
    # attributing a deleted file to an unrelated copy that already existed.
    removed, added = defaultdict(list), defaultdict(list)
    for path in old.files.keys() - current.files.keys():
        removed[old.files[path]].append(path)
    for path in current.files.keys() - old.files.keys():
        added[current.files[path]].append(path)
    renames = {names[0]: added[source][0] for source, names in removed.items()
               if len(names) == 1 and len(added.get(source, ())) == 1}

    maps, matches, rows = {}, [], {}
    for ref in refs:
        path, line, end = _ref_key(ref)
        dest = renames.get(path, path)
        old_lines = old.rows(path)
        row = {"old": ref, "new": None, "oldText": "\n".join(old_lines[line - 1:end]), "newText": ""}
        rows[path, line, end] = row
        if dest not in current.files:
            if added.get(old.files[path]):
                row.update(status="unresolved", reason="文件已移动，相同内容的旧文件和新路径无法唯一配对。")
            else:
                row.update(status="missing", reason="旧文件没有出现在当前索引中。")
            continue
        new_lines = current.rows(dest)
        if dest.endswith(".py") and (path not in old.modules or dest not in current.modules):
            row.update(status="unresolved", reason="该 Python 文件在至少一份快照中未成功解析。")
            continue
        # Identical files retain all offsets, including repeated expressions.
        if old.files[path] == current.files[dest]:
            start1, end1, mode = line, end, "match"
        else:
            before, after = (1, len(old_lines)), (1, len(new_lines))
            contained = [n for n in old.definitions[path] if n["line"] <= line and end <= n["end"]]
            if contained:
                owner = min(contained, key=lambda n: n["end"] - n["line"])
                def same_symbol(node):
                    return node["qualname"] == owner["qualname"] and node["kind"] == owner["kind"]
                targets = [n for n in current.definitions[dest] if same_symbol(n)]
                if not targets:
                    row.update(status="missing", reason=f"当前文件中没有原定义 {owner['qualname']}。")
                    continue
                if len(targets) != 1 or sum(same_symbol(n) for n in old.definitions[path]) != 1:
                    row.update(status="unresolved", reason=f"存在多个同名定义 {owner['qualname']}，无法唯一定位。")
                    continue
                before = owner["line"], owner["end"]
                after = targets[0]["line"], targets[0]["end"]
            key = path, dest, before, after
            if key not in maps:
                maps[key] = _LineMap(old_lines[before[0] - 1:before[1]], new_lines[after[0] - 1:after[1]])
            first, last, mode = maps[key].interval(line - before[0], end + 1 - before[0])
            if mode == "removed":
                row.update(status="missing", reason="该引用行段已从原定义位置移除。")
                continue
            if mode == "ambiguous":
                row.update(status="unresolved", reason="引用片段重复或所在代码块被重写，无法唯一对齐新行段。")
                continue
            start1, end1 = after[0] + first, after[0] - 1 + last
        new_ref = _range(dest, start1, end1)
        row.update(new=new_ref, newText="\n".join(new_lines[start1 - 1:end1]))
        if mode == "edited":
            row.update(status="changed", reason="引用的源码行段已改写。")
        else:
            status = "unchanged" if ref == new_ref else "moved"
            row.update(status=status, reason="引用原文仍在原位置。" if status == "unchanged" else "在新位置找到了相同的引用原文。")
            if old.targets(ref, renames) != current.targets(new_ref, {}):
                row.update(status="changed", reason="引用原文相同，该位置的已解析调用或入口目标发生变化。")
            if old.files[path] != current.files[dest]:
                matches.append(row)

    old_guards = _conditions(old, [m["old"] for m in matches])
    new_guards = _conditions(current, [m["new"] for m in matches])
    for row in matches:
        left, old_sources = old_guards.get(_ref_key(row["old"]), ((), []))
        right, new_sources = new_guards.get(_ref_key(row["new"]), ((), []))
        if left != right:
            row.update(status="changed", reason="引用原文仍在，其已识别的执行条件发生变化。",
                       oldConditions=old_sources, newConditions=new_sources)
    return rows


def compare_claim_evidence(previous_graph, current_graph, guide=None):
    """Report on existing citations, without applying or rewriting the previous guide."""
    if not isinstance(previous_graph, dict) or previous_graph.get("schema") != 1 or not all(
            field in previous_graph for field in SNAPSHOT_FIELDS):
        raise ValueError("旧图缺少完整的源码快照，请选择工作台导出的架构 HTML 或图 JSON。")
    signed_fields = {k: previous_graph[k] for k in SNAPSHOT_FIELDS}
    if "callDiagnosticVersion" in previous_graph:
        signed_fields["callDiagnosticVersion"] = previous_graph["callDiagnosticVersion"]
    if previous_graph.get("snapshot") != digest(signed_fields):
        raise ValueError("旧图的源码和结构与快照指纹不一致，请使用原始导出。")
    guide = previous_graph.get("semantics") if guide is None else guide
    if guide is None:
        raise ValueError("旧图中没有业务讲解。请选择带讲解的导出，或用 --semantics 指定同一快照的讲解 JSON。")
    try:
        validate_semantics(previous_graph, guide)
        overview = guide["overview"]
        claims = overview.get("claims") or [overview]
        if not isinstance(claims, list) or any(not claim["refs"] or not isinstance(claim["text"], str) for claim in claims):
            raise ValueError("旧版总览缺少结论正文或源码引用。")
        refs = {_ref_key(r): r for claim in claims for r in claim["refs"]}
        checked = _compare_refs(_Index(previous_graph), _Index(current_graph), refs.values())
    except (KeyError, TypeError, AttributeError, IndexError, SyntaxError) as exc:
        raise ValueError("旧架构图或讲解中的源码引用格式不完整。") from exc

    results, counts = [], dict.fromkeys(STATUS_LABELS, 0)
    for i, claim in enumerate(claims, 1):
        evidence = [checked[_ref_key(ref)] for ref in claim["refs"]]
        states = {r["status"] for r in evidence}
        status = next(s for s in STATUS_LABELS if s in states)
        counts[status] += 1
        results.append({"id": i, "text": claim["text"], "status": status, "refs": evidence})
    return {"schema": SCHEMA, "previousSnapshot": previous_graph["snapshot"],
            "currentSnapshot": current_graph["snapshot"], "counts": counts, "claims": results}
