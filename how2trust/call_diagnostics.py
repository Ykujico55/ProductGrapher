"""Describe explicit call sites that have no edge to a repository definition."""

import ast
import json
from collections import defaultdict
from functools import lru_cache
from pathlib import Path
from zipfile import ZipFile


STUB_ARCHIVE = Path(__file__).with_name("_vendor") / "pycg.zip"
STUB_DIR = "pycg/analysis/stub_data/"
LABELS = {"builtin": "Python 内置调用", "stdlib": "标准库调用", "external": "仓外导入调用"}
CONTAINERS = {ast.List: "list", ast.Dict: "dict", ast.Set: "set", ast.Tuple: "tuple"}


@lru_cache(maxsize=1)
def _stdlib_roots():
    # Use the bundled typeshed catalog across interpreter versions. Two support
    # packages ship beside its stdlib stubs but are not Python library modules.
    with ZipFile(STUB_ARCHIVE) as archive:
        return {name[len(STUB_DIR):-5] for name in archive.namelist()
                if name.startswith(STUB_DIR) and name.endswith(".json")
                and "/" not in name[len(STUB_DIR):]} - {"_typeshed", "typing_extensions"}


@lru_cache(maxsize=32)
def _stub_modules(root):
    with ZipFile(STUB_ARCHIVE) as archive:
        return json.loads(archive.read(f"{STUB_DIR}{root}.json"))["modules"]


def _binding(index, scope, name):
    """Use the same lexical search and reaching definition as Index.resolve."""
    owner = scope
    while owner:
        if owner.star_imports or name.id in owner.bindings:
            use = name if owner is scope else None
            return owner, owner.binding_at(name.id, use, index.reaching_definition)
        owner = owner.parent
        while owner and isinstance(owner.tree, ast.ClassDef):
            owner = owner.parent
    return None, None


def _local_roots(index):
    roots = {name.partition(".")[0] for name in index.modules}
    # Include unreadable modules and src-layout packages before calling an
    # import external, even when no definition from that package was indexed.
    paths = set(index.files) | {d["file"] for d in index.diagnostics if d.get("file")}
    for path in paths:
        if not path.endswith(".py"):
            continue
        parts = Path(path).parts
        roots.add(Path(parts[0]).stem)
        for i, part in enumerate(parts[:-1]):
            if part == "src":
                roots.add(Path(parts[i + 1]).stem)
    if "__init__.py" in paths:
        roots.add(index.root.name)
    return roots


def _outside_sites(document, pending):
    sites = defaultdict(set)
    if document:
        for caller, calls in document["calls"].items():
            owner = document["definitions"][caller]["module"]
            for call in calls:
                if "implicit" in call:
                    continue
                file = document["modules"][call.get("module", owner)]["file"]
                pos = (file, call["line"], call["col"], *call["end"])
                if pos not in pending:
                    continue
                candidates = call["callees"]
                target = candidates[0] if len(candidates) == 1 and "unresolved" not in call else None
                sites[pos].add(target if target in document["outside"] else None)
    return sites


def _receiver_type(index, scope, expr, local, written, seen=frozenset()):
    """Accept literal container types or a single typeshed factory return type."""
    if type(expr) in CONTAINERS:
        return "builtins." + CONTAINERS[type(expr)]
    if isinstance(expr, ast.Name):
        owner, binding = _binding(index, scope, expr)
        if binding and binding.kind == "value" and binding not in seen:
            return _receiver_type(index, owner, binding.value, local, written, seen | {binding})
    if isinstance(expr, ast.Call):
        if isinstance(expr.func, ast.Name) and expr.func.id in CONTAINERS.values():
            path = "builtins." + expr.func.id
            if _binding(index, scope, expr.func)[0] is None and path not in written:
                return path
        ref = index.resolve(scope, expr.func)
        if ref and ref[0] == "path" and ref[1] not in written:
            module, _, func = ref[1].rpartition(".")
            root = module.partition(".")[0]
            if root not in local and root in _stdlib_roots():
                stub = _stub_modules(root).get(module, {}).get("names", {}).get(func, {})
                returns = [s.get("r") for s in stub.get("s", ())]
                if (returns and isinstance(returns[0], str) and returns[0].startswith(root + ".")
                        and all(t == returns[0] for t in returns)):
                    return returns[0]
    return None


def _target(index, scope, func, candidates, local, written, rebound):
    ref = index.resolve(scope, func)
    if ref and ref[0] == "path":
        path = ref[1]
        root = path.partition(".")[0]
        if root.isidentifier() and root not in local and path not in written:
            if root == "builtins":
                return "builtin", path
            return ("stdlib" if root in _stdlib_roots() else "external"), path
    if isinstance(func, ast.Name):
        if _binding(index, scope, func)[0] is None and "builtins." + func.id not in written:
            names = _stub_modules("builtins")["builtins"]["names"]
            if names.get(func.id, {}).get("k") in {"f", "c"}:
                return "builtin", "builtins." + func.id
    if isinstance(func, ast.Attribute):
        if isinstance(func.value, ast.Name) and (_binding(index, scope, func.value)[1], func.attr) in rebound:
            return None
        receiver = _receiver_type(index, scope, func.value, local, written)
        if receiver and receiver.split(".")[0] not in local:
            path = receiver + "." + func.attr
            if receiver.startswith("builtins."):
                name = receiver.removeprefix("builtins.")
                info = _stub_modules("builtins")["builtins"]["names"].get(name, {})
                if info.get("m", {}).get(func.attr, {}).get("k") == "f":
                    return "builtin", path
            elif candidates == {path} and receiver.split(".")[0] in _stdlib_roots():
                return "stdlib", path
    return None


def classify_call_diagnostics(index, document=None):
    """Annotate diagnostic evidence; never introduce an external graph edge."""
    if not index.unresolved_calls:
        return
    sites = _outside_sites(document, index.unresolved_calls)
    local = _local_roots(index)
    written = set()
    rebound = set()
    for scope, attr in index.writes:
        ref = index.resolve(scope, attr)
        if ref and ref[0] == "path":
            written.add(ref[1])
        if isinstance(attr.value, ast.Name):
            rebound.add((_binding(index, scope, attr.value)[1], attr.attr))
    for pos, (scope, call, record) in index.unresolved_calls.items():
        expression = ast.unparse(call.func)
        record.update(category="unresolved", expression=expression, column=call.col_offset + 1)
        target = _target(index, scope, call.func, sites.get(pos), local, written, rebound)
        if target:
            category, path = target
            record.update(category=category, targetName=path, message=f"{LABELS[category]} · {path}")
