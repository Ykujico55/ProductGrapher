"""Attribute changed lines to declared scope and identify new defensive behavior."""

import ast
from collections import Counter, defaultdict
from dataclasses import dataclass
from fnmatch import fnmatchcase
from functools import lru_cache
from itertools import chain

from .analyzer import Index, Scope, keyword, literal
from .project import digest


def _path_matches(pattern, path):
    """Match whole POSIX paths. Only a complete ** segment crosses directories."""
    pattern_parts, path_parts = pattern.split("/"), path.split("/")

    @lru_cache(None)
    def visit(p, s):
        if p == len(pattern_parts):
            return s == len(path_parts)
        if pattern_parts[p] == "**":
            return visit(p + 1, s) or (s < len(path_parts) and visit(p, s + 1))
        return s < len(path_parts) and fnmatchcase(path_parts[s], pattern_parts[p]) and visit(p + 1, s + 1)

    return visit(0, 0)


def _matches(selector, file, symbol):
    path_pattern, sep, symbol_pattern = selector.partition("::")
    if not _path_matches(path_pattern, file):
        return False
    if not sep:
        return True
    if not symbol:
        return False
    # Declaring a class/function in scope includes declarations nested inside it.
    parts = symbol.split(".")
    return any(fnmatchcase(".".join(parts[:i]), symbol_pattern) for i in range(1, len(parts) + 1))


class Source:
    def __init__(self, file, source, side, parse_python, diagnostics):
        self.lines = source.splitlines() if source else []
        self.tree = None
        self.failed = False
        self.symbols = {""} if source is not None else set()
        self.owners = [""] * (len(self.lines) + 1)
        self.index = None
        if parse_python and source is not None:
            try:
                self.tree = ast.parse(source.removeprefix("\ufeff"), filename=file)
            except (SyntaxError, ValueError, RecursionError) as exc:
                self.failed = True
                version = "基线" if side == "before" else "交付"
                diagnostics.append(f"{file}:{getattr(exc, 'lineno', None) or 1}（{version}）"
                                   f"无法解析 Python，保留文件差异。{exc}")
            else:
                self._index(file)

    def _index(self, file):
        # Collect lexical scopes from this revision's AST, without loading a checkout.
        self.index = Index(root=None)
        module = Scope("module:" + file, file, "<review-file>", "", self.tree)
        self.index.scopes[module.id] = module
        self.index.collect(module)
        for scope in self.index.scopes.values():
            if scope is module:
                continue
            node = scope.tree
            self.symbols.add(scope.qual)
            start = min([node.lineno, *(d.lineno for d in node.decorator_list)])
            self.owners[start:node.end_lineno + 1] = [scope.qual] * (node.end_lineno - start + 1)

    def api_name(self, scope, expr):
        if isinstance(expr, ast.Name) and expr.id == "open":
            owner = scope
            while owner and not (owner.star_imports or "open" in owner.bindings):
                owner = owner.parent
                while owner and isinstance(owner.tree, ast.ClassDef):
                    owner = owner.parent
            if owner is None:
                return "builtins.open"
        resolved = self.index.resolve(scope, expr)
        return resolved[1] if resolved and resolved[0] == "path" else None

    def changed_units(self, positions):
        result = defaultdict(list)
        for n in sorted(set(positions)):
            if 0 < n <= len(self.lines):
                result[self.owners[n]].append(n)
        return result

    def snippet(self, start, end):
        return "\n".join(self.lines[start - 1:min(end, start + 11)])


def _ranges(lines):
    spans = []
    for line in lines:
        if spans and spans[-1]["end"] + 1 == line:
            spans[-1]["end"] = line
        else:
            spans.append({"line": line, "end": line})
    return spans


def _mapping(file, symbol, requirements, forbidden):
    ids = []
    rules = []
    for req in requirements:
        matches = [s for s in req.get("scope", []) if _matches(s, file, symbol)]
        if matches:
            ids.append(req["id"])
            rules.extend(matches)
    denied = [s for s in forbidden if _matches(s, file, symbol)]
    precision = "symbol" if any("::" in s for s in rules) else "file" if rules else "none"
    status = "forbidden" if denied else "mapped" if ids else "unmapped"
    return {"requirementIds": ids, "matchedSelectors": sorted(set(rules)),
            "forbiddenSelectors": denied, "mapping": precision, "status": status}


