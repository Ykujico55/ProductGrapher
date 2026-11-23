"""Project source-backed explanations into a navigable business view."""

from collections import defaultdict

from .project import branch, ref


def shared_calls(graph, items):
    """One representative, shortest source-backed path per entry to a shared operation."""
    nodes = {n["id"]: n for n in graph["nodes"]}
    outgoing = defaultdict(list)
    for edge in graph["edges"]:
        if edge["kind"] in {"calls", "handles"} and nodes[edge["target"]]["kind"] in {"method", "function"}:
            outgoing[edge["source"]].append(edge)
    # The graph has a stable edge order. With equal-length paths retain the first.
    shared = defaultdict(list)
    for entry in graph["nodes"]:
        if entry["kind"] != "entry":
            continue
        direct = outgoing[entry["id"]]
        paths = {}
        for edge in direct:
            paths.setdefault(edge["target"], [edge["id"]])
        for first in direct:
            for second in outgoing[first["target"]]:
                if second["kind"] == "calls":
                    paths.setdefault(second["target"], [first["id"], second["id"]])
        for target, edges in paths.items():
            shared[target].append({"entry": entry["id"], "edges": edges})
    explained = {item["nodeId"] for item in items}
    targets = sorted(shared, key=lambda key: (key not in explained, -len(shared[key]), key))
    return [{"target": key, "paths": shared[key]} for key in targets if len(shared[key]) > 1]


def capability_groups(graph, items):
    nodes = {node["id"]: node for node in graph["nodes"]}
    by_name = defaultdict(list)
    for item in items:
        if item.get("capability"):
            by_name[item["capability"]].append(item)

    groups = []
    for label, members in sorted(by_name.items()):
        members.sort(key=lambda item: item["nodeId"])
        keys = [item["nodeId"] for item in members]
        group = branch("capability", label, ["capability", label, keys])
        group["basis"] = "模型根据这些入口和代码符号的实际职责归组。"
        seen = set()
        for item in members:
            node = nodes[item["nodeId"]]
            evidence = [ref(node), *(r for r in item["refs"] if r != ref(node))]
            leaf = branch(node["kind"], node["label"], ["capability-member", label, node["id"]],
                          node_ids=[node["id"]], evidence=evidence)
            group["children"].append(leaf)
            for loc in item["refs"]:
                key = (loc["file"], loc["line"], loc["end"])
                if key not in seen:
                    seen.add(key)
                    group["evidence"].append(loc)
        groups.append(group)
    return groups
