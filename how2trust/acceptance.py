"""Read the user's acceptance contract and verify its observable expectations."""

from __future__ import annotations

import codecs
import json
import math
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from .runtime_trace import trace_check


def _strings(value, label):
    if not isinstance(value, list) or any(not isinstance(x, str) or not x.strip() for x in value):
        raise ValueError(f"{label} 需要非空字符串组成的列表")
    return value


def _fields(obj, allowed, location):
    if not isinstance(obj, dict):
        raise ValueError(f"{location} 需要 JSON 对象")
    unknown = set(obj) - set(allowed)
    if unknown:
        raise ValueError(f"{location} 中存在未知字段：{', '.join(sorted(unknown))}")


def _criteria(source):
    """Split prose paragraphs and list items, keeping an item's indented detail with it."""
    bullet = re.compile(r"^\s*(?:[-*+]\s+(?:\[[ xX]\]\s*)?|\d+[.)、]\s*)(\S.*)$")
    block = []
    item_indent = None
    separated = False
    for line in source.splitlines():
        if not line.strip():
            separated = True
            continue
        match = bullet.match(line)
        indent = len(line) - len(line.lstrip())
        continues_item = item_indent is not None and indent >= item_indent
        if block and (match or (separated and not continues_item)):
            yield "\n".join(block)
            block = []
            item_indent = None
        if match:
            item_indent = match.start(1)
        block.append(match[1] if match else line.strip())
        separated = False
    if block:
        yield "\n".join(block)


def scenario_name(scenario):
    return scenario if isinstance(scenario, str) else scenario["name"]


def load_contract(path):
    path = Path(path)
    source = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() == ".json":
        obj = json.loads(source)
    else:
        obj = {"task": source.strip(), "requirements": [
            {"id": f"R{i}", "text": text} for i, text in enumerate(_criteria(source), 1)
        ]}
    _fields(obj, ("task", "requirements", "forbidden", "forbiddenDefenses"), "验收清单")
    if not isinstance(obj.get("task"), str) or not obj["task"].strip():
        raise ValueError("验收清单需要原始任务文本 task")
    reqs = obj.get("requirements")
    if not isinstance(reqs, list) or not reqs:
        raise ValueError("至少需要一条 requirement")
    ids = set()
    for req in reqs:
        _fields(req, ("id", "text", "scope", "checks", "writes", "scenarios"), "requirement")
        for key in ("id", "text"):
            if not isinstance(req.get(key), str) or not req[key].strip():
                raise ValueError(f"requirement 缺少 {key}")
        if req["id"] in ids:
            raise ValueError(f"重复的需求 ID：{req['id']}")
        ids.add(req["id"])
        _strings(req.setdefault("scope", []), "scope")
        if "scenarios" in req:
            if not isinstance(req["scenarios"], list):
                raise ValueError("scenarios 需要场景数组")
            for scenario in req["scenarios"]:
                if isinstance(scenario, str):
                    _strings([scenario], "scenarios")
                else:
                    _fields(scenario, ("name", "given", "when", "then"), "scenarios")
                    _strings([scenario.get(k) for k in ("name", "given", "when", "then")], "scenarios")
            names = [scenario_name(s) for s in req["scenarios"]]
            if len(set(names)) != len(names):
                raise ValueError("scenarios 中的场景名称应唯一")
        if "writes" in req:
            _strings(req["writes"], "writes")
        if not isinstance(req.setdefault("checks", []), list):
            raise ValueError("checks 需要数组")
        for check in req["checks"]:
            _fields(check, ("name", "argv", "expect", "timeout", "source", "verifies", "trace"), f"{req['id']} 的检查")
            if not isinstance(check.get("name"), str) or not check["name"].strip():
                raise ValueError("检查需要名称 name")
            if not _strings(check.get("argv"), "argv"):
                raise ValueError("检查缺少可执行命令")
            expect = check.get("expect")
            _fields(expect, ("exit", "stdout", "contains", "json"), "expect")
            if not expect or type(expect.get("exit", 0)) is not int:
                raise ValueError("expect 需要明确的期望值，exit 应为整数")
            if "stdout" in expect and not isinstance(expect["stdout"], str):
                raise ValueError("stdout 应为字符串")
            if "contains" in expect and (not isinstance(expect["contains"], str) or not expect["contains"]):
                raise ValueError("contains 应为非空字符串")
            if "json" in expect:
                fields = expect["json"]
                if not isinstance(fields, dict) or not fields:
                    raise ValueError("expect.json 需要 JSON Pointer 到预期值的非空映射")
                if any((p and not p.startswith("/")) or re.search(r"~(?![01])", p) for p in fields):
                    raise ValueError("expect.json 字段位置需要使用 JSON Pointer，例如 /order/state")
            if "verifies" in check and check["verifies"] not in ("exit", "tests"):
                raise ValueError("verifies 可设置为 exit 或 tests")
            if "source" in check and (not isinstance(check["source"], str)
                                      or not re.fullmatch(r".+\.py::[^:]+", check["source"])):
                raise ValueError("source 需要 仓库相对路径.py::符号限定名")
            if "trace" in check and (type(check["trace"]) is not bool or check["trace"] and not check.get("source")):
                raise ValueError("trace 需要布尔值，启用时请用 source 选定仓库中的函数或方法")
            timeout = check.get("timeout", 60)
            if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
                raise ValueError("timeout 需要正数秒")
    _strings(obj.setdefault("forbidden", []), "forbidden")
    forbidden = _strings(obj.setdefault("forbiddenDefenses", []), "forbiddenDefenses")
    invalid = set(forbidden) - {"swallowed-exception", "fallback", "retry", "duplicate-guard"}
    if invalid:
        raise ValueError("未知的防御规则：" + ", ".join(sorted(invalid)))
    return obj