def _scope_units(change, old, new, requirements, forbidden):
    file = change["file"]
    old_units = old.changed_units(change["removedLines"])
    new_units = new.changed_units(change["addedLines"])
    units = set(old_units) | set(new_units) or {""}
    for symbol in sorted(units):
        before, after = _ranges(old_units.get(symbol, [])), _ranges(new_units.get(symbol, []))
        which = change["status"]
        if symbol and not old.failed and not new.failed:
            which = "modified" if symbol in old.symbols and symbol in new.symbols else (
                "added" if symbol in new.symbols else "deleted")
        side = "after" if after or (not before and which != "deleted") else "before"
        evidence = after if side == "after" else before
        start = evidence[0]["line"] if evidence else 1
        end = evidence[0]["end"] if evidence else 1
        mapping = _mapping(file, symbol, requirements, forbidden)
        chunks = [(old, old_units.get(symbol, [])), (new, new_units.get(symbol, []))]
        only_whitespace = bool(before or after) and all(
            not source.lines[n - 1].strip() for source, lines in chunks for n in lines)
        # Spacing around newly added definitions remains visible in the file diff.
        if only_whitespace and (old.tree or new.tree) and mapping["status"] != "forbidden":
            continue
        detail = {
            "mapped": ("命中任务清单中的符号选择器。" if mapping["mapping"] == "symbol" else
                       "命中文件或目录选择器。需要核对这个文件内的具体行为是否服务于关联需求。"),
            "unmapped": "这项改动没有匹配任何需求的范围选择器，请核对它服务的需求。",
            "forbidden": "这项改动命中了任务明确禁止变更的范围。",
        }[mapping["status"]]
        if only_whitespace:
            detail += " 这些差异行只包含空白。"
        if which == "modified" and (old.failed or new.failed):
            detail += " 源码解析不全，请核对原始差异。"
        if not evidence:
            detail += " 此文件没有可定位的文本变更行。"
        yield {"id": "scope:" + digest([file, symbol])[:24], "file": file, "symbol": symbol,
               "title": symbol or file, "detail": detail, "change": which,
               "side": side, "line": start, "end": end, "beforeRanges": before,
               "afterRanges": after, "snippet": (new if side == "after" else old).snippet(start, end),
               **mapping}


def _arg(call, position, name):
    if len(call.args) > position:
        return call.args[position]
    return keyword(call, name)


def _write_mode(mode):
    flags = set(mode or "")
    return (len(mode or "") == len(flags) and flags <= set("rwaxbt+")
            and len(flags & set("rwax")) == 1 and not {"b", "t"} <= flags
            and bool(flags & set("wax+")))


def _file_writes(source):
    """Yield potential file writes with the API name, destination and open mode."""
    if source.index is None:
        return
    sites = ((scope, call) for scope in source.index.scopes.values() for call in scope.exprs
             if isinstance(call, ast.Call))
    for scope, call in sites:
        evidence = [call]
        name = source.api_name(scope, call.func)
        if name in {"builtins.open", "io.open"}:
            path = _arg(call, 0, "file")
            mode_arg = _arg(call, 1, "mode")
        elif isinstance(call.func, ast.Attribute) and call.func.attr in {"open", "write_text", "write_bytes"}:
            constructor = scope.assigned_call(call.func.value)
            if constructor is None or source.api_name(scope, constructor.func) != "pathlib.Path":
                continue
            if isinstance(call.func.value, ast.Name) and constructor.lineno != call.lineno:
                evidence.append(constructor)
            name = "pathlib.Path." + call.func.attr
            path = constructor.args[0] if len(constructor.args) == 1 else constructor
            mode_arg = _arg(call, 0, "mode")
        else:
            continue
        if name.endswith(".open"):
            mode = literal(mode_arg)
            if not _write_mode(mode):
                continue
        else:
            mode = "w"
        if path is not None:
            yield (scope.qual, name, mode, ast.unparse(path)), evidence, literal(path)


def _scope_effects(change, old, new, requirements, forbidden):
    if old.failed or new.tree is None:
        return
    baseline = Counter(key for key, _, _ in _file_writes(old))
    added = set(change["addedLines"])
    # Consume unchanged occurrences first so a duplicate added earlier in the
    # same function points at the new call site.
    def touched(site):
        _, evidence, _ = site
        return any(n in added for node in evidence for n in range(node.lineno, node.end_lineno + 1))

    findings = []
    for key, evidence, target in sorted(_file_writes(new), key=touched):
        if baseline[key]:
            baseline[key] -= 1
            continue
        owner, name, mode, expression = key
        file = change["file"]
        mapping = _mapping(file, owner, requirements, forbidden)
        if mapping["status"] != "mapped":
            continue
        if target is not None and any(req["id"] in mapping["requirementIds"] and target in req.get("writes", [])
                                      for req in requirements):
            continue
        call = evidence[0]
        ranges = [{"line": node.lineno, "end": node.end_lineno} for node in evidence]
        findings.append({
            **mapping, **ranges[0], "status": "unmapped", "side": "after", "file": file, "symbol": owner,
            "id": "effect:" + digest([file, key, call.lineno, call.col_offset])[:24],
            "kind": "file-write", "change": "added", "target": target, "title": "新增文件写入调用待核对",
            "detail": (f"{name} 使用模式 {mode!r}，可能写入 {expression}。所在位置已关联需求，"
                       "请核对这项写入是否服务于关联需求。"),
            "beforeRanges": [], "afterRanges": ranges, "snippet": new.snippet(call.lineno, call.end_lineno),
        })
    yield from sorted(findings, key=lambda finding: finding["line"])


