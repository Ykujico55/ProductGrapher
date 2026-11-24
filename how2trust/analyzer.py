"""Deterministic source index. All references describe static declarations."""

import ast
import io
import os
import tokenize
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .outputs import export_kind

EXCLUDED = {".git", ".hg", ".svn", "__pycache__", ".venv", "venv", "node_modules",
            ".tox", ".nox", "site-packages", "dist", "build", ".mypy_cache",
            ".pytest_cache", ".ruff_cache", ".how2trust"}
DEFINITIONS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
SCOPED_EXPRESSIONS = (ast.Lambda, ast.GeneratorExp, ast.ListComp, ast.SetComp, ast.DictComp)
ROUTERS = {"fastapi.FastAPI", "fastapi.APIRouter", "flask.Flask", "flask.Blueprint"}
HTTP_METHODS = {"get", "put", "patch", "delete", "post", "head", "options", "trace"}
CONTEXT_SUFFIXES = {".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".css", ".html",
                    ".md", ".rst", ".txt", ".json", ".toml", ".yaml", ".yml", ".ini"}


def context_source(disk, relative):
    """Read supporting text for citations without indexing generated exports as inputs."""
    suffix = disk.suffix.lower()
    if suffix not in CONTEXT_SUFFIXES or any(part.startswith(".") for part in relative.parts):
        return None
    try:
        source = disk.read_bytes().decode("utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return None
    if "\x00" in source or export_kind(source):
        return None
    return source


def evidence(path, expr):
    return {"file": path, "line": getattr(expr, "lineno", 1),
            "end": getattr(expr, "end_lineno", None) or getattr(expr, "lineno", 1)}


def keyword(call, name):
    return next((kw.value for kw in call.keywords if kw.arg == name), None)


def literal(expr):
    return expr.value if isinstance(expr, ast.Constant) and isinstance(expr.value, str) else None


def strings(expr):
    if isinstance(expr, (ast.List, ast.Tuple)):
        values = [literal(e) for e in expr.elts]
        if all(isinstance(v, str) for v in values):
            return values
    return None


def method_call(expr, name):
    return isinstance(expr, ast.Call) and isinstance(expr.func, ast.Attribute) and expr.func.attr == name


def unpack_assignment(target, value):
    """Pair names with RHS expressions only when the unpacking shape is literal."""
    if not isinstance(target, (ast.Tuple, ast.List)):
        yield target, value
        return
    targets = target.elts
    values = [None] * len(targets)
    if isinstance(value, (ast.Tuple, ast.List)) and not any(isinstance(e, ast.Starred) for e in value.elts):
        star = next((i for i, e in enumerate(targets) if isinstance(e, ast.Starred)), None)
        if star is None and len(targets) == len(value.elts):
            values = value.elts
        elif star is not None and len(value.elts) >= len(targets) - 1:
            suffix = len(targets) - star - 1
            values = [*value.elts[:star], None, *value.elts[len(value.elts) - suffix:]]
    for element, assigned in zip(targets, values):
        if isinstance(element, ast.Starred):
            element = element.value  # A starred capture creates a mutable list.
        yield from unpack_assignment(element, assigned)


@dataclass(eq=False)
class Binding:
    kind: str
    value: object
    expr: ast.AST
    suite_end: tuple[int, int] | None = None
    available_after: tuple[int, int] | None = None
    definition: ast.AST | None = None


@dataclass
class Scope:
    id: str
    path: str
    mod: str
    qual: str
    tree: ast.AST
    parent: object = None
    bindings: dict = field(default_factory=lambda: defaultdict(list))
    children: list = field(default_factory=list)
    exprs: list = field(default_factory=list)
    attributes: dict = field(default_factory=lambda: defaultdict(list))
    branches: list = field(default_factory=list)
    star_imports: list = field(default_factory=list)

    def binding_at(self, name, use=None, reaching_definition=None):
        bindings = self.bindings.get(name, ())
        if len(bindings) == 1:
            binding = bindings[0]
        elif isinstance(use, ast.Name) and reaching_definition and any(b.kind == "caught" for b in bindings):
            # Different handlers commonly reuse 'as e'. Only the handler
            # reaching this particular read can supply its declared type.
            origin = reaching_definition(self.path, use)
            binding = next((b for b in bindings if b.kind == "caught" and b.definition is origin), None)
            if binding is None:
                return None
        elif (bindings and isinstance(use, ast.Name) and reaching_definition is not None
              and all((b.kind != "unknown" or isinstance(b.expr, ast.arg))
                      and b.suite_end is None and b.available_after is None for b in bindings)):
            # A deferred read in another scope has no known execution point.
            # An unknown/conditional write also prevents claiming a definite call.
            origin = reaching_definition(self.path, use)
            binding = next((b for b in bindings if (b.definition or b.expr) is origin), None)
            if binding is None:
                return None
        else:
            return None
        start = (binding.expr.lineno, binding.expr.col_offset)
        end = (use.lineno, use.col_offset) if use is not None else None
        if binding.available_after is not None and end is not None and end < binding.available_after:
            return None
        if binding.suite_end is not None:
            assigned = (binding.expr.end_lineno, binding.expr.end_col_offset)
            if end is None or not assigned <= end < binding.suite_end:
                return None
        if end is not None and start > end:
            return None
        if any(start < star and (end is None or star <= end) for star in self.star_imports):
            return None
        return binding

    def assigned_call(self, value):
        if isinstance(value, ast.Name):
            binding = self.binding_at(value.id, value)
            value = binding.value if binding and binding.kind == "value" else None
        return value if isinstance(value, ast.Call) else None


class Index:
    def __init__(self, root):
        self.root = root
        self.files = {}
        self.encodings = {}
        self.unresolved_calls = {}
        self.modules = defaultdict(list)
        self.scopes = {}
        self.nodes = {}
        self.edges = []
        self.diagnostics = []
        self.writes = []
        self.replaced = set()
        self.import_guards = []
        self.reaching_names = {}
        self.subclasses = None

    def add_edge(self, source, target, kind, path, expr, **metadata):
        self.edges.append(dict(source=source, target=target, kind=kind, **evidence(path, expr),
                               column=getattr(expr, "col_offset", 0) + 1, **metadata))

    def diagnose(self, path, expr, message, caller=None, **details):
        diag = dict(**evidence(path, expr), message=message, **details)
        if caller:
            diag["caller"] = caller
        self.diagnostics.append(diag)

    def module_name(self, path):
        parts = path[:-3].split("/")
        if len(parts) > 1 and parts[0] == "src" and not (self.root / "src/__init__.py").is_file():
            parts.pop(0)
        if parts[-1] == "__init__":
            parts.pop()
        if (self.root / "__init__.py").is_file():
            # A stable synthetic package keeps relative imports valid when the
            # selected root is itself a package, irrespective of checkout name.
            parts.insert(0, "<root>")
        return ".".join(parts)

    def load(self):
        for base, dirs, filenames in os.walk(self.root):
            dirs[:] = sorted(d for d in dirs if d not in EXCLUDED
                             and not self.link(Path(base, d)) and not Path(base, d, "pyvenv.cfg").is_file())
            for name in sorted(filenames):
                disk = Path(base, name)
                if self.link(disk) or not disk.is_file():
                    continue
                relative = disk.relative_to(self.root)
                path = relative.as_posix()
                if not name.endswith(".py"):
                    if (source := context_source(disk, relative)) is not None:
                        self.files[path] = source
                    continue
                try:
                    raw = disk.read_bytes()
                    encoding, _ = tokenize.detect_encoding(io.BytesIO(raw).readline)
                    source = raw.decode(encoding)
                    self.files[path] = source
                    tree = ast.parse(source, filename=path)
                    self.encodings[path] = encoding
                except (SyntaxError, UnicodeError, OSError, LookupError, ValueError) as exc:
                    line = getattr(exc, "lineno", None) or 1
                    self.diagnostics.append(dict(file=path, line=line, end=line,
                                                 message=f"源码读取或解析失败 ({type(exc).__name__})"))
                    continue
                mod = self.module_name(path)
                scope = Scope("module:" + path, path, mod, "", tree)
                self.nodes[scope.id] = dict(id=scope.id, kind="module", label=path,
                                            qualname=mod, file=path, line=1,
                                            end=max(1, len(source.splitlines())))
                self.modules[mod].append(scope)
                self.scopes[scope.id] = scope
                self.collect(scope)

    @staticmethod
    def link(path):
        return path.is_symlink() or getattr(path, "is_junction", lambda: False)()

    def imported_from(self, scope, expr):
        if not expr.level:
            return expr.module or ""
        package = scope.mod.split(".")
        if Path(scope.path).name != "__init__.py":
            package.pop()
        if expr.level > len(package):
            return "?"
        return ".".join(package[:len(package) - expr.level + 1] + ([expr.module] if expr.module else []))

    def collect(self, scope):
        def bind(name, kind, value, expr, suite_end=None, definition=None):
            scope.bindings[name].append(Binding(kind, value, expr, suite_end, definition=definition))

        def walk(expr, unconditional, suite_end=None):
            if isinstance(expr, DEFINITIONS):
                name = expr.name
                qual = scope.qual + "." + name if scope.qual else name
                kind = ("class" if isinstance(expr, ast.ClassDef) else
                        "method" if isinstance(scope.tree, ast.ClassDef) else "function")
                ident = f"{kind}:{scope.path}:{qual}"
                if ident in self.nodes:
                    ident += f"@{expr.lineno}"
                child = Scope(ident, scope.path, scope.mod, qual, expr, scope)
                self.scopes[ident] = child
                scope.children.append(child)
                self.nodes[ident] = dict(id=ident, label=name, qualname=qual, kind=kind,
                                         **evidence(scope.path, expr))
                self.add_edge(scope.id, ident, "contains", scope.path, expr)
                bind(name, "node" if unconditional else "unknown", ident, expr)
                # Defaults, decorators and annotations are outside the function body.
                return
            if isinstance(expr, SCOPED_EXPRESSIONS):
                # Only the first iterable of a comprehension is evaluated in
                # the enclosing scope. Its target can shadow names in the body.
                outer_iter = None if isinstance(expr, ast.Lambda) else expr.generators[0].iter
                pending = [expr]
                while pending:
                    node = pending.pop()
                    if node is outer_iter:
                        walk(node, unconditional)
                        continue
                    if isinstance(node, ast.NamedExpr) and isinstance(node.target, ast.Name):
                        bind(node.target.id, "unknown", None, node)
                    if isinstance(node, ast.Call):
                        self.diagnose(scope.path, node, "嵌套表达式的调用目标未解析", scope.id,
                                      category="unresolved", expression=ast.unparse(node.func),
                                      column=node.col_offset + 1)
                    pending.extend(ast.iter_child_nodes(node))
                return
            if isinstance(expr, (ast.Import, ast.ImportFrom)):
                for alias in expr.names:
                    if alias.name == "*":
                        scope.star_imports.append((expr.lineno, expr.col_offset))
                        continue
                    if isinstance(expr, ast.ImportFrom):
                        module = self.imported_from(scope, expr)
                        name = alias.asname or alias.name
                        target = (module, alias.name)
                        kind = "from"
                    else:
                        name = alias.asname or alias.name.split(".")[0]
                        target = alias.name if alias.asname else name
                        kind = "import"
                    bind(name, kind if unconditional else "unknown", target, expr, definition=alias)
                return
            if isinstance(expr, (ast.Assign, ast.AnnAssign)):
                targets = expr.targets if isinstance(expr, ast.Assign) else [expr.target]
                known = (unconditional or suite_end is not None) and expr.value is not None
                for target in targets:
                    for part, value in unpack_assignment(target, expr.value):
                        if isinstance(part, ast.Name):
                            bind(part.id, "value" if known else "unknown", value, expr, suite_end, part)
                        elif not register_field(part, value, unconditional):
                            walk(part, unconditional)
                if expr.value:
                    walk(expr.value, unconditional)
                scope.exprs.append(expr)
                return
            if isinstance(expr, (ast.Global, ast.Nonlocal)):
                for name in expr.names:
                    bind(name, "unknown", None, expr)
                    owner = scope.parent
                    while owner is not None:
                        if isinstance(expr, ast.Global) and owner.parent is None or (
                                isinstance(expr, ast.Nonlocal) and name in owner.bindings):
                            owner.bindings[name].append(Binding("unknown", None, expr))
                            break
                        owner = owner.parent
            if isinstance(expr, ast.ExceptHandler) and expr.name:
                bind(expr.name, "unknown", None, expr)
            if isinstance(expr, (ast.MatchAs, ast.MatchStar)) and expr.name:
                bind(expr.name, "unknown", None, expr)
            if isinstance(expr, ast.MatchMapping) and expr.rest:
                bind(expr.rest, "unknown", None, expr)
            if isinstance(expr, ast.Name) and isinstance(expr.ctx, (ast.Store, ast.Del)):
                bind(expr.id, "unknown", None, expr)
            if isinstance(expr, ast.Attribute) and isinstance(expr.ctx, (ast.Store, ast.Del)):
                if not register_field(expr, None, False):
                    self.writes.append((scope, expr))
            if isinstance(expr, ast.Call):
                scope.exprs.append(expr)
            if isinstance(expr, ast.If):
                scope.branches.append(expr)
            if isinstance(expr, ast.Try):
                if (unconditional and len(expr.body) == 1
                        and isinstance(expr.body[0], (ast.Import, ast.ImportFrom))
                        and expr.handlers and not expr.finalbody):
                    self.import_guards.append((scope, expr))
                # Later statements in this suite can run only after a direct
                # assignment succeeds. The try's own handlers/finally cannot assume it.
                last = expr.body[-1]
                for stmt in expr.body:
                    walk(stmt, False, (last.end_lineno, last.end_col_offset))
                for handler in expr.handlers:
                    if handler.name and isinstance(handler.type, (ast.Name, ast.Attribute)):
                        bind(handler.name, "caught", handler.type, handler.type,
                             (handler.end_lineno, handler.end_col_offset), handler)
                        for child in ast.iter_child_nodes(handler):
                            walk(child, False)
                    else:
                        walk(handler, False)
                for stmt in (*expr.orelse, *expr.finalbody):
                    walk(stmt, False)
                return
            direct = unconditional and not isinstance(expr, ast.stmt)
            for child in ast.iter_child_nodes(expr):
                walk(child, direct)

        def register_field(target, value, unconditional):
            if not (scope.parent and isinstance(scope.parent.tree, ast.ClassDef)
                    and isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name)
                    and target.value.id == "self"):
                return False
            init = unconditional and scope.tree.name == "__init__" and isinstance(value, ast.Call)
            scope.parent.attributes[target.attr].append((scope, value if init else None))
            return True

        if isinstance(scope.tree, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = scope.tree.args
            positional = [*args.posonlyargs, *args.args]
            first = positional[0] if positional else None
            for arg in [*positional, *args.kwonlyargs, args.vararg, args.kwarg]:
                if arg is None:
                    continue
                receiver = (arg is first and isinstance(scope.parent.tree, ast.ClassDef)
                            and arg.arg in {"self", "cls"} and not scope.tree.decorator_list)
                bind(arg.arg, "receiver" if receiver else "unknown", scope.parent.id, arg)
        for stmt in scope.tree.body:
            walk(stmt, True)
        for child in scope.children:
            self.collect(child)

    def module_ref(self, name):
        if name in self.modules:
            modules = self.modules[name]
            return ("node", modules[0].id) if len(modules) == 1 else None
        return ("path", name)

    def member(self, ref, name, seen):
        if not ref:
            return None
        kind, value = ref
        if kind == "caught":
            return self.caught_member(value, name, seen)
        if kind == "path":
            return self.module_ref(value + "." + name)
        if kind not in {"node", "instance"}:
            return None
        scope = self.scopes[value]
        if isinstance(scope.tree, ast.Module):
            if (value, name) in self.replaced:
                return None
            if name in scope.bindings:
                return self.bound(scope, name, None, seen)
            if Path(scope.path).name == "__init__.py":
                submodule = scope.mod + "." + name
                if submodule in self.modules:
                    return self.module_ref(submodule)
        if isinstance(scope.tree, ast.ClassDef):
            lineage = list(self.class_chain(scope, seen))
            if kind == "instance" and any(owner and name in owner.attributes for owner in lineage[1:]):
                return None
            for owner in lineage:
                if owner is None or (owner.id, name) in self.replaced:
                    return None
                if kind == "instance" and name in owner.attributes:
                    fields = owner.attributes[name]
                    if len(fields) != 1 or fields[0][1] is None:
                        return None
                    key = (owner.id, name)
                    if key in seen:
                        return None
                    method, call = fields[0]
                    return self.resolve(method, call, seen | {key})
                if name in owner.bindings:
                    return self.bound(owner, name, None, seen)
        return None

    def caught_member(self, class_id, name, seen):
        target = self.member(("instance", class_id), name, seen)
        if not target:
            return None
        # An except clause also catches subclasses. Keep a single target only
        # if known subclasses inherit that same implementation.
        if self.subclasses is None:
            self.subclasses = defaultdict(list)
            for scope in self.scopes.values():
                if isinstance(scope.tree, ast.ClassDef):
                    for base in scope.tree.bases:
                        if isinstance(base, (ast.Name, ast.Attribute)):
                            parent = self.resolve(scope.parent, base)
                            if parent and parent[0] == "node":
                                self.subclasses[parent[1]].append(scope.id)
        visited = {class_id}
        pending = list(self.subclasses.get(class_id, ()))
        while pending:
            child = pending.pop()
            if child in visited:
                continue
            visited.add(child)
            if self.member(("instance", child), name, seen) != target:
                return None
            pending.extend(self.subclasses.get(child, ()))
        return target

    def class_chain(self, scope, seen):
        """Walk a statically bound single-inheritance chain; None marks an unknown remainder."""
        while (scope.id, None) not in seen:
            yield scope
            seen = seen | {(scope.id, None)}
            bases = scope.tree.bases
            if not bases:
                return
            if len(bases) != 1 or not isinstance(bases[0], (ast.Name, ast.Attribute)):
                break
            base = bases[0]
            if isinstance(base, ast.Name) and base.id == "object":
                parent = scope.parent
                while parent and not (parent.star_imports or "object" in parent.bindings):
                    parent = parent.parent
                if parent is None:
                    return
            ref = self.resolve(scope.parent, base, seen)
            if not self.class_target(ref):
                break
            scope = self.scopes[ref[1]]
        yield None

    def constructed_instance(self, ref, seen):
        if not self.class_target(ref):
            return None
        lineage = list(self.class_chain(self.scopes[ref[1]], seen))
        if any(owner is None or owner.tree.keywords for owner in lineage):
            return None
        for owner in lineage:
            if (owner.id, "__new__") in self.replaced:
                return None
            if "__new__" in owner.bindings:
                binding = owner.binding_at("__new__")
                if not binding or binding.kind != "node" or (owner.id, "__new__") in seen:
                    return None
                method = self.scopes[binding.value]
                if not isinstance(method.tree, ast.FunctionDef) or method.tree.decorator_list:
                    return None
                body = method.tree.body[1:] if ast.get_docstring(method.tree) is not None else method.tree.body
                if len(body) != 1 or not isinstance(body[0], ast.Return) or not isinstance(body[0].value, ast.Call):
                    return None
                return self.resolve(method, body[0].value, seen | {(owner.id, "__new__")})
        return ("instance", ref[1])

    def reaching_definition(self, path, use):
        if path not in self.reaching_names:
            from .name_flow import unique_name_origins
            self.reaching_names[path] = unique_name_origins(self.scopes["module:" + path].tree)
        return self.reaching_names[path].get(use)

    def bound(self, scope, name, use, seen):
        binding = scope.binding_at(name, use, self.reaching_definition)
        if binding is None or binding in seen or (scope.id, name) in seen:
            return None
        # An alias captures its RHS at assignment time. A previous binding of
        # the same name (for example during a swap) is a distinct definition.
        seen = seen | {binding}
        kind, value = binding.kind, binding.value
        if kind == "node":
            return ("node", value)
        if kind == "receiver":
            return ("instance", value)
        if kind == "caught":
            ref = self.resolve(scope, value, seen)
            if ref and ref[0] == "node":
                cls = self.scopes[ref[1]]
                if isinstance(cls.tree, ast.ClassDef) and all(
                        self.identity_decorator(cls.parent, dec, seen) for dec in cls.tree.decorator_list):
                    return ("caught", cls.id)
        if kind == "import":
            return self.module_ref(value)
        if kind == "from":
            if value[0] == scope.mod and value[0] + "." + value[1] in self.modules:
                return self.module_ref(value[0] + "." + value[1])
            return self.member(self.module_ref(value[0]), value[1], seen)
        if kind == "value":
            if isinstance(value, (ast.Call, ast.Name, ast.Attribute)):
                return self.resolve(scope, value, seen)
        return None

    def identity_decorator(self, scope, decorator, seen):
        ref = self.resolve(scope, decorator, seen)
        if not self.callable_node(ref):
            return False
        tree = self.scopes[ref[1]].tree
        if not isinstance(tree, ast.FunctionDef) or tree.decorator_list:
            return False
        positional = tree.args.posonlyargs + tree.args.args
        body = tree.body[1:] if ast.get_docstring(tree) is not None else tree.body
        return (len(positional) == 1 and len(body) == 1 and isinstance(body[0], ast.Return)
                and isinstance(body[0].value, ast.Name) and body[0].value.id == positional[0].arg)

    def resolve(self, scope, expr, seen=frozenset()):
        if isinstance(expr, ast.Attribute):
            return self.member(self.resolve(scope, expr.value, seen), expr.attr, seen)
        if isinstance(expr, ast.Call):
            target = self.resolve(scope, expr.func, seen)
            return self.constructed_instance(target, seen)
        if isinstance(expr, ast.Name):
            current = scope
            while current:
                if current.star_imports or expr.id in current.bindings:
                    return self.bound(current, expr.id, expr if current is scope else None, seen)
                current = current.parent
                while current and isinstance(current.tree, ast.ClassDef):
                    current = current.parent
        return None

    def route_entries(self, scope):
        owner = scope.parent
        if not isinstance(owner.tree, ast.Module):
            return
        for dec in scope.tree.decorator_list:
            func = dec.func if isinstance(dec, ast.Call) else dec
            ref = self.resolve(owner, func)
            if ref in {("path", "click.command"), ("path", "click.group")}:
                naming = ((dec.args[0] if dec.args else keyword(dec, "name"))
                          if isinstance(dec, ast.Call) else None)
                name = "Click · " + scope.tree.name if naming is None else literal(naming)
                if name:
                    self.entry(scope, dec, name, "CLI", basis="Click 命令声明")
                continue
            if not isinstance(dec, ast.Call) or not isinstance(func, ast.Attribute) or (
                    not isinstance(func.value, ast.Name)):
                continue
            b = owner.binding_at(func.value.id, dec)
            if b is None:
                continue
            if b.kind == "node" and func.attr == "command":
                parent = self.scopes[b.value]
                for group_dec in getattr(parent.tree, "decorator_list", ()):
                    group_func = group_dec.func if isinstance(group_dec, ast.Call) else group_dec
                    if self.resolve(owner, group_func) != ("path", "click.group"):
                        continue
                    group_name = None
                    if isinstance(group_dec, ast.Call):
                        group_name = literal(group_dec.args[0] if group_dec.args else keyword(group_dec, "name"))
                    command = dec.args[0] if dec.args else keyword(dec, "name")
                    name = literal(command) if command is not None else "Click · " + scope.tree.name
                    if name:
                        self.entry(scope, dec, name, "CLI", group=group_name or parent.qual,
                                   site=group_dec, basis="Click 分组命令声明")
                    break
                continue
            if b.kind != "value" or not isinstance(b.value, ast.Call) or b.expr.lineno >= dec.lineno:
                continue
            factory = self.resolve(owner, b.value.func)
            if not factory or factory[0] != "path" or factory[1] not in ROUTERS:
                continue
            method = func.attr
            path = literal(dec.args[0] if dec.args else keyword(dec, "path") or keyword(dec, "rule"))
            if method not in HTTP_METHODS | {"route", "api_route"} or path is None:
                continue
            flask = factory[1].startswith("flask.")
            if flask and method in {"api_route", "trace", "head", "options"} or not flask and method == "route":
                continue
            raw_methods = keyword(dec, "methods")
            methods = ([method.upper()] if method in HTTP_METHODS else
                       ["GET"] if raw_methods is None else strings(raw_methods))
            if not methods:
                self.diagnose(owner.path, dec, "HTTP 方法无法从字面量确认", scope.id)
                continue
            group, site = None, None
            tags = strings(keyword(dec, "tags")) or strings(keyword(b.value, "tags"))
            if tags and not flask:
                group = tags[0]
                site = dec if strings(keyword(dec, "tags")) else b.expr
            if factory[1] == "flask.Blueprint":
                group = literal(b.value.args[0] if b.value.args else keyword(b.value, "name"))
                site = b.expr
            prefix_expr = (keyword(b.value, "prefix") if factory[1] == "fastapi.APIRouter" else
                           keyword(b.value, "url_prefix") if factory[1] == "flask.Blueprint" else None)
            prefix = literal(prefix_expr) if prefix_expr else ""
            if prefix is None:
                self.diagnose(owner.path, prefix_expr, "动态路由前缀未解析，图中显示装饰器声明的局部路径", scope.id)
            for verb in sorted(set(methods)):
                node = self.entry(scope, dec, f"{verb.upper()} {(prefix or '') + path}", "HTTP",
                                  group=group, site=site, basis="路由声明（挂载路径以运行配置为准）")
                if prefix_expr:
                    node["evidence"] = [evidence(owner.path, prefix_expr)]

    def entry(self, handler, expr, label, protocol, group=None, site=None, basis="", path=None, linked=True):
        path = path or handler.path
        ident = f"entry:{path}:{handler.id}:{expr.lineno}:{label}"
        node = dict(id=ident, label=label, kind="entry", protocol=protocol, basis=basis,
                    qualname=handler.qual, **evidence(path, expr))
        if group and site:
            node.update(group=group, group_evidence=evidence(handler.path, site))
        self.nodes[ident] = node
        if linked:
            self.add_edge(ident, handler.id, "handles", path, expr)
        return node

    def argparse_entries(self, scope):
        defaults = {}
        for registration in scope.exprs:
            if not method_call(registration, "add_parser"):
                continue
            subparsers = scope.assigned_call(registration.func.value)
            if not method_call(subparsers, "add_subparsers"):
                continue
            factory = scope.assigned_call(subparsers.func.value)
            if factory is None or self.resolve(scope, factory.func) != ("path", "argparse.ArgumentParser"):
                continue
            name = literal(registration.args[0] if registration.args else keyword(registration, "name"))
            if not name:
                continue
            entry = self.entry(scope, registration, name, "CLI", group=literal(keyword(factory, "prog")),
                               site=factory, basis="argparse 子命令声明", linked=False)
            entry["evidence"] = [evidence(scope.path, subparsers)]
            if subparsers not in defaults:
                defaults[subparsers] = self.argparse_defaults(scope, factory, subparsers)
            self.argparse_dispatch(scope, entry, factory, subparsers, registration, defaults[subparsers].get(name, ()))

    @staticmethod
    def argparse_defaults(scope, factory, subparsers):
        """Recognize a final else in a closed, required local command parser."""
        required = keyword(subparsers, "required")
        dest = literal(keyword(subparsers, "dest"))
        if not dest or not isinstance(required, ast.Constant) or required.value is not True:
            return {}
        # Only accept ordinary local setup from construction to parse_args. Passing
        # the parser to a helper or registering dynamic names leaves the set open.
        parser = action = None
        child_parsers = set()
        registrations = {}
        for stmt in scope.tree.body:
            value = getattr(stmt, "value", None)
            target = None
            if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
                target = stmt.targets[0].id
            if value is factory and target:
                parser = target
                continue
            if parser is None:
                continue
            if not (isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute)
                    and isinstance(value.func.value, ast.Name)):
                return {}
            receiver, method = value.func.value.id, value.func.attr
            if value is subparsers and target and receiver == parser:
                action = target
            elif method == "add_parser" and receiver == action:
                name = literal(value.args[0] if value.args else keyword(value, "name"))
                if not name or any(kw.arg in (None, "aliases", "parents") for kw in value.keywords):
                    return {}
                registrations[name] = value
                if target:
                    child_parsers.add(target)
            elif method == "add_argument" and (receiver == parser or receiver in child_parsers):
                # A subparser argument must not overwrite the parent's command field.
                declared_dest = keyword(value, "dest")
                if (not value.args or any(literal(a) is None or literal(a).lstrip("-").replace("-", "_") == dest
                                          for a in value.args)
                        or any(kw.arg is None for kw in value.keywords)
                        or declared_dest is not None and literal(declared_dest) in (None, dest)):
                    return {}
            elif method == "parse_args" and receiver == parser and target and registrations:
                namespace = target
                parsed = value
                break
            else:
                return {}
        else:
            return {}

        # A helper receiving the entire namespace can change its command field.
        receivers = {e.value for e in ast.walk(scope.tree) if isinstance(e, ast.Attribute)}
        first_escape = min(((e.lineno, e.col_offset) for e in ast.walk(scope.tree)
                            if isinstance(e, ast.Name) and isinstance(e.ctx, ast.Load)
                            and e.id == namespace and e not in receivers), default=None)

        def tested_command(test):
            if not isinstance(test, ast.Compare) or len(test.ops) != 1 or not isinstance(test.ops[0], ast.Eq):
                return None
            left, right = test.left, test.comparators[0]
            if literal(left) is not None:
                left, right = right, left
            if (isinstance(left, ast.Attribute) and left.attr == dest and isinstance(left.value, ast.Name)
                    and left.value.id == namespace and scope.assigned_call(left.value) is parsed):
                return literal(right)
            return None

        result = defaultdict(list)
        suites = [scope.tree.body]
        while suites:
            for root in suites.pop():
                if isinstance(root, ast.Try):
                    suites.append(root.body)
                if not isinstance(root, ast.If):
                    continue
                if first_escape is not None and first_escape < (root.lineno, root.col_offset):
                    continue
                remaining = registrations.keys()
                tests = []
                branch = root
                while (name := tested_command(branch.test)) in remaining:
                    remaining = remaining - {name}
                    tests.append(branch.test)
                    tail = branch.orelse
                    if len(tail) == 1 and isinstance(tail[0], ast.If):
                        branch = tail[0]
                        continue
                    if tail and len(remaining) == 1:
                        result[next(iter(remaining))].append((tail, tests))
                    break
        return result

    def argparse_dispatch(self, scope, entry, factory, subparsers, registration, defaults):
        def parsed_attribute(expr):
            if isinstance(expr, ast.Attribute):
                parse = scope.assigned_call(expr.value)
                if method_call(parse, "parse_args") and scope.assigned_call(parse.func.value) is factory:
                    return expr.attr
            return None

        calls = [e for e in scope.exprs if isinstance(e, ast.Call)]
        edge_count = len(self.edges)
        setters = [e for e in calls if method_call(e, "set_defaults")
                   and scope.assigned_call(e.func.value) is registration]
        direct = [stmt.value for stmt in scope.tree.body if isinstance(stmt, (ast.Expr, ast.Return))]
        if len(setters) == 1 and setters[0] in direct:
            for kw in setters[0].keywords:
                if not kw.arg or any(owner is scope and parsed_attribute(attr) == kw.arg for owner, attr in self.writes):
                    continue
                uses = [call for call in direct if isinstance(call, ast.Call) and
                        parsed_attribute(call.func) == kw.arg and
                        setters[0].lineno < scope.assigned_call(call.func.value).lineno]
                target = self.resolve(scope, kw.value)
                if uses and self.callable_node(target):
                    self.add_edge(entry["id"], target[1], "handles", scope.path, kw,
                                  basis="set_defaults 回调与已解析命令空间中的同名调用")
                    entry["evidence"].extend(evidence(scope.path, call) for call in uses)

        dest = literal(keyword(subparsers, "dest"))
        found_branch = False
        if any(owner is scope and parsed_attribute(attr) == dest for owner, attr in self.writes):
            dest = None
        for branch in scope.branches if dest else ():
            test = branch.test
            terms = test.values if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.And) else [test]
            for term in terms:
                if not isinstance(term, ast.Compare) or len(term.ops) != 1 or not isinstance(term.ops[0], ast.Eq):
                    continue
                left, right = term.left, term.comparators[0]
                if literal(left) == entry["label"]:
                    left, right = right, left
                if parsed_attribute(left) == dest and literal(right) == entry["label"]:
                    entry["evidence"].append(evidence(scope.path, test))
                    self.branch_calls(scope, entry["id"], branch.body, calls)
                    found_branch = True
                    break
        if dest and defaults:
            entry["basis"] += "；必选命令中仅此项进入末尾 else"
            for statements, tests in defaults:
                entry["evidence"].extend(evidence(scope.path, test) for test in tests)
                self.branch_calls(scope, entry["id"], statements, calls, basis="必选命令排除已比较项后对应的 else 调用")
            found_branch = True
        if found_branch:
            entry["basis"] += "；调用图仅包含明确命令分支中可定位的调用与已绑定回调。"
        if len(self.edges) == edge_count:
            detail = "条件分支中未解析出仓库内调用" if found_branch else "具体处理分支或回调未解析"
            entry["basis"] += " · " + detail
            self.diagnose(scope.path, registration, detail, entry["id"])

    def branch_calls(self, scope, entry_id, statements, calls, basis="命令条件分支内的静态调用点"):
        within = {n for statement in statements for n in ast.walk(statement)}
        imports = {node for node in statements if isinstance(node, ast.ImportFrom)}
        for call in calls:
            if call not in within:
                continue
            target = self.resolve(scope, call.func)
            if not target and isinstance(call.func, ast.Name):
                # A direct import in this same branch dominates the call even though
                # the import cannot be assumed elsewhere in the enclosing function.
                binding = scope.binding_at(call.func.id, call.func)
                if binding and binding.expr in imports:
                    mod, name = binding.value
                    target = self.member(self.module_ref(mod), name, set())
            if self.call_basis(target):
                self.add_edge(entry_id, target[1], "calls", scope.path, call,
                              basis=basis, expression=ast.unparse(call.func))

    def main_guards(self, scope):
        for stmt in scope.tree.body:
            if not isinstance(stmt, ast.If) or not isinstance(stmt.test, ast.Compare):
                continue
            test = stmt.test
            if len(test.ops) != 1 or not isinstance(test.ops[0], ast.Eq):
                continue
            left, right = test.left, test.comparators[0]
            if literal(left) == "__main__":
                left, right = right, left
            if not isinstance(left, ast.Name) or left.id != "__name__" or literal(right) != "__main__":
                continue
            for action in stmt.body:
                if isinstance(action, ast.Expr):
                    call = action.value
                elif (isinstance(action, ast.Raise) and isinstance(action.exc, ast.Call)
                      and isinstance(action.exc.func, ast.Name) and action.exc.func.id == "SystemExit"
                      and len(action.exc.args) == 1):
                    call = action.exc.args[0]
                else:
                    continue
                if not isinstance(call, ast.Call):
                    continue
                target = self.resolve(scope, call.func)
                if self.launch_handler(target):
                    node = self.entry(self.scopes[target[1]], call, f"{scope.path} · __main__", "CLI",
                                      basis="脚本入口", path=scope.path)
                    node["evidence"] = [evidence(scope.path, stmt.test)]

    def launch_handler(self, ref):
        if not ref or ref[0] != "node" or self.nodes[ref[1]]["kind"] != "function":
            return False
        if self.callable_node(ref):
            return True
        target = self.scopes[ref[1]]
        # A Click command object is a launch target even though invoking it is
        # not a direct call to the original Python function.
        decorators = [self.resolve(target.parent, d.func if isinstance(d, ast.Call) else d)
                      for d in target.tree.decorator_list]
        return (bool(decorators) and decorators[0] in {("path", "click.command"), ("path", "click.group")}
                and all(d in {("path", "click.option"), ("path", "click.argument"),
                              ("path", "click.version_option"), ("path", "click.pass_context"),
                              ("path", "click.pass_obj")} for d in decorators[1:]))

    def console_scripts(self, scope):
        for stmt in scope.tree.body:
            if not (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call)
                    and self.resolve(scope, stmt.value.func) == ("path", "setuptools.setup")):
                continue
            entries = keyword(stmt.value, "entry_points")
            if not isinstance(entries, ast.Dict) or not all(isinstance(k, ast.Constant) for k in entries.keys):
                continue
            # A later dictionary item with the same key wins at runtime.
            scripts = next((v for k, v in reversed(list(zip(entries.keys, entries.values)))
                            if literal(k) == "console_scripts"), None)
            if not isinstance(scripts, (ast.Tuple, ast.List)):
                continue
            for item in scripts.elts:
                name, _, spec = (literal(item) or "").partition("=")
                module, _, symbol = spec.strip().partition(":")
                if not name.strip() or not symbol.isidentifier():
                    continue
                target = self.member(self.module_ref(module), symbol, set())
                if self.launch_handler(target):
                    self.entry(self.scopes[target[1]], item, name.strip(), "CLI", path=scope.path,
                               basis="setuptools 控制台脚本注册")

    def resolve_import_guards(self):
        # With a single import, every path reaching the following statement has
        # the imported name when each failure handler raises or calls sys.exit.
        def stops(scope, stmt):
            return isinstance(stmt, ast.Raise) or (
                isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call)
                and self.resolve(scope, stmt.value.func) == ("path", "sys.exit"))

        for scope, guard in self.import_guards:
            if not all(stops(scope, h.body[-1]) for h in guard.handlers):
                continue
            stmt = guard.body[0]
            for alias in stmt.names:
                name = alias.asname or (alias.name if isinstance(stmt, ast.ImportFrom) else alias.name.split(".")[0])
                for b in scope.bindings.get(name, ()):
                    if b.expr is stmt:
                        b.kind = "from" if isinstance(stmt, ast.ImportFrom) else "import"
                        b.available_after = (guard.end_lineno, guard.end_col_offset)

    def callable_node(self, ref):
        if not ref or ref[0] != "node":
            return False
        target = self.scopes[ref[1]]
        if not isinstance(target.tree, DEFINITIONS):
            return False
        for dec in target.tree.decorator_list:
            if (self.nodes[target.id]["kind"] != "method" or not isinstance(dec, ast.Name)
                    or dec.id not in {"classmethod", "staticmethod"}):
                return False
            outer = target.parent
            while outer:
                if outer.star_imports or dec.id in outer.bindings:
                    return False
                outer = outer.parent
        return True

    def class_target(self, ref):
        return self.callable_node(ref) and self.nodes[ref[1]]["kind"] == "class"

    def call_basis(self, ref):
        if self.callable_node(ref):
            return "按声明类型解析方法" if self.nodes[ref[1]]["kind"] == "method" else "静态符号绑定"
        if not ref or ref[0] != "node":
            return None
        scope = self.scopes[ref[1]]
        if isinstance(scope.tree, ast.FunctionDef) and len(scope.tree.decorator_list) == 1:
            decorator = scope.tree.decorator_list[0]
            if isinstance(decorator, (ast.Name, ast.Attribute)) and (
                self.resolve(scope.parent, decorator) == ("path", "contextlib.contextmanager")
            ):
                return "contextlib.contextmanager 工厂调用"
        return None

    def connect(self):
        self.resolve_import_guards()
        for scope, attr in self.writes:
            ref = self.resolve(scope, attr.value)
            if ref and ref[0] in {"instance", "node", "caught"}:
                self.replaced.add((ref[1], attr.attr))
        for scope in self.scopes.values():
            tree = scope.tree
            self.argparse_entries(scope)
            if isinstance(tree, ast.ClassDef):
                for base in tree.bases:
                    ref = self.resolve(scope.parent, base)
                    if self.class_target(ref):
                        self.add_edge(scope.id, ref[1], "inherits", scope.path, base)
            if isinstance(tree, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.route_entries(scope)
            if isinstance(tree, ast.Module):
                self.main_guards(scope)
                if Path(scope.path).name == "setup.py":
                    self.console_scripts(scope)
            for expr in scope.exprs:
                if isinstance(expr, ast.Call):
                    target = self.resolve(scope, expr.func)
                    basis = self.call_basis(target)
                    if basis:
                        self.add_edge(scope.id, target[1], "calls", scope.path, expr,
                                      basis=basis, expression=ast.unparse(expr.func))
                    else:
                        self.diagnose(scope.path, expr, "调用目标未解析 · " + ast.unparse(expr.func), scope.id)
                        site = (scope.path, expr.lineno, expr.col_offset, expr.end_lineno, expr.end_col_offset)
                        self.unresolved_calls[site] = (scope, expr, self.diagnostics[-1])
                elif isinstance(tree, (ast.FunctionDef, ast.AsyncFunctionDef)) and isinstance(
                        scope.parent.tree, ast.ClassDef) and isinstance(expr.value, ast.Call):
                    target = self.resolve(scope, expr.value)
                    if not target or target[0] != "instance":
                        continue
                    for attr in expr.targets if isinstance(expr, ast.Assign) else [expr.target]:
                        if isinstance(attr, ast.Attribute) and self.resolve(scope, attr.value) == (
                                "instance", scope.parent.id):
                            self.add_edge(scope.parent.id, target[1], "associates", scope.path, expr,
                                          basis="实例属性赋值", expression=ast.unparse(attr))


def analyze(root: Path) -> dict:
    """Index Python declarations and call sites without importing target code."""
    root = Path(root).resolve()
    if not root.is_dir():
        raise ValueError(f"仓库目录不存在: {root}")
    index = Index(root)
    index.load()
    index.connect()
    pycg = None
    if index.unresolved_calls:
        from .pycg_backend import supplement_calls
        pycg = supplement_calls(index)
    from .call_diagnostics import classify_call_diagnostics
    classify_call_diagnostics(index, pycg)
    return {"callDiagnosticVersion": 1, "files": dict(sorted(index.files.items())),
            "nodes": sorted(index.nodes.values(), key=lambda n: n["id"]),
            "edges": sorted(index.edges, key=lambda e: (
                e["source"], e["target"], e["kind"], e["file"], e["line"], e["column"])),
            "diagnostics": sorted(index.diagnostics, key=lambda d: (d["file"], d["line"], d["message"]))}
