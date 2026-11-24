"""Reconcile proposed business responsibilities with the members that implement them."""

import ast
from collections import Counter, defaultdict
from itertools import islice

from .analyzer import DEFINITIONS
from .capabilities import shared_calls


GROUP_INSTRUCTIONS = """你在为整仓生产代码制作业务架构图。groups 是各批提出的职责及其真实成员。
每个成员的 proof 给出它自己的入口或实现片段。shared 中的调用、共同使用的操作说明这些组怎样协作，
不能仅凭共享调用、相似组名或同属一个目录，判定它们属于同一职责。将工作流编排、持久化、通用支持等不同职责保留为独立组。
若有独立源码依据表明这些成员共同实现同一业务目标，可合并近义名称或相关业务步骤。只输出 JSON：
{"domains":[{"name":"模块名","groupIds":[1,2],"reason":{"text":"这些成员共同实现什么，源码分别体现在哪里",
"nodeIds":["原样复制每个原组至少一个有 proof 的成员 nodeId"]}},{"name":"独立职责","groupIds":[3]}]}。
仅合并时需要 reason。成员的局部实现才支持实现职责，入口声明只支持其公开操作。证据不足就保留独立组。
每个输入组编号恰好分配一次，域名唯一，禁用 reservedNames 中的名称。"""


def _names(answer, labels, reserved):
    domains = answer.get("domains")
    if not isinstance(domains, list):
        raise ValueError("业务归并需要 domains 数组")
    names, used = {}, {label.casefold() for label in reserved}
    for domain in domains:
        if not isinstance(domain, dict) or not isinstance(domain.get("name"), str):
            raise ValueError("业务模块缺少名称")
        label = domain["name"].strip()
        ids = domain.get("groupIds")
        if not label or label.casefold() in used or not isinstance(ids, list) or not ids:
            raise ValueError("业务模块名称重复或缺少原始组编号")
        used.add(label.casefold())
        for index in ids:
            if type(index) is not int or not 1 <= index <= len(labels) or index in names:
                raise ValueError("原始分组编号无效或重复")
            names[index] = label
    if len(names) != len(labels):
        raise ValueError("业务模块未覆盖原始组编号 " + str(sorted(set(range(1, len(labels) + 1)) - names.keys())))
    return {name: names[index] for index, name in enumerate(labels, 1)}


def _call_context(calls, owners):
    # One real call site per original responsibility is enough to demonstrate sharing.
    callers, boundaries = defaultdict(dict), {}
    for edge in calls:
        target = edge["target"]
        source_group, target_group = owners[edge["source"]], owners.get(target)
        callers[target].setdefault(source_group, edge["id"])
        if target_group and source_group != target_group:
            boundaries.setdefault((source_group, target_group), edge)
    return {target: uses for target, uses in callers.items() if len(uses) > 1}, boundaries


def _local_actions(scope):
    """The condition, data operation or effect in this exact scope, in source order."""
    pending = list(reversed(scope.body))
    while pending:
        part = pending.pop()
        if isinstance(part, DEFINITIONS):
            continue
        pending.extend(reversed(list(ast.iter_child_nodes(part))))
        if isinstance(part, (ast.If, ast.While)):
            yield part.test
        elif isinstance(part, (ast.For, ast.AsyncFor)):
            yield part.iter
        elif isinstance(part, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Delete, ast.Raise, ast.Assert)):
            yield part
        elif isinstance(part, ast.Return) and part.value is not None:
            yield part
        elif isinstance(part, ast.Expr) and isinstance(part.value, ast.Call):
            yield part


def _proofs(graph, items, owners, calls):
    """Pick an inspectable local action for each candidate, excluding delegation to other groups."""
    cited = {item["nodeId"]: item.get("refs", []) for item in items}
    # The same source range can contain both an entry registration and a declaration.
    by_file = defaultdict(dict)
    proofs = {}
    for node in graph["nodes"]:
        key = node["id"]
        if key not in owners:
            continue
        file, line = node["file"], node["line"]
        if node["kind"] == "entry":
            proofs[key] = {"file": file, "line": line, "end": line}
        else:
            by_file[file][(node["kind"] == "module", line)] = key
    delegation = defaultdict(set)
    for edge in calls:
        if owners[edge["source"]] != owners.get(edge["target"]):
            delegation[edge["source"]].update(range(edge["line"], edge["end"] + 1))
    for file, selected in by_file.items():
        if not file.endswith(".py"):
            continue
        tree = ast.parse(graph["files"][file], filename=file)
        for scope in ast.walk(tree):
            if not isinstance(scope, (ast.Module, *DEFINITIONS)):
                continue
            key = selected.get((isinstance(scope, ast.Module), getattr(scope, "lineno", 1)))
            if key is None:
                continue
            for action in _local_actions(scope):
                start, end = action.lineno, action.end_lineno
                if not delegation[key].isdisjoint(range(start, end + 1)):
                    continue
                proof = {"file": file, "line": start, "end": min(end, start + 2)}
                if any(r["file"] == file and r["line"] <= start <= r["end"] for r in cited[key]):
                    proofs[key] = proof
                    break
                proofs.setdefault(key, proof)
    return proofs


