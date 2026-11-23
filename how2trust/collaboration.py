"""Project indexed production calls onto modules, preserving each call site's local control flow."""

import ast
from collections import defaultdict

from .project import digest, ref
from .source_roles import source_role


class _CallConditions:
    def __init__(self, file, tree, positions, *, watch=()):
        self.file = file
        self.positions = positions
        self.found = {}
        self.watch = watch
        self.contexts = {}
        # Only recognize sys.exit when this file imports that name without any
        # competing declaration. Ordinary calls that happen to be named exit
        # cannot establish that a branch terminates.
        bindings = defaultdict(list)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    bindings[alias.asname or alias.name.split(".")[0]].append(node)
            elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
                bindings[node.id].append(node)
            elif isinstance(node, ast.arg):
                bindings[node.arg].append(node)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                bindings[node.name].append(node)
        self.exit_functions = set()
        for node in tree.body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.asname or alias.name
                    if alias.name == "sys" and bindings[name] == [node]:
                        self.exit_functions.add(name + ".exit")
            if isinstance(node, ast.ImportFrom) and node.module == "sys" and not node.level:
                for alias in node.names:
                    name = alias.asname or alias.name
                    if alias.name == "exit" and bindings[name] == [node]:
                        self.exit_functions.add(name)
        self.block(tree.body, [])

    def fact(self, text, node, end=None):
        return {"text": text, "file": self.file, "line": node.lineno,
                "end": end if end is not None else node.end_lineno}

    def predicate(self, test, truth):
        return self.fact(f"在第 {test.lineno} 行判断为{'真' if truth else '假'}：{ast.unparse(test)}", test)

    def exit_from(self, statements):
        if statements:
            tail = statements[-1]
            for cls, keyword in ((ast.Return, "return"), (ast.Raise, "raise"),
                                 (ast.Continue, "continue"), (ast.Break, "break")):
                if isinstance(tail, cls):
                    return keyword
            if isinstance(tail, ast.Expr) and isinstance(tail.value, ast.Call):
                name = ast.unparse(tail.value.func)
                if name in self.exit_functions:
                    return name
        return None

    def block(self, statements, inherited):
        active = list(inherited)
        for statement in statements:
            self.visit(statement, active)
            if isinstance(statement, ast.If):
                active.extend(self.forward_guards(statement))
            elif isinstance(statement, (ast.With, ast.AsyncWith)):
                # A return inside a context manager still leaves the enclosing
                # function. An exception may be suppressed by __exit__, so never
                # propagate a raise/exit guard across this boundary.
                for child in statement.body:
                    if isinstance(child, ast.If):
                        active.extend(self.forward_guards(child, in_with=True))

    def forward_guards(self, statement, in_with=False):
        for body, truth in ((statement.body, "真"), (statement.orelse, "假")):
            keyword = self.exit_from(body)
            if keyword and (not in_with or keyword in {"return", "continue", "break"}):
                when = "前方 with 块内" if in_with else "前方"
                yield self.fact(f"{when}分支判断为{truth}且执行至末尾时走向 {keyword}：{ast.unparse(statement.test)}",
                                statement.test, body[-1].end_lineno)

    def visit(self, node, active):
        if node in self.watch:
            self.contexts[node] = list(active)
        if isinstance(node, ast.Call):
            key = (node.lineno, node.col_offset + 1)
            if key in self.positions:
                self.found[key] = list(active)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            for field, value in ast.iter_fields(node):
                if field != "body":
                    self.visit_field(value, active)
            self.block(node.body, [])
        elif isinstance(node, ast.Lambda):
            self.visit(node.args, active)
            self.visit(node.body, [])
        elif isinstance(node, ast.If):
            self.visit(node.test, active)
            self.block(node.body, [*active, self.predicate(node.test, True)])
            self.block(node.orelse, [*active, self.predicate(node.test, False)])
        elif isinstance(node, ast.IfExp):
            self.visit(node.test, active)
            self.visit(node.body, [*active, self.predicate(node.test, True)])
            self.visit(node.orelse, [*active, self.predicate(node.test, False)])
        elif isinstance(node, ast.BoolOp):
            running = list(active)
            for value in node.values:
                self.visit(value, running)
                running.append(self.predicate(value, isinstance(node.op, ast.And)))
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
            test = node.test if isinstance(node, ast.While) else node.iter
            self.visit(test, active)
            if isinstance(node, ast.While):
                condition = self.predicate(test, True)
            else:
                condition = self.fact("循环每次取得一项：" + ast.unparse(node.target) + " in " + ast.unparse(test), test)
                self.visit(node.target, [*active, condition])
            self.block(node.body, [*active, condition])
            self.block(node.orelse, [*active, self.fact("循环正常结束，进入 else", node, test.end_lineno)])
        elif isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
            running = list(active)
            for clause in node.generators:
                self.visit(clause.iter, running)
                running.append(self.fact("推导式每次取得一项：" + ast.unparse(clause.target) + " in " +
                                         ast.unparse(clause.iter), clause.iter))
                for test in clause.ifs:
                    self.visit(test, running)
                    running.append(self.predicate(test, True))
            for expr in ((node.key, node.value) if isinstance(node, ast.DictComp) else (node.elt,)):
                self.visit(expr, running)
        elif isinstance(node, ast.Match):
            self.visit(node.subject, active)
            for case in node.cases:
                matched = [*active, self.fact("匹配分支：" + ast.unparse(case.pattern), case.pattern)]
                if case.guard:
                    self.visit(case.guard, matched)
                    matched.append(self.predicate(case.guard, True))
                self.block(case.body, matched)
        elif isinstance(node, ast.ExceptHandler):
            if node.type:
                self.visit(node.type, active)
            name = ast.unparse(node.type) if node.type else "所有异常"
            self.block(node.body, [*active, self.fact("进入异常处理：" + name, node,
                                                      node.type.end_lineno if node.type else node.lineno)])
        elif isinstance(node, (ast.Try, getattr(ast, "TryStar", ast.Try))):
            self.block(node.body, active)
            for handler in node.handlers:
                self.visit(handler, active)
            self.block(node.orelse, [*active, self.fact("try 块正常结束，进入 else", node, node.lineno)])
            self.block(node.finalbody, [*active, self.fact("进入 finally 清理分支", node, node.lineno)])
        else:
            for _, value in ast.iter_fields(node):
                self.visit_field(value, active)

    def visit_field(self, value, active):
        if isinstance(value, ast.AST):
            self.visit(value, active)
        elif isinstance(value, list):
            if value and isinstance(value[0], ast.stmt):
                self.block(value, active)
            else:
                for item in value:
                    if isinstance(item, ast.AST):
                        self.visit(item, active)


