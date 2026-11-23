"""Assemble an evidence-backed product hierarchy without executing repository code."""

from __future__ import annotations

import configparser
import hashlib
import json
import re
from pathlib import Path, PurePosixPath

from .analyzer import analyze
from .source_roles import source_role


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def ref(obj):
    return {key: obj[key] for key in ("file", "line", "end")}


def branch(kind, label, identity, *, node_ids=(), evidence=()):
    return {
        "id": "tree:" + digest(identity)[:24], "kind": kind, "label": label,
        "children": [], "nodeIds": list(node_ids), "evidence": list(evidence),
    }


def source_tree(tree, nodes, roles):
    """Project the same leaves into role-specific sections, retaining source navigation IDs."""
    def partition(item):
        if not item["children"]:
            member = next(iter(item["nodeIds"]), None)
            role = roles[nodes[member]["file"]] if member else "production"
            return {role: item}
        children = {}
        for child in item["children"]:
            for role, part in partition(child).items():
                children.setdefault(role, []).append(part)
        result = {}
        for role, members in children.items():
            evidence = [r for r in item["evidence"] if roles.get(r["file"]) == role or r["file"] == "how2trust.ini"]
            branch_id = item["id"] if role == "production" else "tree:" + digest([role, item["id"]])[:24]
            result[role] = {**item, "id": branch_id, "children": members, "evidence": evidence}
        return result

    sections = partition(tree)
    root = {**tree, "children": list(sections.get("production", {}).get("children", []))}
    for role, label in (("test", "测试与夹具"), ("example", "示例与文档")):
        if role in sections:
            section = branch("group", label, ["source-role", role])
            section["children"] = sections[role]["children"]
            root["children"].append(section)
    return root


def read_mapping(root, files):
    filename = "how2trust.ini"
    location = root / filename
    if location.is_symlink() or not location.is_file():
        return "Python 项目", [], []
    source = location.read_text(encoding="utf-8-sig")
    files[filename] = source
    ini = configparser.ConfigParser(interpolation=None, delimiters=("=",))
    ini.read_string(source, source=filename)
    headers = [(i, match[1]) for i, line in enumerate(source.splitlines(), 1)
               if (match := re.fullmatch(r"\[([^\]]+)\]\s*", line.strip()))]
    proofs = {
        section: {"file": filename, "line": start,
                  "end": headers[index + 1][0] - 1 if index + 1 < len(headers) else len(source.splitlines())}
        for index, (start, section) in enumerate(headers)
    }
    rules = []
    for section in ini.sections():
        if section == "project":
            continue
        if not section.startswith("group:") or section not in proofs:
            raise ValueError(f"{filename} 中无法识别配置段 [{section}]，请使用 [group:业务域/模块]")
        path = tuple(part.strip() for part in section[6:].split("/"))
        if not all(path):
            raise ValueError(f"{filename} 的 [{section}] 中存在空组名")
        for field in ini.options(section):
            if field not in {"entries", "files"}:
                raise ValueError(f"{filename} 的 [{section}] 不支持 {field}")
            for selector in ini.get(section, field).splitlines():
                if selector.strip():
                    rules.append((path, field, selector.strip(), proofs[section]))
    return ini.get("project", "name", fallback="Python 项目"), rules, [proofs["project"]] if "project" in proofs else []


def build_graph(root):
    root = Path(root).resolve(strict=True)
    if not root.is_dir():
        raise ValueError(f"不是目录：{root}")
    graph = analyze(root)
    name, rules, project_evidence = read_mapping(root, graph["files"])
    tree = branch("project", name, ["project"], evidence=project_evidence)
    nodes = {n["id"]: n for n in graph["nodes"]}
    entries = [n for n in nodes.values() if n["kind"] == "entry"]
    modules = {n["file"]: n for n in nodes.values() if n["kind"] == "module"}
    handles = {e["source"]: nodes[e["target"]] for e in graph["edges"] if e["kind"] == "handles"}
    selectors = {}
    for entry in entries:
        handler = handles.get(entry["id"])
        if handler:
            selectors.setdefault(f"{handler['file']}::{handler['qualname']}", []).append(entry)
    by_group = {(): tree}

    def group(path, labels, evidence):
        parent = tree
        for depth, label in enumerate(labels, 1):
            key = path[:depth]
            if key not in by_group:
                by_group[key] = branch("group", label, ["group", key])
                parent["children"].append(by_group[key])
            parent = by_group[key]
            if evidence and evidence not in parent["evidence"]:
                parent["evidence"].append(evidence)
        return parent

    def append(parent, node, membership=None):
        evidence = [ref(node), *node.get("evidence", ())]
        if membership and membership not in evidence:
            evidence.append(membership)
        leaf = branch(node["kind"], node["label"], ["leaf", node["id"]],
                      node_ids=[node["id"]], evidence=evidence)
        parent["children"].append(leaf)

    assigned = {}
    for path, field, selector, proof in rules:
        matches = selectors.get(selector, []) if field == "entries" else [modules[selector]] if selector in modules else []
        if not matches:
            graph["diagnostics"].append({**proof, "message": f"未匹配的分组规则 {field} = {selector}"})
        for node in matches:
            if node["id"] in assigned:
                if assigned[node["id"]] != path:
                    raise ValueError(f"{selector} 同时属于多个显式分组，请为该入口或文件选定一个分组")
                continue
            parent = group(tuple("explicit:" + s for s in path), path, proof)
            append(parent, node, proof)
            assigned[node["id"]] = path

    separate_examples = any(not path.startswith("examples/") for path in modules)
    for entry in entries:
        if entry["id"] in assigned:
            continue
        parent = tree
        path, labels = (), ()
        if separate_examples and entry["file"].startswith("examples/"):
            path, labels = ("examples",), ("examples/",)
            parent = group(path, labels, ref(entry))
        if entry.get("group"):
            label = entry["group"]
            parent = group((*path, "declared:" + label), (*labels, label), ref(entry.get("group_evidence") or entry))
        append(parent, entry, entry.get("group_evidence"))
    for filename, module in sorted(modules.items()):
        if module["id"] in assigned:
            continue
        folders = PurePosixPath(filename).parts[:-1]
        parent = group(("source", *folders), ("源码模块", *folders), ref(module))
        append(parent, module)

    def order(item):
        item["children"].sort(key=lambda c: (c["kind"] != "group", c["label"], c["id"]))
        if item["children"]:
            item["evidence"].sort(key=lambda r: (r["file"], r["line"], r["end"]))
        for child in item["children"]:
            order(child)

    order(tree)
    graph.update(name=name, tree=tree, schema=1)
    for edge in graph["edges"]:
        edge.setdefault("id", "edge:" + digest(edge)[:24])
    graph["snapshot"] = digest(graph)
    # The immutable source snapshot is also used by older saved guides. These
    # navigation layers are derived from it, just like the collaboration view.
    graph["fileRoles"] = {file: source_role(file) for file in graph["files"]}
    graph["sourceTree"] = source_tree(tree, nodes, graph["fileRoles"])
    from .collaboration import collaboration_graph
    graph["collaboration"] = collaboration_graph(graph)
    return graph
