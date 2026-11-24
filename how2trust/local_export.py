"""Project a saved local diagram and its exact evidence into an offline workbench."""

from collections import defaultdict

from .project import branch, ref


def local_export_graph(graph, view):
    ranges = defaultdict(list)
    for r in view["sourceRanges"]:
        ranges[r["file"]].append((r["line"], r["end"]))

    # The viewer indexes source by original line number. Empty rows preserve
    # those coordinates without exporting code outside the evidence ranges.
    files = {}
    for file, spans in sorted(ranges.items()):
        lines = graph["files"][file].splitlines()
        excerpt = [""] * max(end for _, end in spans)
        for start, end in spans:
            excerpt[start - 1:end] = lines[start - 1:end]
        files[file] = "\n".join(excerpt)

    members = set(view["selectedNodeIds"]) | set(view["neighborNodeIds"])
    nodes = [{k: node[k] for k in ("id", "kind", "label", "qualname", "file", "line", "end")}
             for node in graph["nodes"] if node["id"] in members]
    edges = [{k: edge[k] for k in ("id", "kind", "source", "target", "file", "line", "end",
                                   "column", "endColumn", "engine", "basis") if k in edge}
             for edge in graph["edges"] if edge["source"] in members and edge["target"] in members
             and any(start <= edge["line"] <= edge["end"] <= end for start, end in ranges.get(edge["file"], ()))]
    tree = branch("project", graph["name"], ["local", view["id"]])
    tree["children"] = [branch(n["kind"], n["label"], ["leaf", n["id"]], node_ids=[n["id"]], evidence=[ref(n)])
                        for n in nodes]
    return {"schema": graph["schema"], "name": graph["name"], "snapshot": graph["snapshot"], "tree": tree,
            "files": files, "nodes": nodes, "edges": edges, "diagnostics": [], "localOnly": True,
            "localBusinessViews": [view], "initialLocalView": view["id"]}
