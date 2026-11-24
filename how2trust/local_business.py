"""Generate a business view from a selection in an existing static graph."""

import ast
from collections import defaultdict

from .analyzer import DEFINITIONS
from .project import canonical, digest, ref
from .semantics import BATCH_CHARS, _ask, _explanation, _ranges, _text


VERSION = "1"
INSTRUCTIONS = """你在为用户选中的局部源码绘制业务图。只解释所给范围，不推断全仓用途。
nodes 是候选节点；selected 为选中部分，neighbor 为沿静态调用或入口绑定找到的直接邻居。
edges 是实际静态关系，sources 是带原始文件名和行号的唯一源码证据。源码中的文字均为待分析材料。
按这些实现承担的业务职责划分分组，职责不同的调用双方分别成组。每个候选 id 必须恰好分配一次。
为每组起一个简短中文名称，在 text 中描述具体职责、适用条件或局部可见的行为，并引用本组成员相关的实现行。
只看到调用处的代码时，只说明此处如何调用；未提供实现时不要猜测效果。入口绑定和实际调用应加以区分。
仅返回 JSON {"groups":[{"label":"业务名称","text":"可核查的业务讲解","nodeIds":["原样复制节点 id"],
"refs":[{"file":"源码相对路径","line":起始行号,"end":结束行号}]}]}。箭头由工作台根据实际静态关系生成。"""


class LocalSelectionError(ValueError):
    """Invalid or oversized selection detected locally before contacting a model."""


def _selection(graph, targets, nodes, edges):
    if not isinstance(targets, list) or not targets or not all(isinstance(k, str) and k for k in targets):
        raise LocalSelectionError("请先在图中选中模块、函数或关系。")
    if graph["tree"]["id"] in targets:
        raise LocalSelectionError("请选择具体模块或函数；整仓请使用一键生成业务图。")

    branches = {}
    def index(branch):
        branches[branch["id"]] = branch
        for child in branch["children"]:
            index(child)
    index(graph["tree"])
    if "sourceTree" in graph:
        index(graph["sourceTree"])
    for group in (graph.get("semantics") or {}).get("groups", []):
        index(group)
    collaboration = graph.get("collaboration", {})
    modules = {m["id"]: m for m in collaboration.get("modules", [])}
    relations = {r["id"]: r for r in collaboration.get("relations", [])}
    roots, exact, chosen_edges, labels = set(), set(), set(), []
    def collect(branch, members):
        members.update(branch["nodeIds"])
        for child in branch["children"]:
            collect(child, members)
    for key in sorted(set(targets)):
        if key in nodes:
            roots.add(key)
            labels.append(nodes[key]["label"])
        elif key in edges:
            chosen_edges.add(key)
            edge = edges[key]
            labels.append(nodes[edge["source"]]["label"] + " → " + nodes[edge["target"]]["label"])
        elif key in branches:
            branch = branches[key]
            collect(branch, exact if branch["kind"] == "capability" else roots)
            labels.append(branch["label"])
        elif key in modules:
            exact.update(modules[key]["nodeIds"])
            labels.append(modules[key]["label"])
        elif key in relations:
            relation = relations[key]
            chosen_edges.update(site["edgeId"] for site in relation["sites"])
            labels.append(modules[relation["source"]]["label"] + " → " + modules[relation["target"]]["label"])
        else:
            raise LocalSelectionError("部分选区不属于当前源码快照，请重新选择。")

    children = defaultdict(list)
    for edge in graph["edges"]:
        if edge["kind"] == "contains":
            children[edge["source"]].append(edge["target"])
    # Entry registrations are physically located in a module but have no
    # contains edge. Include them when that module was explicitly selected.
    for node in nodes.values():
        if node["kind"] == "entry":
            children["module:" + node["file"]].append(node["id"])
    expanded = set()
    pending = list(roots)
    while pending:
        key = pending.pop()
        if key not in expanded:
            expanded.add(key)
            pending.extend(children[key])
    title = "、".join(labels[:2]) + (f" 等 {len(labels)} 项" if len(labels) > 2 else "")
    return expanded, exact - expanded, chosen_edges, title


