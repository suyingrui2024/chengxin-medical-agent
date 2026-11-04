"""患者数据出域的三条硬规则，逐条由测试钉住（设计 §4.4）。

这三条规则以前只写在设计文档里，靠每个调用点自觉遵守：

1. 模型请求的**事件记录只存元数据**——完整的 prompt 曾经整个写进
   events.jsonl（每轮重复、永久保留），后来被改成 backend/model、
   字数与 sha256。这条测试防止它悄悄改回去。
2. **联网搜索是唯一的自由文本出站点**，因此每个 ``.search(`` 调用点
   都在白名单里，且每一条 query 都要么出自受控词表拼装、要么过
   ``scrub_free_query`` 脱敏、要么把病例原文片段表传给
   ``WebTools.search`` 的内置闸门。subagent 路径曾经绕过这一切。
3. **出站片段表只有一份定义**——education 与 subagent 必须禁止同一批
   患者原话（``patient_query_fragments``），两处各抄一份迟早漂移。

被拒的查询不产生外发请求，``web.blocked`` 事件也不带 query 原文——
那部分由 tests/subagents/test_web_tools.py 的行为测试钉住。
"""

from __future__ import annotations

import ast
import json
import pathlib
import unittest

from medagent.infra.backends import request_event_payload
from medagent.infra.web_tools import patient_query_fragments

SRC = pathlib.Path(__file__).resolve().parents[2] / "src" / "medagent"

#: ``.search(`` 调用点允许存在的文件。加新的出站路径时必须先到这里
#: 说明它如何满足 §4.4，再改这个清单。
SEARCH_CALLERS = {
    "reports/education.py",
    "subagents/coordinator/branch.py",
}

#: education.py 里唯一合法的 query 构造函数。
_CONTROLLED_BUILDERS = {"build_education_query", "scrub_free_query"}


def _iter_search_calls():
    """Yield (relative file, call node) for every ``.search(`` in src.

    排除两类非出站调用：``re.search`` 与编译后的正则模式常量
    （``_BP_PAIR.search`` 这类全大写接收者）。
    """
    for path in sorted(SRC.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute) or func.attr != "search":
                continue
            receiver = func.value
            name = getattr(receiver, "id", None) or getattr(receiver, "attr", None)
            if name == "re" or (name and name.isupper()):
                continue
            yield path.relative_to(SRC).as_posix(), node


class ModelRequestEventTests(unittest.TestCase):
    """model.requested 事件只存元数据，不存 prompt 内容。"""

    def test_payload_is_metadata_only(self):
        secret = "患者张三，35岁，手机13812345678，左侧太阳穴跳痛两天"
        messages = [
            {"role": "system", "content": "你是问诊助手"},
            {"role": "user", "content": secret},
        ]
        payload = request_event_payload("compatible", "test-model", messages)
        dumped = json.dumps(payload, ensure_ascii=False)
        for fragment in ("张三", "13812345678", "太阳穴", "问诊助手"):
            self.assertNotIn(fragment, dumped)
        self.assertNotIn("content", payload)
        self.assertNotIn("messages", payload)

    def test_payload_still_reconciles(self):
        """审计对账需要的字段还在：条数、字数、可匹配的指纹。"""
        messages = [{"role": "user", "content": "头痛两天"}]
        payload = request_event_payload("dsh", "m", messages)
        self.assertEqual(payload["backend"], "dsh")
        self.assertEqual(payload["message_count"], 1)
        self.assertEqual(payload["message_chars"], [4])
        self.assertEqual(payload["total_chars"], 4)
        self.assertRegex(payload["content_sha256"], r"^[0-9a-f]{64}$")


class SearchCallSiteTests(unittest.TestCase):
    """每个 ``.search(`` 调用点都在白名单里，且 query 来源受控。"""

    def _enclosing_function(self, tree: ast.AST, node: ast.Call):
        best = None
        for wrap in ast.walk(tree):
            if isinstance(wrap, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if wrap.lineno <= node.lineno <= wrap.end_lineno:
                    best = wrap
        return best

    def _query_is_controlled(self, tree: ast.AST, node: ast.Call) -> bool:
        """query 实参要么直接是构造调用，要么是构造调用的结果。"""

        def _builder_call(value: ast.expr) -> bool:
            if not isinstance(value, ast.Call):
                return False
            func = value.func
            if isinstance(func, ast.Name):
                return func.id in _CONTROLLED_BUILDERS
            return isinstance(func, ast.Attribute) and (
                func.attr in _CONTROLLED_BUILDERS
            )

        arg = node.args[0] if node.args else node.keywords[0].value
        if _builder_call(arg):
            return True
        if isinstance(arg, ast.Name):
            owner = self._enclosing_function(tree, node)
            if owner is None:
                return False
            for assign in ast.walk(owner):
                if not isinstance(assign, ast.Assign):
                    continue
                if not any(
                    isinstance(t, ast.Name) and t.id == arg.id for t in assign.targets
                ):
                    continue
                if _builder_call(assign.value):
                    return True
        return False

    def test_every_search_call_site_is_gated(self):
        found = []
        for rel, node in _iter_search_calls():
            self.assertIn(
                rel,
                SEARCH_CALLERS,
                f"{rel} 新增了 .search( 出站调用：先接入 §4.4 闸门，"
                f"再把它加进 SEARCH_CALLERS 并写明理由",
            )
            if rel == "subagents/coordinator/branch.py":
                # subagent 的 query 来自模型 arguments，必须把病例原文
                # 片段表传给 WebTools.search 的内置闸门。
                self.assertTrue(
                    any(kw.arg == "forbidden_fragments" for kw in node.keywords),
                    "subagent 的 search 调用必须传 forbidden_fragments",
                )
            else:
                path = SRC / rel
                tree = ast.parse(path.read_text(encoding="utf-8"))
                self.assertTrue(
                    self._query_is_controlled(tree, node),
                    f"{rel} 的 search query 必须出自 "
                    f"build_education_query / scrub_free_query",
                )
            found.append(rel)
        # 白名单里的文件必须真的还有调用点（搬走时同步收紧清单）。
        self.assertEqual(set(found), SEARCH_CALLERS)

    def test_fragments_have_one_definition(self):
        """出站片段表唯一定义，两条搜索路径都从它取。"""
        import inspect

        from medagent.infra import web_tools
        from medagent.reports import education
        from medagent.subagents.coordinator import branch

        self.assertIs(patient_query_fragments, web_tools.patient_query_fragments)
        self.assertIs(education.patient_query_fragments, web_tools.patient_query_fragments)
        self.assertIs(branch.patient_query_fragments, web_tools.patient_query_fragments)
        # helper 覆盖主诉与全部 evidence——这是 §4.4 禁止外发的两块原文。
        source = inspect.getsource(web_tools.patient_query_fragments)
        self.assertIn("chief_complaint", source)
        self.assertIn("evidence", source)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