def collaboration_graph(graph, guide=None):
    """Build a deterministic view. Only existing call/handler edge IDs can back a relation."""
    guide = guide if guide is not None else graph.get("semantics") or {}
    roles = graph.get("fileRoles", {})
    production = {file for file in graph["files"] if (roles.get(file) or source_role(file)) == "production"}
    nodes = {node["id"]: node for node in graph["nodes"] if node["file"] in production}
    groups = {leaf_id: group for group in guide.get("groups", [])
              for child in group["children"] for leaf_id in child["nodeIds"] if leaf_id in nodes}
    parents = {e["target"]: e["source"] for e in graph["edges"] if e["kind"] == "contains"}
    handlers = defaultdict(set)
    for e in graph["edges"]:
        if e["kind"] == "handles" and e["target"] in nodes:
            handlers[e["source"]].add(e["target"])

    modules, owners = {}, {}
    for key, node in nodes.items():
        ancestor = key
        if key not in groups and len(handlers.get(key, ())) == 1:
            ancestor = next(iter(handlers[key]))
        while ancestor not in groups and ancestor in parents:
            ancestor = parents[ancestor]
        group = groups.get(ancestor)
        if group:
            module_id = "collab:business:" + digest(group["label"])[:24]
            label, basis = group["label"], "业务分组"
        else:
            module_id = "collab:file:" + node["file"]
            label, basis = node["file"], "源码模块"
        owners[key] = module_id
        if module_id not in modules:
            modules[module_id] = {"id": module_id, "label": label, "basis": basis, "nodeIds": [], "refs": []}
            if group:
                modules[module_id]["groupId"] = group["id"]
        modules[module_id]["nodeIds"].append(key)
        modules[module_id]["refs"].append({"file": node["file"], "line": node["line"], "end": node["line"]})

    pairs = defaultdict(list)
    by_file = defaultdict(list)
    for edge in graph["edges"]:
        source, target = owners.get(edge["source"]), owners.get(edge["target"])
        if (edge["kind"] not in {"calls", "handles"} or edge["file"] not in production
                or not source or not target or source == target):
            continue
        site = {**ref(edge), "column": edge["column"], "edgeId": edge["id"], "kind": edge["kind"],
                "caller": edge["source"], "callee": edge["target"], "conditions": []}
        pairs[source, target].append(site)
        if edge["kind"] == "calls":
            by_file[site["file"]].append(site)
    for file, sites in by_file.items():
        positions = {(s["line"], s["column"]) for s in sites}
        # The index only emits calls from successfully parsed Python sources.
        tree = ast.parse(graph["files"][file], filename=file)
        conditions = _CallConditions(file, tree, positions).found
        for site in sites:
            site["conditions"] = conditions.get((site["line"], site["column"]), [])
    relations = [{"id": "collab:relation:" + digest([a, b])[:24], "source": a, "target": b,
                  "sites": sorted(sites, key=lambda s: (s["file"], s["line"], s["column"], s["edgeId"]))}
                 for (a, b), sites in sorted(pairs.items())]
    connected = {module for pair in pairs for module in pair}
    visible = [module for module in modules.values()
               if module["id"] in connected or module.get("groupId") or not pairs]
    for module in visible:
        module["nodeIds"].sort()
        refs = {(r["file"], r["line"]): r for r in module["refs"]}
        module["refs"] = [refs[key] for key in sorted(refs)]
    return {"modules": sorted(visible, key=lambda m: (m["label"], m["id"])), "relations": relations}