def _reason(raw, origins, owners, proofs):
    if not isinstance(raw, dict) or not isinstance(raw.get("text"), str) or not raw["text"].strip():
        return None
    ids = raw.get("nodeIds")
    if (not isinstance(ids, list) or not all(isinstance(key, str) and key in proofs for key in ids)
            or {owners[key] for key in ids} != set(origins)):
        return None
    return {"text": raw["text"].strip(), "refs": [
        {"nodeId": key, **proofs[key]} for key in sorted(set(ids))]}


def _evidence(owners, mapping, shared, boundaries, reasons):
    evidence, members = {}, defaultdict(list)
    for key, origin in owners.items():
        members[origin].append(key)
    for origin, keys in sorted(members.items()):
        record = evidence.setdefault(mapping[origin], {"origins": [], "sharedTargets": []})
        record["origins"].append({"label": origin, "nodeIds": sorted(keys)})
    for target, uses in sorted(shared.items()):
        by_destination = defaultdict(list)
        for origin, edge_id in sorted(uses.items()):
            by_destination[mapping[origin]].append(edge_id)
        destinations = set(by_destination)
        if target in owners:
            destinations.add(mapping[owners[target]])
        for destination in sorted(destinations):
            local = by_destination[destination]
            other = (edge for group, edges in by_destination.items() if group != destination for edge in edges)
            examples = local + list(islice(other, 1 if local else 2))
            evidence[destination]["sharedTargets"].append({"target": target, "edgeIds": examples})
    for (caller, receiver), edge in sorted(boundaries.items()):
        for label in dict.fromkeys((mapping[caller], mapping[receiver])):
            evidence[label].setdefault("boundaryCalls", []).append(edge["id"])
    for label, reason in reasons.items():
        evidence[label]["reason"] = reason
    return evidence


def group_decisions(graph, items, client, ask=None, *, cache=None):
    nodes = {n["id"]: n for n in graph["nodes"]}
    by_group, entries = defaultdict(list), defaultdict(list)
    owners = {}
    for item in items:
        label = item.get("capability")
        if label:
            key = item["nodeId"]
            owners[key] = label
            by_group[label].append(item)
            if nodes[key]["kind"] == "entry":
                entries[label].append(item)
    identity = {label: label for label in by_group}
    calls = [e for e in graph["edges"] if e["kind"] == "calls" and e["source"] in owners]
    shared, boundaries = _call_context(calls, owners)
    internal = set(by_group) - entries.keys()
    command_groups = len(entries) > 1 and len(internal) <= 1
    # Preserve an already-grouped command family next to a single literal command.
    if (len(by_group) < 2 or command_groups and len(entries) == 2 and any(len(rows) > 1 for rows in entries.values())
            and any(len(rows) == 1 and label == nodes[rows[0]["nodeId"]]["label"] for label, rows in entries.items())):
        return items, _evidence(owners, identity, shared, boundaries, {})

    labels = sorted(entries if command_groups else by_group)
    group_id = {label: index for index, label in enumerate(labels, 1)}
    proofs = _proofs(graph, items, owners, calls)
    lines = {file: graph["files"][file].splitlines() for file in {p["file"] for p in proofs.values()}}
    brief, offered = [], {}
    for label in labels:
        members = sorted(by_group[label], key=lambda item: item["nodeId"])
        sample = (entries.get(label) or members)[0]
        row = {"id": group_id[label], "name": label, "entry": nodes[sample["nodeId"]]["label"],
               "purpose": sample["text"], "members": []}
        # Keep the full inventory, with a small number of source previews per
        # original group. Show both the implementation and the entry when available.
        candidates = [m["nodeId"] for m in members if m["nodeId"] in proofs]
        implementations = [k for k in candidates if nodes[k]["kind"] != "entry"]
        entry_ids = [k for k in candidates if nodes[k]["kind"] == "entry"]
        previews = set(islice(dict.fromkeys(implementations[:1] + entry_ids[:1] + candidates), 3))
        for item in members:
            key = item["nodeId"]
            node = nodes[key]
            member = {"nodeId": key, "symbol": node["qualname"], "file": node["file"],
                      "kind": node["kind"], "title": item["title"]}
            if key in previews:
                p = proofs[key]
                excerpt = "\n".join(lines[p["file"]][p["line"] - 1:p["end"]])
                member["proof"] = {"line": p["line"], "end": p["end"], "code": excerpt[:300]}
                offered[key] = p
            row["members"].append(member)
        brief.append(row)

    reserved = sorted(internal) if command_groups else []
    shared_rows = []
    if command_groups:
        for call in shared_calls(graph, items):
            related = {group_id[owners[p["entry"]]] for p in call["paths"]
                       if owners.get(p["entry"]) in group_id}
            if len(related) > 1:
                shared_rows.append({"operation": nodes[call["target"]]["qualname"], "groupIds": sorted(related)})
    else:
        pairs = Counter((group_id[owners[e["source"]]], group_id[owners[e["target"]]]) for e in graph["edges"]
                        if e["kind"] in {"calls", "handles"} and e["source"] in owners and e["target"] in owners
                        and owners[e["source"]] != owners[e["target"]])
        for (a, b), n in sorted(pairs.items()):
            row = {"fromGroup": a, "toGroup": b, "callSites": n}
            edge = boundaries.get((labels[a - 1], labels[b - 1]))
            if edge:
                row["example"] = {k: edge[k] for k in ("source", "target", "file", "line", "end")}
            shared_rows.append(row)
        for target, uses in sorted(shared.items()):
            shared_rows.append({"operation": nodes[target]["qualname"], "groupIds": sorted(group_id[g] for g in uses),
                                "edgeIds": [uses[g] for g in sorted(uses)]})
    payload = {"task": "domains", "groups": brief, "shared": shared_rows, "reservedNames": reserved}
    def read(answer):
        return answer, {**identity, **_names(answer, labels, reserved)}
    if ask is None:
        from .semantics import _ask
        answer, mapping = _ask(client, payload, read, cache=cache)
    else:
        for attempt in range(2):
            try:
                answer, mapping = read(ask(client, payload))
                break
            except ValueError as exc:
                if attempt:
                    raise
                payload = {**payload, "validationError": str(exc)}

    reasons, used = {}, {name.casefold() for name in reserved}
    for domain in answer["domains"]:
        origins = [labels[i - 1] for i in domain["groupIds"]]
        name = mapping[origins[0]]
        kept_names = [name]
        if len(origins) > 1:
            reason = _reason(domain.get("reason"), origins, owners, offered)
            if reason:
                reasons[name] = reason
            else:
                mapping.update({origin: origin for origin in origins})
                kept_names = origins
        if any(label.casefold() in used for label in kept_names):
            # A proposed rename now collides with an original name kept for lack of evidence.
            return items, _evidence(owners, identity, shared, boundaries, {})
        used.update(label.casefold() for label in kept_names)
    projected = [{**item, "capability": mapping[item["capability"]]} if item.get("capability") else item
                 for item in items]
    return projected, _evidence(owners, mapping, shared, boundaries, reasons)


