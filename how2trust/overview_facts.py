"""Source-derived reading notes for the implementation cited by each overview claim."""

import ast
from collections import defaultdict
from itertools import groupby

from .collaboration import _CallConditions


FUNCTION = (ast.FunctionDef, ast.AsyncFunctionDef)
COMPREHENSION = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
EXIT = (ast.Return, ast.Raise, ast.Break, ast.Continue)


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
    assignments = {n for n in parent if isinstance(n, (ast.Assign, ast.AnnAssign)) and isinstance(n.value, ast.IfExp)}
    watched = assignments | filtered | {n for n in parent if isinstance(n, EXIT)} | calls.keys() | statements
    analysis = _CallConditions(path, tree, set(), watch=watched)
    result = []
    for node, context in analysis.contexts.items():
        owner = scopes[node.lineno]
        if isinstance(node, ast.Call):
            choices = analysis.call_arguments(node)
        elif node in assignments or isinstance(node, ast.Return):
            choices = analysis.value_choices([node.value])
        else:
            choices = []
        extend = node in filtered
        if extend:
            action = _filters(node, parent.get(node))
        elif node in assignments:
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            action = "赋值给 " + "、".join(ast.unparse(target) for target in targets)
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
            elif context or choices:
                action = f"调用 {ast.unparse(node.func)}" + ("，直接返回其结果" if returned else "")
            else:
                continue
        elif node in statements:
            if not (context or choices):
                continue
            action = "源码表达式 " + ast.unparse(node)
        elif context or choices:
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
        # Keep earlier exits and assertions individually clickable, without stretching this
        # action's source range across every intervening statement.
        guards = [g for g in context if g.get("kind") not in {"priorExit", "assertion"}]
        start = min(guards[-1]["line"], node.lineno) if guards else node.lineno
        text = "；".join([*(g["text"] for g in context), action, *(c["text"] for c in choices)])
        fact = {"file": path, "line": start, "end": node.end_lineno, "text": text,
                "conditions": context, "action": analysis.fact(action, node), "valueChoices": choices}
        result.append((owner, node.lineno, node.end_lineno, extend, fact))
    return scopes, result


def _supporting_spans(fact):
    for guard in fact["conditions"]:
        if guard.get("kind") == "priorExit":
            test = guard["predicate"]
            truth = "真" if test["truth"] else "假"
            yield "condition", test, f"{test['text']} 为{truth}时进入此前分支"
            yield "exit", guard["exit"], guard["exit"]["text"]
        else:
            yield "condition", guard, guard["text"]
    for choice in fact["valueChoices"]:
        for key, label in (("test", "取值条件"), ("whenTrue", "为真时取"), ("whenFalse", "为假时取")):
            part = choice[key]
            yield "value", part, f"{label} {part['text']}"


def _uncovered_evidence(fact, cited):
    """Return only the uncited runs of lines in the action's local supporting evidence."""
    missing = {}
    for role, span, text in _supporting_spans(fact):
        numbers = range(span["line"], span["end"] + 1)
        for covered, group in groupby(numbers, key=cited.__contains__):
            if covered:
                continue
            lines = list(group)
            key = (span["file"], lines[0], lines[-1])
            _, texts = missing.setdefault(key, (role, []))
            if text not in texts:
                texts.append(text)
    return [{"role": role, "file": path, "line": start, "end": end, "text": "；".join(texts)}
            for (path, start, end), (role, texts) in sorted(missing.items())]


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
        cited_by_file = defaultdict(set)
        for ref in claim["refs"]:
            path = ref["file"]
            if path in indexed:
                scopes, _ = indexed[path]
                cited_by_file[path].update(range(len(scopes))[ref["line"]:ref["end"] + 1])
        rows = {}
        for path, cited in cited_by_file.items():
            scopes, facts = indexed[path]
            owners = {scopes[n] for n in cited if isinstance(scopes[n], FUNCTION)}
            for owner, line, last, extend, fact in facts:
                cited_action = not cited.isdisjoint(range(line, last + 1))
                if not (cited_action or extend and owner in owners):
                    continue
                key = (path, fact["line"], line, fact["end"], fact["text"])
                missing = _uncovered_evidence(fact, cited) if cited_action else []
                rows[key] = {**fact, "uncoveredEvidence": missing} if missing else fact
        result.append([rows[key] for key in sorted(rows)])
    return result
