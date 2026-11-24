"""Collect implementation and caller context for checking a batch's business statements."""
import ast
from collections import defaultdict

from .collaboration import _CallConditions
from .project import canonical, ref


def needs_batch_review(graph, material):
    if any(n["kind"] == "entry" for n in material["nodes"]):
        return True
    if not any(n["kind"] in {"function", "method"} for n in material["nodes"]):
        return False
    ranges = defaultdict(list)
    for source in material["sources"]:
        if source["file"].endswith(".py"):
            ranges[source["file"]].append((source["line"], source["end"]))
    branches = (ast.If, ast.IfExp, ast.For, ast.AsyncFor, ast.While, ast.Match, ast.ExceptHandler, ast.Assert)
    for file, spans in ranges.items():
        try:
            tree = ast.parse(graph["files"][file], filename=file)
        except SyntaxError:
            continue
        if any(isinstance(n, branches) and any(a <= n.lineno <= b for a, b in spans) for n in ast.walk(tree)):
            return True
    # A callee's first reading can contain the invocation but omit a distant
    # guard, or run out of room before a caller is shown at all.
    wanted = {n["id"] for n in material["nodes"]}
    sites = defaultdict(list)
    for edge in graph["edges"]:
        if edge["kind"] != "calls" or edge["target"] not in wanted:
            continue
        spans = ranges[edge["file"]]
        if not _covered(edge, spans):
            return True
        sites[edge["file"]].append(edge)
    for file, edges in sites.items():
        if _covered({"line": 1, "end": len(graph["files"][file].splitlines())}, ranges[file]):
            continue
        positions = {(e["line"], e["column"]) for e in edges}
        contexts = _CallConditions(file, ast.parse(graph["files"][file]), positions).found
        for edge in edges:
            context = contexts.get((edge["line"], edge["column"]))
            if context and any(not _covered(r, ranges[file]) for r in _action_refs(edge, context)):
                return True
    return False


def _covered(ref, spans):
    return any(a <= ref["line"] and ref["end"] <= b for a, b in spans)


def _action_refs(action, fact):
    spans = [action]
    for guard in fact["conditions"]:
        if "exit" in guard:
            spans.extend((guard["predicate"], guard["exit"]))
        else:
            spans.append(guard)
    for choice in fact["valueChoices"]:
        spans.extend((choice["test"], choice["whenTrue"], choice["whenFalse"]))
    refs = {(s["file"], s["line"], s["end"]): ref(s) for s in spans}
    return list(refs.values())


def action_fact_row(fact):
    """Use the predicate and the exit itself, without citing the lines between them."""
    return {"action": ref(fact["action"]), "text": fact["text"], "refs": _action_refs(fact["action"], fact)}


def _batch_calls(graph, sources, wanted, nodes, refs, *, incoming_evidence):
    calls = [e for e in graph["edges"] if e["kind"] == "calls"]
    wanted = wanted | {e["target"] for e in graph["edges"] if e["kind"] == "handles" and e["source"] in wanted}
    cited = defaultdict(set)
    for r in refs:
        cited[r["file"]].update(range(r["line"], r["end"] + 1))
    targets = {e["target"] for e in calls
               if e["line"] in cited[e["file"]] or e["source"] in wanted or e["target"] in wanted}
    batch = defaultdict(list)
    for source in sources:
        batch[source["file"]].append((source["line"], source["end"]))
    sites = defaultdict(dict)
    for e in calls:
        if e["target"] not in targets:
            continue
        path, line = e["file"], e["line"]
        if line in cited[path]:
            priority = 0
        elif e["source"] in wanted or e["target"] in wanted:
            priority = 1
        elif any(a <= line <= b for a, b in batch[path]):
            priority = 2
        else:
            continue
        # The same syntax can be indexed through a CLI entry and its Python
        # owner. Its physical position and resolved target identify one call.
        key = (line, e["column"], e["target"])
        if key not in sites[path] or priority < sites[path][key][0]:
            sites[path][key] = (priority, e)
    counts = defaultdict(int)
    for file_sites in sites.values():
        for _, _, target in file_sites:
            counts[target] += 1
    rows = []
    for path, file_sites in sites.items():
        positions = {(line, column) for line, column, _ in file_sites}
        parsed = ast.parse(graph["files"][path], filename=path)
        contexts = _CallConditions(path, parsed, positions).found
        for (line, column, target), (priority, e) in file_sites.items():
            context = contexts.get((line, column))
            missing_call = incoming_evidence and target in wanted and not _covered(e, batch[path])
            if context is None or not (context["conditions"] or context["valueChoices"] or counts[target] > 1 or missing_call):
                continue
            texts = [g["text"] for g in context["conditions"]]
            verb = "PyCG 静态推断的调用目标为 " if e.get("engine") == "pycg" else "调用 "
            texts.append(verb + nodes[target]["qualname"])
            texts.extend(choice["text"] for choice in context["valueChoices"])
            row = {**action_fact_row({**context, "action": e, "text": "；".join(texts)}),
                   "column": column, "target": target}
            rows.append(((priority, path, line, column, target), row))
    return [row for _, row in sorted(rows, key=lambda pair: pair[0])]


