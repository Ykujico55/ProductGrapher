"""Reconcile independently named responsibilities against their members across the project."""

from collections import Counter, defaultdict

from .capabilities import shared_calls


GROUP_INSTRUCTIONS = """你在为整仓生产代码制作业务架构图。groups 列出各批提出的职责及实际成员，
shared 是这些成员间的静态调用或共享入口。先阅读各组的成员、职责、调用方与接收方，再归并指向同一职责的
近义名称和同一业务功能的细分操作。即使只有一个公开入口也要归并内部业务职责。
同一个词出现在两个组名里不足以证明同义。若一组统筹整个流程、另一组只实施某个步骤，应保留调用方与被调用方，
用直观中文指出两者的具体区别。保留检索、配置、持久化、生命周期回调等实际不同的协作职责。
大量初始标签时将图收敛到便于一屏概览的业务模块，以实际成员为准，不凑数。不要发明组中没有的职责。
只输出 JSON {"domains":[{"name":"读者能理解的模块名","groupIds":[1,2]}]}。
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


def merge_groups(graph, items, client, ask):
    nodes = {n["id"]: n for n in graph["nodes"]}
    by_group, entries = defaultdict(list), defaultdict(list)
    for item in items:
        label = item.get("capability")
        if label:
            by_group[label].append(item)
            if nodes[item["nodeId"]]["kind"] == "entry":
                entries[label].append(item)
    if len(by_group) < 2:
        return items
    internal = set(by_group) - entries.keys()
    # When all business groups are already organized around commands, keep the
    # existing command-centric grouping and reserve unrelated implementation labels.
    command_groups = len(entries) > 1 and len(internal) <= 1
    # An already-grouped command family alongside a single literal command
    # needs no renaming pass. Descriptive or competing domain labels still do.
    if (command_groups and len(entries) == 2 and any(len(rows) > 1 for rows in entries.values())
            and any(len(rows) == 1 and label == nodes[rows[0]["nodeId"]]["label"]
                    for label, rows in entries.items())):
        return items
    labels = sorted(entries if command_groups else by_group)
    group_id = {label: index for index, label in enumerate(labels, 1)}
    owners = {}
    brief = []
    for label in labels:
        members = sorted(by_group[label], key=lambda item: item["nodeId"])
        for item in members:
            owners[item["nodeId"]] = group_id[label]
        sample = (entries.get(label) or members)[0]
        row = {"id": group_id[label], "name": label, "entry": nodes[sample["nodeId"]]["label"],
               "purpose": sample["text"]}
        if not command_groups:
            row["members"] = [{"symbol": nodes[m["nodeId"]]["qualname"], "file": nodes[m["nodeId"]]["file"],
                               "title": m["title"]} for m in members]
        brief.append(row)
    reserved = sorted(internal) if command_groups else []
    shared = []
    if command_groups:
        for call in shared_calls(graph, items):
            related = {owners[p["entry"]] for p in call["paths"] if p["entry"] in owners}
            if len(related) > 1:
                shared.append({"operation": nodes[call["target"]]["qualname"], "groupIds": sorted(related)})
    else:
        pairs = Counter((owners[e["source"]], owners[e["target"]]) for e in graph["edges"]
                        if e["kind"] in {"calls", "handles"} and e["source"] in owners and e["target"] in owners
                        and owners[e["source"]] != owners[e["target"]])
        shared = [{"fromGroup": a, "toGroup": b, "callSites": n} for (a, b), n in sorted(pairs.items())]
    payload = {"task": "domains", "groups": brief, "shared": shared, "reservedNames": reserved}
    for attempt in range(2):
        try:
            mapping = _names(ask(client, payload), labels, reserved)
            break
        except ValueError as exc:
            if attempt:
                raise
            payload = {**payload, "validationError": str(exc)}
    return [{**item, "capability": mapping[item["capability"]]}
            if item.get("capability") in mapping else item for item in items]