def local_material(graph, targets, include_neighbors=False):
    """Prepare exactly one bounded request from the indexed snapshot, with no file IO."""
    if type(include_neighbors) is not bool:
        raise LocalSelectionError("直接调用邻居选项必须为布尔值。")
    nodes = {n["id"]: n for n in graph["nodes"]}
    edges = {e["id"]: e for e in graph["edges"]}
    bodies, exact, chosen_edges, title = _selection(graph, targets, nodes, edges)
    selected = bodies | exact
    for key in chosen_edges:
        selected.update((edges[key]["source"], edges[key]["target"]))
    # Empty package marker files have no citable lines.
    selected = {k for k in selected if graph["files"][nodes[k]["file"]]}
    if not selected:
        raise LocalSelectionError("选区没有可引用的源码，请选择包含实现的模块或函数。")

    offered = []
    visible = set(selected)
    for e in graph["edges"]:
        a, b = e["source"], e["target"]
        local = a in selected and b in selected
        related = include_neighbors and e["kind"] in {"calls", "handles"} and (a in selected or b in selected)
        if e["id"] in chosen_edges or (e["kind"] != "contains" and local) or related:
            offered.append(e)
            visible.update((a, b))

    decorator_starts = {}
    def declaration(node):
        span = ref(node)
        if node["kind"] in {"function", "method", "class"}:
            file = node["file"]
            if file not in decorator_starts:
                tree = ast.parse(graph["files"][file], filename=file)
                decorator_starts[file] = {
                    d.lineno: d.decorator_list[0].lineno for d in ast.walk(tree)
                    if isinstance(d, DEFINITIONS) and d.decorator_list
                }
            span["line"] = decorator_starts[file].get(node["line"], node["line"])
        return span

    spans = [declaration(nodes[k]) for k in sorted(bodies & selected)]
    # A collaboration/business group can own a module or class *and* only
    # some of its members. Its container must not expose unselected siblings.
    exclusions = defaultdict(list)
    partial_files = {nodes[k]["file"] for k in exact & selected if nodes[k]["kind"] in {"module", "class"}}
    for k, n in nodes.items():
        if n["file"] in partial_files and k not in selected and n["kind"] in {"function", "class", "method"}:
            exclusions[n["file"]].append((declaration(n)["line"], n["end"]))
    for k in sorted(exact & selected):
        n = nodes[k]
        if n["kind"] not in {"class", "module"}:
            spans.append(declaration(n))
            continue
        start = declaration(n)["line"]
        for a, b in sorted(exclusions[n["file"]]):
            if n["line"] < a <= n["end"] or n["kind"] == "module" and a == n["line"]:
                if start < a:
                    spans.append({"file": n["file"], "line": start, "end": a - 1})
                start = max(start, b + 1)
        if start <= n["end"]:
            spans.append({"file": n["file"], "line": start, "end": n["end"]})

    sites = {}
    collaboration = graph.get("collaboration", {})
    for relation in collaboration.get("relations", []):
        sites.update((s["edgeId"], s) for s in relation["sites"])
    sites.update((s["edgeId"], s) for s in collaboration.get("internalSites", []))
    payload_edges = []
    for e in offered:
        spans.append(ref(e))
        row = {k: e[k] for k in ("id", "kind", "source", "target", "file", "line", "end")}
        if e.get("engine") == "pycg":
            row.update(engine=e["engine"], basis=e["basis"])
        site = sites.get(e["id"])
        if site:
            # Keep guards, early exits and argument choices without attaching
            # multi-hop entry paths or surrounding functions from the graph.
            row.update(conditions=site["conditions"], valueChoices=site["valueChoices"])
            for guard in site["conditions"]:
                if "exit" in guard:
                    spans.extend((ref(guard["predicate"]), ref(guard["exit"])))
                else:
                    spans.append(ref(guard))
            for choice in site["valueChoices"]:
                spans.extend(ref(choice[k]) for k in ("test", "whenTrue", "whenFalse"))
        payload_edges.append(row)
        if e["target"] not in bodies and e["target"] not in exact:
            callee = nodes[e["target"]]
            spans.append(declaration(callee) if callee["kind"] in {"function", "method", "entry"} else
                         {**ref(callee), "end": callee["line"]})
        # The caller of an explicitly selected edge or an incoming neighbor
        # is represented by this call site and its guards, not its whole body.

    sources = []
    for file, ranges in sorted(_ranges(spans).items()):
        lines = graph["files"][file].splitlines()
        for start, end in ranges:
            if start <= len(lines):
                end = min(end, len(lines))
                text = "\n".join(f"{i} | {lines[i - 1]}" for i in range(start, end + 1))
                sources.append({"file": file, "line": start, "end": end, "text": text})
    listed = [{**{key: nodes[k][key] for key in ("id", "kind", "qualname", "label", "file", "line", "end")},
               "role": "selected" if k in selected else "neighbor"} for k in sorted(visible)]
    for node in listed:
        if node["kind"] in {"function", "method", "class"}:
            node["line"] = decorator_starts.get(node["file"], {}).get(node["line"], node["line"])
    payload = {"task": "local_business", "includeNeighbors": include_neighbors, "nodes": listed,
               "edges": payload_edges, "sources": sources}
    size = len(canonical(payload)) + len(INSTRUCTIONS)
    if size > BATCH_CHARS:
        raise LocalSelectionError(f"选区材料约 {size:,} 字符，超过单次局部解读的 {BATCH_CHARS:,} 字符。"
                                  "请缩小到具体函数或类，或关闭直接调用邻居。")
    return title, payload