def review_material(graph, material, claims, items, limit, *, legacy=False, incoming_evidence=True):
    lines = {file: text.splitlines() for file, text in graph["files"].items() if file.endswith(".py") or file == "how2trust.ini"}
    nodes = {n["id"]: n for n in graph["nodes"]}
    refs = [r for row in [*claims, *items] for r in row["refs"]]
    wanted = {item["nodeId"] for item in items}
    for ref in refs:
        containing = [n for n in graph["nodes"] if n["file"] == ref["file"] and n["kind"] in {"method", "function"}
                      and n["line"] <= ref["line"] <= n["end"]]
        if containing:
            wanted.add(min(containing, key=lambda n: n["end"] - n["line"])["id"])
    taken = defaultdict(set)
    draft = {"claims": [{"id": i, **c} for i, c in enumerate(claims, 1)], "items": items}
    review = {**material, "task": "verify", "draft": draft, "sources": [], "actionFacts": []}
    size = 0 if legacy else len(canonical(review))

    def cost(file, num, pending=()):
        if legacy:
            return len(lines[file][num - 1]) + 12
        # Count the actual escaped source text. Reserve a block header at each
        # new run; using the file's last line for its end leaves room to extend it.
        amount = len(canonical(f"{num} | {lines[file][num - 1]}"))
        if num - 1 not in taken[file] and num - 1 not in pending:
            amount += len(canonical({"file": file, "line": num, "end": len(lines[file]), "text": ""})) + 1
        return amount

    def include(file, start, end, *, required=False):
        nonlocal size
        for num in range(max(1, start), min(end, len(lines[file])) + 1):
            if num not in taken[file]:
                amount = cost(file, num)
                if not required and size + amount > limit:
                    break
                taken[file].add(num)
                size += amount

    # Keep all existing references checkable. Add the context of every cited
    # condition and the calls that may bypass a callee's local policy.
    for r in refs:
        include(r["file"], r["line"], r["end"], required=True)
    if not legacy and size < limit:
        for row in _batch_calls(graph, material["sources"], wanted, nodes, refs, incoming_evidence=incoming_evidence):
            missing = defaultdict(set)
            for r in row["refs"]:
                missing[r["file"]].update(n for n in range(r["line"], r["end"] + 1) if n not in taken[r["file"]])
            amount = len(canonical(row)) + 1 + sum(cost(f, n, nums) for f, nums in missing.items() for n in nums)
            if size + amount <= limit:
                size += amount
                review["actionFacts"].append(row)
                for f, nums in missing.items():
                    taken[f].update(nums)
    for edge in graph["edges"]:
        if edge["kind"] in {"calls", "handles"} and edge["target"] in wanted:
            caller = nodes[edge["source"]]
            include(edge["file"], max(caller["line"], edge["line"] - 24), min(caller["end"], edge["line"] + 8))
    for r in refs:
        include(r["file"], r["line"] - 8, r["end"] + 8)
    for key in sorted(wanted):
        node = nodes[key]
        include(node["file"], node["line"], node["end"])
    for block in material["sources"]:
        include(block["file"], block["line"], block["end"])

    sources = []
    for file, nums in sorted(taken.items()):
        numbered = []
        previous = 0
        for num in sorted(nums):
            if not numbered or num != previous + 1:
                if numbered:
                    sources.append({"file": file, "line": start, "end": previous, "text": "\n".join(numbered)})
                start, numbered = num, []
            numbered.append(f"{num} | {lines[file][num - 1]}")
            previous = num
        if numbered:
            sources.append({"file": file, "line": start, "end": previous, "text": "\n".join(numbered)})
    review["sources"] = sources
    return review
