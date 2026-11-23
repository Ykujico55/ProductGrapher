"""Source-derived reading notes for the implementation cited by each overview claim."""

import ast
from collections import defaultdict

from .collaboration import _CallConditions


FUNCTION = (ast.FunctionDef, ast.AsyncFunctionDef)
COMPREHENSION = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
EXIT = (ast.Return, ast.Raise, ast.Break, ast.Continue)


class _OverviewConditions(_CallConditions):
    def predicate(self, test, truth):
        row = super().predicate(test, truth)
        if truth and isinstance(test, ast.Subscript):
            row["text"] += "（空字典或空列表不会进入此分支）"
        return row


def _filters(node, parent):
    clauses = []
    for clause in node.generators:
        clauses.append(f"从 {ast.unparse(clause.iter)} 取 {ast.unparse(clause.target)}")
        for test in clause.ifs:
            if (isinstance(test, ast.Compare) and len(test.ops) == 1
                    and isinstance(test.ops[0], (ast.In, ast.NotIn))):
                operator = "不在" if isinstance(test.ops[0], ast.NotIn) else "在"
                clauses.append(f"仅保留 {ast.unparse(test.left)} {operator} {ast.unparse(test.comparators[0])} 中的项")
            else:
                clauses.append(f"仅保留满足 {ast.unparse(test)} 的项")
    target = ""
    if isinstance(parent, ast.Assign):
        target = "，结果赋给 " + "、".join(ast.unparse(t) for t in parent.targets)
    return "；".join(clauses) + target


def _scope_index(tree, line_count):
    """Associate body lines with their innermost definition, including nested functions."""
    scopes = [None] * (line_count + 1)
    owners = [node for node in ast.walk(tree) if isinstance(node, (*FUNCTION, ast.ClassDef))]
    for owner in sorted(owners, key=lambda n: n.end_lineno - n.lineno, reverse=True):
        scopes[owner.lineno:owner.end_lineno + 1] = [owner] * (owner.end_lineno - owner.lineno + 1)
    return scopes


def _file_facts(path, source, call_sites):
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError:
        return [], []
    scopes = _scope_index(tree, len(source.splitlines()))
    parent = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    calls = {n: call_sites[(n.lineno, n.col_offset + 1)] for n in parent if isinstance(n, ast.Call)
             and (n.lineno, n.col_offset + 1) in call_sites}
    # A branch can emit output or exit through a library call with no local
    # target. The expression is source evidence even when it is not a graph edge.
    statements = {node.value for node in parent if isinstance(node, ast.Expr)
                  and isinstance(node.value, ast.Call) and node.value not in calls}
    filtered = {n for n in parent if isinstance(n, COMPREHENSION) and any(g.ifs for g in n.generators)}
    watched = filtered | {n for n in parent if isinstance(n, EXIT)} | calls.keys() | statements
    contexts = _OverviewConditions(path, tree, set(), watch=watched).contexts
    result = []
    for node, context in contexts.items():
        owner = scopes[node.lineno]
        # Local guards/iterations are shown before the source action. A return
        # from an earlier branch has its own row at its actual source position.
        guards = [g for g in context if not g["text"].startswith("前方")]
        extend = node in filtered
        if extend:
            action = _filters(node, parent.get(node))
        elif node in calls:
            edges = calls[node]
            recursive = any(edge["source"] == edge["target"] for edge in edges)
            enclosing = parent.get(node)
            returned = isinstance(enclosing, ast.Return)
            if isinstance(enclosing, ast.Raise) and enclosing.exc is node:
                continue
            if recursive and returned:
                extend = True
                action = f"递归调用 {ast.unparse(node.func)}，直接返回其结果；本层后续语句不会执行"
            elif guards:
                action = f"调用 {ast.unparse(node.func)}" + ("，直接返回其结果" if returned else "")
            else:
                continue
        elif node in statements:
            if not guards:
                continue
            action = "源码表达式 " + ast.unparse(node)
        elif guards:
            if isinstance(node, ast.Return):
                if node.value in calls:
                    continue
                action = "执行 return，结束本次函数调用"
            elif isinstance(node, ast.Raise):
                action = "抛出 " + (ast.unparse(node.exc) if node.exc else "当前异常")
            else:
                action = "执行 " + ast.unparse(node) + "，" + (
                    "结束当前迭代" if isinstance(node, ast.Continue) else "离开当前循环")
        else:
            continue
        # Use the call/filter line for source ordering. Include the closest
        # guard in the clickable range, and name all lexical checks in the text.
        start = min(guards[-1]["line"], node.lineno) if guards else node.lineno
        text = "；".join([*(g["text"] for g in guards), action])
        fact = {"file": path, "line": start, "end": node.end_lineno, "text": text}
        result.append((owner, node.lineno, node.end_lineno, extend, fact))
    return scopes, result


def overview_facts(graph, guide):
    """Per claim, report facts in its citations plus filters and recursive returns of those functions."""
    claims = (guide or {}).get("overview", {}).get("claims", [])
    files = {r["file"] for claim in claims for r in claim["refs"]
             if r["file"].endswith(".py") and r["file"] in graph["files"]}
    calls = defaultdict(lambda: defaultdict(list))
    for edge in graph["edges"]:
        if edge["kind"] == "calls" and edge["file"] in files:
            calls[edge["file"]][edge["line"], edge["column"]].append(edge)
    indexed = {path: _file_facts(path, graph["files"][path], calls[path]) for path in files}
    result = []
    for claim in claims:
        rows = {}
        for ref in claim["refs"]:
            path = ref["file"]
            if path not in indexed:
                continue
            scopes, facts = indexed[path]
            start, end = ref["line"], ref["end"]
            owners = {owner for owner in scopes[start:end + 1] if isinstance(owner, FUNCTION)}
            for owner, line, last, extend, fact in facts:
                if not (start <= last and line <= end or extend and owner in owners):
                    continue
                key = (path, fact["line"], line, fact["end"], fact["text"])
                rows[key] = fact
        result.append([rows[key] for key in sorted(rows)])
    return result
