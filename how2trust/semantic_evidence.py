"""Collect implementation and caller context for checking a batch's business statements."""
from collections import defaultdict


def review_material(graph, material, claims, items, limit):
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
    size = 0

    def include(file, start, end, *, required=False):
        nonlocal size
        for num in range(max(1, start), min(end, len(lines[file])) + 1):
            if num not in taken[file]:
                cost = len(lines[file][num - 1]) + 12
                if not required and size + cost > limit:
                    break
                taken[file].add(num)
                size += cost

    # Keep all existing references checkable. Add the context of every cited
    # condition and the calls that may bypass a callee's local policy.
    for r in refs:
        include(r["file"], r["line"], r["end"], required=True)
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
    return {**material, "sources": sources}
