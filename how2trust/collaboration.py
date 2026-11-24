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
        expression = ast.unparse(test)
        text = f"在第 {test.lineno} 行判断为{'真' if truth else '假'}：{expression}"
        if isinstance(test, ast.Subscript):
            text += "（下标取值成功后，判断取出值的真假）"
        return {**self.fact(text, test), "kind": "predicate", "truth": truth, "expression": expression}

    def value_choices(self, values):
        choices = []
        for value in values:
            if isinstance(value, (ast.List, ast.Tuple)):
                choices.extend(self.value_choices(value.elts))
            elif isinstance(value, ast.IfExp):
                test, yes, no = (self.fact(ast.unparse(expr), expr)
                                 for expr in (value.test, value.body, value.orelse))
                choices.append({**self.fact(f"{test['text']} 为真时取 {yes['text']}，为假时取 {no['text']}", value),
                                "test": test, "whenTrue": yes, "whenFalse": no})
        return choices

    def call_arguments(self, call):
        # A choice nested in another call (or a deferred lambda) belongs to
        # that expression. Only describe values supplied directly to this call.
        return self.value_choices([*call.args, *(kw.value for kw in call.keywords)])

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
            elif isinstance(statement, ast.Assert):
                test = self.fact(ast.unparse(statement.test), statement.test)
                text = (f"启用断言时，第 {statement.lineno} 行的 {test['text']} 必须为真，"
                        "若为假则抛出 AssertionError（-O 下跳过此检查）")
                active.append({**self.fact(text, statement), "kind": "assertion",
                               "predicate": {**test, "truth": True}})
            elif isinstance(statement, (ast.With, ast.AsyncWith)):
                # A return inside a context manager still leaves the enclosing
                # function. An exception may be suppressed by __exit__, so never
                # propagate a raise/exit guard across this boundary.
                for child in statement.body:
                    if isinstance(child, ast.If):
                        active.extend(self.forward_guards(child, in_with=True))

    def forward_guards(self, statement, in_with=False):
        for body, truth in ((statement.body, True), (statement.orelse, False)):
            keyword = self.exit_from(body)
            if keyword and (not in_with or keyword in {"return", "continue", "break"}):
                when = "前方 with 块内" if in_with else "前方"
                predicate = self.fact(ast.unparse(statement.test), statement.test)
                exit_statement = self.fact(ast.unparse(body[-1]), body[-1])
                text = (f"{when}分支判断为{'真' if truth else '假'}且执行至末尾时走向 {keyword}："
                        f"{predicate['text']}")
                if exit_statement["text"] != keyword:
                    text += f"（{exit_statement['text']}）"
                yield {**self.fact(text, statement.test, exit_statement["end"]), "kind": "priorExit",
                       "predicate": {**predicate, "truth": truth}, "exit": exit_statement, "inWith": in_with}

    def visit(self, node, active):
        if node in self.watch:
            self.contexts[node] = list(active)
        if isinstance(node, ast.Call):
            key = (node.lineno, node.col_offset + 1)
            if key in self.positions:
                self.found[key] = {"conditions": list(active), "valueChoices": self.call_arguments(node)}
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
    links = [e for e in graph["edges"] if e["kind"] in {"calls", "handles"} and e["file"] in production
             and e["source"] in nodes and e["target"] in nodes]
    handlers = defaultdict(set)
    bindings = defaultdict(list)
    prefixes = defaultdict(list)
    for edge in links:
        if nodes[edge["source"]]["kind"] == "entry":
            prefixes[edge["target"]].append((edge["source"], [edge["id"]]))
            if edge["kind"] == "handles":
                handlers[edge["source"]].add(edge["target"])
                bindings[edge["target"]].append(edge)
    # Registration is a binding, not a call: Click/HTTP handlers get the
    # same two evidenced call steps as a directly dispatched CLI command.
    for edge in links:
        if edge["kind"] == "calls" and edge["source"] != edge["target"]:
            for binding in bindings.get(edge["source"], ()):
                prefixes[edge["target"]].append((binding["source"], [binding["id"], edge["id"]]))

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
    internal_sites = []
    by_file = defaultdict(list)
    for edge in links:
        source, target = owners[edge["source"]], owners[edge["target"]]
        if nodes[edge["source"]]["kind"] == "entry":
            paths = [{"entry": edge["source"], "edges": [edge["id"]]}]
        else:
            paths = [{"entry": entry, "edges": [*prefix, edge["id"]]}
                     for entry, prefix in prefixes.get(edge["source"], ())]
        site = {**ref(edge), "column": edge["column"], "edgeId": edge["id"], "kind": edge["kind"],
                "caller": edge["source"], "callee": edge["target"], "conditions": [], "valueChoices": [],
                "entryPaths": paths}
        if source == target:
            internal_sites.append(site)
        else:
            pairs[source, target].append(site)
        if edge["kind"] == "calls":
            by_file[site["file"]].append(site)
    for file, sites in by_file.items():
        positions = {(s["line"], s["column"]) for s in sites}
        # The index only emits calls from successfully parsed Python sources.
        tree = ast.parse(graph["files"][file], filename=file)
        contexts = _CallConditions(file, tree, positions).found
        for site in sites:
            site.update(contexts.get((site["line"], site["column"]), {}))

    def site_order(site):
        return site["file"], site["line"], site["column"], site["edgeId"]

    relations = [{"id": "collab:relation:" + digest([a, b])[:24], "source": a, "target": b,
                  "sites": sorted(sites, key=site_order)}
                 for (a, b), sites in sorted(pairs.items())]
    connected = {module for pair in pairs for module in pair}
    visible = [module for module in modules.values()
               if module["id"] in connected or module.get("groupId") or not pairs]
    for module in visible:
        module["nodeIds"].sort()
        refs = {(r["file"], r["line"]): r for r in module["refs"]}
        module["refs"] = [refs[key] for key in sorted(refs)]
    return {"modules": sorted(visible, key=lambda m: (m["label"], m["id"])), "relations": relations,
            "internalSites": sorted(internal_sites, key=site_order)}
