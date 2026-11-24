"""Attach PyCG's explicit call sites to the workbench's indexed syntax."""

import ast
from collections import defaultdict


def _target_ids(index, document):
    """PyCG collapses redefinitions under one name, so verify uniqueness locally."""
    modules = document["modules"]
    file_modules = {module["file"]: name for name, module in modules.items()}
    by_name = defaultdict(list)
    for node in index.nodes.values():
        if node["file"] in file_modules and node["kind"] in {"class", "method", "function"}:
            module = file_modules[node["file"]]
            by_name[f"{module}.{node['qualname']}"].append(node)

    targets = {}
    for name, nodes in by_name.items():
        definition = document["definitions"].get(name)
        if len(nodes) != 1 or definition is None:
            continue
        node = nodes[0]
        kind = "class" if node["kind"] == "class" else "function"
        if (definition["kind"] == kind
                and modules[definition["module"]]["file"] == node["file"]
                and definition["lines"] == [node["line"], node["end"]]):
            targets[name] = node["id"]
    return targets


def _blocked_binding(index, scope, callee):
    # Preserve the native binding's lifetime even if PyCG sees only the
    # callable branch (e.g. an assignment inside a try or if).
    base = callee
    while isinstance(base, (ast.Attribute, ast.Subscript, ast.Call)):
        base = base.func if isinstance(base, ast.Call) else base.value
    if not isinstance(base, ast.Name):
        return False
    owner = scope
    while owner:
        if base.id in owner.bindings:
            use = base if owner is scope else None
            binding = owner.binding_at(base.id, use, index.reaching_definition)
            if binding is None or binding.kind == "caught":
                return True
            return binding.kind == "unknown" and not isinstance(binding.expr, ast.arg)
        owner = owner.parent
        while owner and isinstance(owner.tree, ast.ClassDef):
            owner = owner.parent
    return False


def _shadowed_method(index, scope, callee, target):
    # A stored instance attribute shadows a method, including a method a
    # subclass defines. PyCG can return just the method when the value is unknown.
    if not isinstance(callee, ast.Attribute) or target["kind"] != "method" or callee.attr != target["label"]:
        return False
    ref = index.resolve(scope, callee.value)
    if not ref or ref[0] not in {"node", "instance"}:
        return False
    receiver = index.scopes[ref[1]]
    if not isinstance(receiver.tree, ast.ClassDef):
        return False
    return any(owner and ((owner.id, callee.attr) in index.replaced
                          or ref[0] == "instance" and callee.attr in owner.attributes)
               for owner in index.class_chain(receiver, frozenset()))


def merge_pycg(index, document):
    """Add unique local candidates for unresolved explicit calls. Return additions."""
    if document["format"] != "pycg/1":
        raise ValueError("不支持的 PyCG 调用图格式")
    targets = _target_ids(index, document)
    candidates = defaultdict(set)
    for caller, sites in document["calls"].items():
        owner = document["definitions"][caller]["module"]
        for site in sites:
            if "implicit" in site:
                continue
            module = site.get("module", owner)
            file = document["modules"][module]["file"]
            pos = (file, site["line"], site["col"], *site["end"])
            if pos in index.unresolved_calls:
                # Keep unknown, external and ambiguous alternatives ineligible.
                callees = site["callees"]
                target = targets.get(callees[0]) if len(callees) == 1 and "unresolved" not in site else None
                candidates[pos].add(target)

    cleared = set()
    for pos, possible in candidates.items():
        if len(possible) != 1 or None in possible:
            continue
        target, = possible
        scope, expr, diagnostic = index.unresolved_calls[pos]
        # The name can resolve to the declaration even when an external decorator
        # replaced its callable (e.g. click.group returns a Command object).
        if index.resolve(scope, expr.func) == ("node", target) and not index.callable_node(("node", target)):
            continue
        if _blocked_binding(index, scope, expr.func) or _shadowed_method(index, scope, expr.func, index.nodes[target]):
            continue
        del index.unresolved_calls[pos]
        index.add_edge(scope.id, target, "calls", scope.path, expr, engine="pycg",
                       basis="PyCG 静态推断（仓内唯一候选）", expression=ast.unparse(expr.func),
                       endColumn=expr.end_col_offset)
        cleared.add(id(diagnostic))
    index.diagnostics[:] = [item for item in index.diagnostics if id(item) not in cleared]
    return len(cleared)