def merge_groups(graph, items, client, ask):
    return group_decisions(graph, items, client, ask)[0]


def validate_group_evidence(graph, items, evidence):
    """Replay membership and call references from this exact snapshot."""
    actual = {item["nodeId"]: item["capability"] for item in items if item.get("capability")}
    if not isinstance(evidence, dict) or set(evidence) != set(actual.values()):
        raise ValueError("业务分组依据与最终分组不一致")
    origins, mapping = {}, {}
    for label, record in evidence.items():
        if not isinstance(record, dict) or not isinstance(record.get("origins"), list):
            raise ValueError("业务分组缺少原始职责")
        for origin in record["origins"]:
            if not isinstance(origin, dict):
                raise ValueError("业务分组原始职责格式错误")
            name, keys = origin.get("label"), origin.get("nodeIds")
            if (not isinstance(name, str) or not name.strip() or name in mapping or not isinstance(keys, list)
                    or not keys or any(not isinstance(k, str) or k in origins or actual.get(k) != label for k in keys)):
                raise ValueError("业务分组原始职责与成员不一致")
            mapping[name] = label
            origins.update(dict.fromkeys(keys, name))
    if origins.keys() != actual.keys():
        raise ValueError("业务分组依据缺少成员")
    calls = [e for e in graph["edges"] if e["kind"] == "calls" and e["source"] in origins]
    reasons = {}
    if any(len(record["origins"]) > 1 for record in evidence.values()):
        proofs = _proofs(graph, items, origins, calls)
        for label, record in evidence.items():
            names = [origin["label"] for origin in record["origins"]]
            if len(names) < 2:
                continue
            reason = record.get("reason")
            if not isinstance(reason, dict) or not isinstance(reason.get("refs"), list):
                raise ValueError("合并业务分组缺少职责依据")
            checked = _reason({"text": reason.get("text"), "nodeIds": [r.get("nodeId") for r in reason["refs"]
                                                                         if isinstance(r, dict)]}, names, origins, proofs)
            if checked is None:
                raise ValueError("合并业务分组缺少各原始职责的成员依据")
            reasons[label] = checked
    shared, boundaries = _call_context(calls, origins)
    if evidence != _evidence(origins, mapping, shared, boundaries, reasons):
        raise ValueError("业务分组的源码依据或共享调用与快照不符")
