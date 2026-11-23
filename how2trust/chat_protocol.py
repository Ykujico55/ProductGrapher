"""Decode a model's text-only snapshot lookup before showing a chat answer."""

import json
import re
from xml.etree import ElementTree


class ChatProtocolError(ValueError):
    """The reply contains a tool request that cannot be used for this chat."""


_ACTIONS = {"find", "read", "graph", "selection", "history", "review"}
# DeepSeek can emit either full-width vertical bars or their ASCII equivalents.
_DSML = re.compile(r"<\s*(/?)\s*(?:[|｜]\s*)+DSML\s*(?:[|｜]\s*)+")
_REQUEST_FENCE = re.compile(r"```(?:(?:json|xml|dsml)\b|(?=\s))\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)
_MARKDOWN_CODE = re.compile(r"(`+).*?\1", re.DOTALL)


def _dsml_lookup(text):
    xml = _DSML.sub(lambda match: "<" + match[1], text)
    # deepseek-flash sometimes fuses the invoke and first parameter into
    # <invoke name="lookup" string="true">find</parameter>.
    xml = re.sub(r'^(<(?:calls|function_calls)>\s*)<invoke name="lookup" string="true">([a-z]+)</parameter>',
                 r'\1<invoke name="lookup"><parameter name="lookup">\2</parameter>', xml)
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError as exc:
        raise ChatProtocolError("模型返回的检索指令不完整。") from exc
    if root.tag not in {"calls", "function_calls"} or len(root) != 1:
        raise ChatProtocolError("每轮只能发起一个快照检索。")
    call = root[0]
    if call.tag != "invoke" or call.get("name") != "lookup":
        raise ChatProtocolError("当前问答仅支持 lookup 快照检索。")
    if any((text or "").strip() for text in (root.text, call.tail, call.text)):
        raise ChatProtocolError("无法识别检索指令中的文本。")
    query = {}
    for param in call:
        name = param.get("name")
        if name == "action":
            name = "lookup"
        if (param.tag != "parameter" or not name or name in query or len(param)
                or (param.tail or "").strip()):
            raise ChatProtocolError("无法识别检索参数。")
        value = param.text or ""
        if param.get("string") == "false":
            try:
                value = json.loads(value)
            except ValueError as exc:
                raise ChatProtocolError("检索参数的值格式不正确。") from exc
        query[name] = value
    return query


def decode_lookup(text: str) -> dict | None:
    """Return one snapshot query, None for an answer, or raise for a broken request."""
    body = text.strip()
    fence = _REQUEST_FENCE.fullmatch(body)
    if fence and "```" not in fence[1]:
        body = fence[1].strip()
    if _DSML.match(body):
        query = _dsml_lookup(body)
    else:
        # Prose or a truncated opening before a raw tool marker is also an
        # unfinished request. A quoted example inside Markdown code is an answer.
        if _DSML.search(body):
            prose = _MARKDOWN_CODE.sub("", body)
            if (any(_DSML.match(line.lstrip()) for line in prose.splitlines())
                    or any(prose.startswith("invoke ", marker.end()) for marker in _DSML.finditer(prose))):
                raise ChatProtocolError("模型将检索指令与回答混在了一起。")
        if not body.startswith("{"):
            return None
        try:
            query = json.loads(body)
        except ValueError as exc:
            if re.match(r'^\{\s*"lookup"\s*:', body):
                raise ChatProtocolError("模型返回的 JSON 检索指令不完整。") from exc
            return None
        if "lookup" not in query:
            return None
    action = query.get("lookup")
    if not isinstance(action, str) or action not in _ACTIONS:
        raise ChatProtocolError("模型没有指定可用的快照检索类型。")
    return query