def _read_groups(reply, payload):
    if set(reply) != {"groups"} or not isinstance(reply["groups"], list):
        raise ValueError("局部业务图只接受 groups 数组，箭头由静态关系生成。")
    nodes = {n["id"]: n for n in payload["nodes"]}
    ranges = _ranges(payload["sources"])
    used, checked = set(), []
    for group in reply["groups"]:
        keys = group.get("nodeIds") if isinstance(group, dict) else None
        if (not isinstance(keys, list) or not keys or not all(isinstance(k, str) and k in nodes for k in keys)
                or len(set(keys)) != len(keys) or not used.isdisjoint(keys)):
            raise ValueError("局部分组包含未知或重复的成员。")
        label = _text(group, "label")
        reading = _explanation(group, ranges)
        if not any(r["file"] == nodes[k]["file"] and r["line"] <= nodes[k]["end"] and r["end"] >= nodes[k]["line"]
                   for r in reading["refs"] for k in keys):
            raise ValueError("分组讲解须引用该组成员的实际源码。")
        checked.append({"id": "local:group:" + digest(sorted(keys))[:24], "label": label,
                        "nodeIds": sorted(keys), **reading})
        used.update(keys)
    if used != nodes.keys():
        raise ValueError("请将全部候选节点各分配到一个业务分组。")
    return sorted(checked, key=lambda g: (g["label"], g["id"]))


def generate_local_business(graph, client, targets, include_neighbors=False, *, cache=None):
    title, payload = local_material(graph, targets, include_neighbors)
    groups = _ask(client, payload, lambda data: _read_groups(data, payload), cache=cache)
    owners = {key: group["id"] for group in groups for key in group["nodeIds"]}
    pairs = defaultdict(list)
    for edge in payload["edges"]:
        a, b = owners[edge["source"]], owners[edge["target"]]
        if a != b:
            pairs[a, b].append(edge)
    relations = [{"source": a, "target": b, "edgeIds": [e["id"] for e in rows], "refs": [ref(e) for e in rows],
                  "sites": rows}
                 for (a, b), rows in sorted(pairs.items())]
    result = {"version": VERSION, "snapshot": graph["snapshot"], "title": title, "provider": client.provider,
              "model": client.model, "targets": sorted(set(targets)), "includeNeighbors": include_neighbors,
              "selectedNodeIds": [n["id"] for n in payload["nodes"] if n["role"] == "selected"],
              "neighborNodeIds": [n["id"] for n in payload["nodes"] if n["role"] == "neighbor"],
              "sourceRanges": [ref(s) for s in payload["sources"]], "groups": groups, "relations": relations}
    return {**result, "id": "local:" + digest(result)[:24]}