def pending(check):
    record = {"name": check["name"], "argv": check["argv"], "expected": {"exit": 0, **check["expect"]},
              "status": "unverified", "actualExit": None, "output": "", "detail": "尚未执行"}
    if "verifies" in check:
        record["verifies"] = check["verifies"]
    if check.get("trace"):
        record["traceEvidence"] = {"status": "not_run", "detail": "尚未执行该场景。"}
    if "json" in check["expect"]:
        record["observations"] = [{"path": path, "expected": value, "status": "unverified"}
                                  for path, value in check["expect"]["json"].items()]
    return record


def _same_json(left, right):
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(_same_json(left[k], right[k]) for k in left)
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(_same_json(a, b) for a, b in zip(left, right))
    return isinstance(left, bool) == isinstance(right, bool) and left == right


def _observe_json(stream, observations):
    stream.seek(0)
    try:
        data = json.load(stream)
    except (ValueError, UnicodeError):
        return "约定输出为 JSON，实际标准输出无法解析"
    for item in observations:
        value = data
        for token in item["path"].split("/")[1:]:
            key = token.replace("~1", "/").replace("~0", "~")
            if isinstance(value, dict) and key in value:
                value = value[key]
            elif isinstance(value, list) and re.fullmatch(r"0|[1-9][0-9]*", key) and len(key) <= len(str(len(value))) and int(key) < len(value):
                value = value[int(key)]
            else:
                item.update(status="failed", missing=True)
                break
        else:
            item.update(actual=value, status="passed" if _same_json(value, item["expected"]) else "failed")
    if any(item["status"] == "failed" for item in observations):
        return "业务字段与约定不符，见逐项预期和实测值"
    return None


def _read_log(stream):
    stream.seek(0)
    raw = stream.read(128_001)
    prefix = raw[:128_000].decode("utf-8", "replace").replace("\r\n", "\n")
    if len(raw) <= 128_000:
        return prefix, False, prefix
    # A verbose runner can emit its entire test summary after the displayed prefix.
    stream.seek(-4096, os.SEEK_END)
    tail = stream.read().decode("utf-8", "replace").replace("\r\n", "\n")
    return prefix, True, tail


