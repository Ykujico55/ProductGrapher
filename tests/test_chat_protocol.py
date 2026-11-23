import unittest

from how2trust.chat_protocol import ChatProtocolError, decode_lookup


class ChatProtocolTests(unittest.TestCase):
    def test_json_lookup_and_whole_json_fence(self):
        command = '{"lookup":"history","query":"上一个问题"}'
        for body in (command, "```json\n" + command + "\n```", "```json " + command + "```"):
            self.assertEqual(decode_lookup(body), {"lookup": "history", "query": "上一个问题"})

    def test_dsml_lookup_from_deepseek(self):
        for marker, envelope, action in (("｜DSML｜", "calls", "lookup"),
                                         (" | | DSML | | ", "function_calls", "action")):
            body = f"""<{marker}{envelope}>
<{marker}invoke name="lookup">
<{marker}parameter name="{action}" string="true">find</{marker}parameter>
<{marker}parameter name="query" string="true">migrate</{marker}parameter>
<{marker}parameter name="offset" string="false">0</{marker}parameter>
</{marker}invoke>
</{marker}{envelope}>"""
            self.assertEqual(decode_lookup(body), {"lookup": "find", "query": "migrate", "offset": 0})

    def test_malformed_or_unknown_tool_is_not_an_answer(self):
        for body in (
            '<｜DSML｜calls><｜DSML｜invoke name="lookup" string="true">find</｜DSML｜parameter>',
            '<|DSML|calls><|DSML|invoke name="shell"></|DSML|invoke></|DSML|calls>',
            '<|DSML|calls><|DSML|invoke name="lookup"/><|DSML|invoke name="lookup"/></|DSML|calls>',
            '让我先查看源码。\n<|DSML|function_calls>',
            '让我先查看源码。<|DSML|calls><|DSML|invoke name="lookup">',
            '{"lookup":"find","query":',
            '{"lookup":[]}',
        ):
            with self.subTest(body=body), self.assertRaises(ChatProtocolError):
                decode_lookup(body)

    def test_deepseek_flash_fused_lookup_parameter(self):
        body = """<｜｜DSML｜｜ calls>
<｜｜DSML｜｜ invoke name="lookup" string="true">find</｜｜DSML｜｜ parameter>
<｜｜DSML｜｜ parameter name="query" string="true">migrate</｜｜DSML｜｜ parameter>
<｜｜DSML｜｜ parameter name="offset" string="false">0</｜｜DSML｜｜ parameter>
</｜｜DSML｜｜ invoke>
</｜｜DSML｜｜ calls>"""
        self.assertEqual(decode_lookup(body), {"lookup": "find", "query": "migrate", "offset": 0})
        self.assertEqual(decode_lookup("```xml\n" + body + "\n```"), decode_lookup(body))

    def test_human_explanation_and_code_examples_are_preserved(self):
        for body in (
            "该函数负责创建数据库连接。[db.py:L5]",
            '读出来的 JSON 是 `{"lookup":"selection"}`。',
            '工具请求示意：\n```xml\n<｜DSML｜calls>\n```',
            '```python\nprint("<|DSML|calls>")\n```',
            'print("<|DSML|calls>")',
            '```json\n{"version": 2}\n```',
        ):
            with self.subTest(body=body):
                self.assertIsNone(decode_lookup(body))