@dataclass(frozen=True)
class Signal:
    rule: str
    signature: str
    line: int
    end: int
    observation: str


DEFENSES = {
    "swallowed-exception": (
        "宽泛捕获后继续执行",
        "被捕获的错误会跳过正常处理路径，调用者可能无法观察失败。",
        "预期允许忽略哪种具体异常？调用者如何区分成功和失败？"),
    "fallback": (
        "异常处理分支返回结果",
        "调用方需要区分正常结果和处理异常后返回的值。",
        "哪条要求约定这个返回值？上游能否识别这次失败？"),
    "retry": (
        "异常后可能重新执行循环体",
        "额外执行会增加等待时间，已产生的副作用也可能重复。",
        "需要恢复哪种临时失败？重试次数和重复执行的后果有何依据？"),
    "duplicate-guard": (
        "相邻分支重复检查相同条件",
        "这条路径多求值一次相同表达式，修改校验规则时需要核对两处。",
        "两次判断之间什么状态会改变？第二次求值会产生不同结果吗？"),
}


def _broad_catch(exc):
    if exc is None:
        return True
    if isinstance(exc, ast.Tuple):
        return any(_broad_catch(e) for e in exc.elts)
    return ast.unparse(exc) in {"Exception", "BaseException", "builtins.Exception", "builtins.BaseException"}


def _handler_signal(handler):
    last = handler.body[-1]
    caught = ast.unparse(handler.type) if handler.type else "裸 except"
    if isinstance(last, ast.Return):
        return Signal("fallback", f"{caught}|{ast.dump(last)}", handler.lineno, last.end_lineno,
                      f"异常处理分支 {caught} 以 {ast.unparse(last)} 结束。")
    if _broad_catch(handler.type) and isinstance(last, (ast.Pass, ast.Continue)):
        return Signal("swallowed-exception", f"{caught}|{type(last).__name__}", handler.lineno,
                      last.end_lineno, f"处理器 {caught} 以 {ast.unparse(last)} 结束。")
    if _broad_catch(handler.type) and all(isinstance(stmt, ast.Expr) for stmt in handler.body):
        return Signal("swallowed-exception", f"{caught}|expressions", handler.lineno, last.end_lineno,
                      f"处理器 {caught} 中只有表达式语句，末尾没有显式重新抛出异常。")
    return None


def _returned_assignments(tried, returned):
    """Match an exception handler's final assignment to the next returned name."""
    if tried.finalbody or not isinstance(returned, ast.Return) or not isinstance(returned.value, ast.Name):
        return
    name = returned.value.id
    for handler in tried.handlers:
        last = handler.body[-1]
        if isinstance(last, ast.Assign) and len(last.targets) == 1:
            target = last.targets[0]
        elif isinstance(last, ast.AnnAssign) and last.value is not None:
            target = last.target
        else:
            continue
        # Python clears the name bound by "except ... as name" on handler exit.
        if not isinstance(target, ast.Name) or target.id != name or handler.name == name:
            continue
        caught = ast.unparse(handler.type) if handler.type else "裸 except"
        # An equivalent direct return in the baseline represents the same fallback.
        signature = f"{caught}|{ast.dump(ast.Return(value=last.value))}"
        yield Signal("fallback", signature, handler.lineno, returned.end_lineno,
                     f"异常处理分支 {caught} 将 {ast.unparse(last.value)} 赋给 {name}，"
                     f"第 {returned.lineno} 行返回 {name}。")