def _stdout_chunks(stream):
    """Decode UTF-8 and CRLF even when a block ends in the middle of either."""
    stream.seek(0)
    blocks = iter(lambda: stream.read(65_536), b"")
    carry = ""
    for chunk in codecs.iterdecode(blocks, "utf-8", errors="replace"):
        chunk = carry + chunk
        carry = "\r" if chunk.endswith("\r") else ""
        text = chunk.removesuffix(carry).replace("\r\n", "\n")
        if text:
            yield text
    if carry:
        yield carry


def _stdout_mismatch(stream, expected):
    if "stdout" in expected:
        target = expected["stdout"].replace("\r\n", "\n").removesuffix("\n")
        offset = 0
        last = ""
        for chunk in _stdout_chunks(stream):
            # Keep the final character until EOF so exactly one line ending is optional.
            text = last + chunk
            prefix, last = text[:-1], text[-1:]
            if not target.startswith(prefix, offset):
                return "标准输出与期望不符"
            offset += len(prefix)
        if target[offset:] != last.removesuffix("\n"):
            return "标准输出与期望不符"
    if "contains" in expected:
        needle = expected["contains"].replace("\r\n", "\n")
        overlap = ""
        for chunk in _stdout_chunks(stream):
            text = overlap + chunk
            if needle in text:
                break
            overlap = text[-(len(needle) - 1):] if len(needle) > 1 else ""
        else:
            return "标准输出未包含约定文本"
    return None


def _is_test_runner(argv):
    if Path(argv[0]).stem == "pytest":
        return True
    # Interpreter flags such as -B are allowed before the module entry point.
    for index, arg in enumerate(argv[1:], 1):
        if arg == "-m":
            return argv[index + 1:index + 2] in (["unittest"], ["pytest"])
        if arg == "-c" or arg.endswith(".py"):
            break
    return False


def _named_test(argv):
    """Recognize explicitly selected unittest modules/cases and pytest node IDs."""
    if not _is_test_runner(argv) or "discover" in argv:
        return False
    if "unittest" in argv:
        return any(re.search(r"(?:^|[./\\])test_[\w.]+$", arg) for arg in argv[argv.index("unittest") + 1:])
    return any("::test_" in arg for arg in argv[1:])


