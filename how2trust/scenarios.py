"""Draft reviewable business scenarios from requirements, before implementation."""

import json

from .acceptance import _criteria


def draft_scenarios(source, client):
    clauses = [{"id": f"R{i}", "text": text} for i, text in enumerate(_criteria(source), 1)]
    if not clauses:
        raise ValueError("请提供原始业务需求。")
    prompt = """将用户提供的需求转成可由人审阅的验收场景，用用户的语言。保留传入的每个 id。
只描述需求中有依据的初始状态、用户操作、可观察结果。遇到标题、例子或无法独立验收的文字，可以返回空场景数组。
不要假设未给定的结果，也不要生成代码或执行命令。只返回一个 JSON 对象：
{"requirements":[{"id":"R1","scenarios":[{"name":"场景名称","given":"前置条件","when":"用户操作","then":"可核查的预期结果"}]}]}。
同一需求的场景名称应各不相同。"""
    answer = client.complete([{"role": "system", "content": prompt},
                              {"role": "user", "content": json.dumps(clauses, ensure_ascii=False)}])
    try:
        rows = json.loads(answer.strip().removeprefix("```json").removesuffix("```").strip())["requirements"]
        if not isinstance(rows, list):
            raise ValueError()
        by_id = {r["id"]: r["scenarios"] for r in rows}
        if len(by_id) != len(rows) or set(by_id) != {c["id"] for c in clauses}:
            raise ValueError()
        for clause in clauses:
            scenarios = by_id[clause["id"]]
            if not isinstance(scenarios, list):
                raise ValueError()
            cleaned = []
            names = set()
            for s in scenarios:
                if not isinstance(s, dict) or set(s) != {"name", "given", "when", "then"}:
                    raise ValueError()
                if any(not isinstance(value, str) or not value.strip() for value in s.values()):
                    raise ValueError()
                scene = {k: s[k].strip() for k in ("name", "given", "when", "then")}
                if scene["name"] in names:
                    raise ValueError()
                names.add(scene["name"])
                cleaned.append(scene)
            clause.update(scenarios=cleaned, checks=[])
    except (ValueError, KeyError, TypeError) as exc:
        raise ValueError("模型未返回与原始需求逐项对应的具名场景，请检查模型输出配置后重试。") from exc
    return {"task": source.strip(), "requirements": clauses}