def _retry_signal(loop):
    if isinstance(loop, ast.For):
        if not (isinstance(loop.iter, ast.Call) and isinstance(loop.iter.func, ast.Name)
                and loop.iter.func.id == "range"):
            return None
        bounds = loop.iter.args
        if 1 <= len(bounds) <= 3 and all(isinstance(arg, ast.Constant) and isinstance(arg.value, int)
                                         for arg in bounds):
            try:
                trips = range(*(arg.value for arg in bounds))
            except ValueError:
                return None
            if len(trips[:2]) < 2:
                return None
        header = ast.unparse(loop.iter)
    else:
        if isinstance(loop.test, ast.Constant) and not loop.test.value:
            return None
        header = ast.unparse(loop.test)
    for stmt in loop.body:
        if not isinstance(stmt, ast.Try):
            continue
        # An exception resumes the loop while success explicitly exits it.
        exits_on_success = any(isinstance(s, (ast.Break, ast.Return)) for s in stmt.body + stmt.orelse)
        handlers = []
        for h in stmt.handlers:
            last = h.body[-1]
            if isinstance(last, ast.Continue) or (exits_on_success and not isinstance(
                    last, (ast.Raise, ast.Return, ast.Break))):
                handlers.append(ast.dump(h))
        if handlers:
            sig = repr((header, handlers))
            return Signal("retry", sig, loop.lineno, loop.end_lineno,
                          f"循环条件或迭代范围为 {header}，循环内有可继续执行的异常处理分支。")
    return None


def _signals(source):
    if source.tree is None:
        return
    for node in ast.walk(source.tree):
        signal = None
        if isinstance(node, ast.ExceptHandler):
            signal = _handler_signal(node)
        elif isinstance(node, (ast.For, ast.While)):
            signal = _retry_signal(node)
        if signal:
            yield source.owners[signal.line], signal
        for _, block in ast.iter_fields(node):
            if not isinstance(block, list):
                continue
            for left, right in zip(block, block[1:]):
                if isinstance(left, ast.Try):
                    for fallback in _returned_assignments(left, right):
                        yield source.owners[fallback.line], fallback
                if not isinstance(left, ast.If) or left.orelse or not isinstance(right, ast.If):
                    continue
                if not isinstance(left.body[-1], (ast.Return, ast.Raise, ast.Continue, ast.Break)):
                    continue
                if any(isinstance(x, (ast.Call, ast.NamedExpr, ast.Await)) for x in ast.walk(left.test)):
                    continue
                condition = ast.dump(left.test)
                if condition == ast.dump(right.test):
                    yield source.owners[right.lineno], Signal(
                        "duplicate-guard", condition, right.lineno, right.test.end_lineno,
                        f"第 {left.lineno} 行和第 {right.lineno} 行连续判断 {ast.unparse(right.test)}。")


def _new_defenses(change, old, new, requirements, forbidden):
    if old.failed or new.tree is None:
        return
    counts = Counter((symbol, s.rule, s.signature) for symbol, s in _signals(old))
    additions = set(change["addedLines"])
    # When an identical guard is added, consume the existing unchanged one first.
    def touched(item):
        _, signal = item
        return any(i in additions for i in range(signal.line, signal.end + 1))

    for symbol, signal in sorted(_signals(new), key=touched):
        key = (symbol, signal.rule, signal.signature)
        if counts[key]:
            counts[key] -= 1
            continue
        title, cost, question = DEFENSES[signal.rule]
        file = change["file"]
        yield {"id": "defense:" + digest([file, symbol, signal.rule, signal.line, signal.signature])[:24],
               "file": file, "symbol": symbol, "line": signal.line, "end": signal.end,
               "side": "after", "status": "review", "rule": signal.rule, "title": title,
               "detail": signal.observation, "cost": cost, "question": question,
               "snippet": new.snippet(signal.line, signal.end),
               "requirementIds": _mapping(file, symbol, requirements, forbidden)["requirementIds"]}


def assess(changes, requirements, forbidden):
    scope, defenses, diagnostics = [], [], []
    links = {req["id"]: [] for req in requirements}
    for change in sorted(changes, key=lambda c: c["file"]):
        file = change["file"]
        parse = file.endswith(".py") and not change["binary"]
        old_regular = (change.get("beforeMode") or "100644").startswith("100")
        new_regular = (change.get("afterMode") or "100644").startswith("100")
        old = Source(file, change["before"], "before", parse and old_regular, diagnostics)
        new = Source(file, change["after"], "after", parse and new_regular, diagnostics)
        units = _scope_units(change, old, new, requirements, forbidden)
        effects = _scope_effects(change, old, new, requirements, forbidden)
        for unit in chain(units, effects):
            scope.append(unit)
            for req_id in unit["requirementIds"]:
                links[req_id].append({k: unit[k] for k in (
                    "id", "file", "line", "end", "side", "symbol", "change", "status", "mapping")})
        defenses.extend(_new_defenses(change, old, new, requirements, forbidden))
    defenses.sort(key=lambda s: (s["file"], s["line"], s["rule"]))
    return {"requirements": links, "scope": scope, "defenses": defenses, "diagnostics": diagnostics}