def run_check(check, checkout, spec_dir):
    result = pending(check)
    argv = [arg.replace("{python}", sys.executable).replace("{repo}", str(checkout))
            .replace("{spec_dir}", str(spec_dir)) for arg in check["argv"]]
    if check["argv"][0] == "{python}" and len(argv) > 1 and argv[1].lower().endswith(".py"):
        # A probe next to the live checkout must import the particular revision under test.
        # Append its own directory for helper imports after the snapshot's PYTHONPATH.
        script = ("import os,runpy,sys; sys.argv=sys.argv[1:]; "
                  "sys.path.append(os.path.dirname(os.path.abspath(sys.argv[0]))); "
                  "runpy.run_path(sys.argv[0],run_name='__main__')")
        argv = [argv[0], "-c", script, *argv[1:]]
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1",
               PYTHONPATH=os.pathsep.join([str(checkout), str(checkout / "src")]))
    expected = result["expected"]
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr, trace_check(check, checkout, env, result):
        try:
            proc = subprocess.run(argv, cwd=checkout, env=env, stdin=subprocess.DEVNULL,
                                  stdout=stdout, stderr=stderr, timeout=check.get("timeout", 60))
            result["actualExit"] = proc.returncode
        except (OSError, subprocess.TimeoutExpired) as exc:
            result.update(status="error", detail=f"检查未正常执行：{exc}")
        output, truncated, output_tail = _read_log(stdout)
        error, error_truncated, error_tail = _read_log(stderr)
        mismatch = _stdout_mismatch(stdout, expected) if result["actualExit"] == expected["exit"] else None
        if result["actualExit"] == expected["exit"] and "observations" in result:
            json_mismatch = _observe_json(stdout, result["observations"])
            mismatch = mismatch or json_mismatch
    result.update(output=output, stderr=error, outputTruncated=truncated or error_truncated)
    if result["status"] == "error":
        return result
    # A probe can assert an output *about* failed/skipped tests. Its successful
    # observation is distinct from directly claiming that the inner suite passed.
    exit_only = len(expected) == 1
    runner_check = (exit_only or _is_test_runner(argv)) and check.get("verifies") != "exit"
    runner_log = error_tail + "\n" + output_tail
    summary = _runner_summary(runner_log) if runner_check else None
    if summary:
        result["testSummary"] = summary
    if result["actualExit"] != expected["exit"]:
        result.update(status="failed", detail="实际退出码与约定不符")
    elif mismatch:
        result.update(status="failed", detail=mismatch)
    elif summary:
        detail = (f"{summary['runner']} 汇总中通过 {summary['passed']} 个用例，"
                  f"跳过 {summary['skipped']}，预期失败 {summary['expectedFailures']}")
        if not summary["passed"]:
            result.update(status="unverified", detail=detail + "。没有通过的用例可以验证业务行为")
        elif summary["skipped"] or summary["expectedFailures"]:
            result.update(status="partial", detail=detail + "。本次仅部分验证，请核对跳过或预期失败的用例对应哪项要求")
        else:
            result.update(status="passed", detail=detail)
    elif runner_check and re.search(r"(?m)^Ran 0 tests? in |^no tests ran in ", runner_log):
        result.update(status="unverified", detail="测试运行器报告零个已执行用例或全部跳过，未验证行为")
    else:
        detail = "仅验证了约定的退出码" if exit_only else "退出码和标准输出符合约定"
        result.update(status="passed", detail=detail)
    if exit_only and result["status"] in ("passed", "partial", "failed"):
        verifies = check.get("verifies")
        if verifies == "exit":
            result["detail"] += "。该场景明确验收退出码"
        elif _is_test_runner(argv) and (_named_test(argv) or verifies == "tests") and (
            summary or result["status"] == "failed"
        ):
            result["detail"] += "。该场景验收指定测试的执行结果"
        else:
            result["businessStatus"] = "unverified"
            result["detail"] += "。业务结果未验证，请约定可观察的输出或明确指定要验收的测试"
    if truncated and not exit_only and result["actualExit"] == expected["exit"]:
        result["detail"] += "。已使用完整标准输出核验，报告仅显示前 128000 字节"
    return result


def _runner_summary(log):
    match = re.search(r"(?m)^Ran (\d+) tests? in [^\n]+\n\s*OK(?: \(([^)\n]+)\))?[ \t]*$", log)
    if match:
        run = int(match[1])
        counts = dict(re.findall(r"(skipped|expected failures)=(\d+)", match[2] or ""))
        skipped = int(counts.get("skipped", 0))
        expected_failures = int(counts.get("expected failures", 0))
        return {"runner": "unittest", "run": run, "skipped": skipped, "expectedFailures": expected_failures,
                "passed": max(0, run - skipped - expected_failures)}
    match = re.search(r"(?m)^[= \t]*(\d+ [\w, ]+?) in [\d.]+s(?: \([\d:]+\))?[= \t]*$", log)
    if match:
        counts = {kind: int(n) for n, kind in re.findall(r"(\d+) (passed|skipped|xfailed|failed|errors?|xpassed)\b",
                                                         match[1])}
        if counts:
            return {"runner": "pytest", "run": sum(counts.values()), "skipped": counts.get("skipped", 0),
                    "expectedFailures": counts.get("xfailed", 0), "passed": counts.get("passed", 0)}
    return None


def requirement_status(checks, missing_scenarios=()):
    statuses = {check.get("businessStatus", check["status"]) for check in checks}
    if missing_scenarios:
        statuses.add("unverified")
    if "failed" in statuses:
        return "failed"
    if "error" in statuses:
        return "error"
    if statuses == {"passed"}:
        return "passed"
    return "partial" if statuses & {"passed", "partial"} else "unverified"
