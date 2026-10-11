"""防编造核对（service/runtime/action_guard.py + ReActEngine）。

真实事故：采购助手在同一会话里不再调用工具，直接说“草稿创建成功”“提交成功”，系统里一张单子都没有。
"""
import json
import unittest

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import StructuredTool

import service.tools.procurement  # noqa: F401  注册真实的采购工具名和风险等级
from service.runtime import action_guard
from service.runtime.react_engine import ReActEngine
from tests._runtime_fakes import _FakeLLM

FAKE_SUBMITTED = "## 📋 采购申请提交成功\n- 申请ID：[系统已生成]\n- 状态：已提交，等待部门负责人审批"


def _lc_tool(name, result):
    """借用真实工具名（风险等级从注册表取），执行时返回固定结果。"""
    return StructuredTool.from_function(func=lambda **kwargs: result, name=name, description=name)


def drain(stream):
    events = []
    while True:
        try:
            events.append(next(stream))
        except StopIteration as stop:
            return events, stop.value


class ClaimDetectionTest(unittest.TestCase):
    def test_claims(self):
        for text in ("采购申请提交成功", "已为您创建采购申请草稿", "草稿已创建好，单号 #12", "已经帮你提交了请假申请"):
            self.assertTrue(action_guard.claims_action(text), text)

    def test_not_claims(self):
        for text in ("提交成功后会进入审批", "你名下已提交的申请有 3 张", "需要我为您提交这个申请吗？",
                     "库存充足，建议采购 0 张", ""):
            self.assertFalse(action_guard.claims_action(text), text)

    def test_outcomes(self):
        self.assertEqual(action_guard.tool_result_outcome('{"status": "confirmation_required"}'), "pending")
        self.assertEqual(action_guard.tool_result_outcome('{"error": "未知产品"}'), "failed")
        self.assertEqual(action_guard.tool_result_outcome('{"id": 7, "status": "DRAFT"}'), "ok")
        self.assertEqual(action_guard.tool_result_outcome("工具权限不足: x"), "failed")

    def test_risk_levels_come_from_registry(self):
        self.assertTrue(action_guard.is_action_tool("create_purchase_draft"))
        self.assertTrue(action_guard.is_action_tool("submit_purchase_request"))
        self.assertFalse(action_guard.is_action_tool("list_inventory"))


class EngineGuardTest(unittest.TestCase):
    def setUp(self):
        self.tools = [_lc_tool("list_inventory", json.dumps({"products": []})),
                      _lc_tool("create_purchase_draft", json.dumps({"id": 7, "status": "DRAFT"})),
                      _lc_tool("submit_purchase_request", json.dumps({"status": "confirmation_required",
                                                                      "confirmation_token": "t"}))]

    def run_both(self, responses_factory):
        results = {}
        for streaming in (False, True):
            llm = _FakeLLM(responses_factory())
            steps = []
            engine = ReActEngine(llm, self.tools, step_callback=steps.append)
            if streaming:
                events, result = drain(engine.invoke_stream("", "买 10 张 5070 显卡"))
                result["events"] = "".join(events)
            else:
                result = engine.invoke("", "买 10 张 5070 显卡")
            result["llm"], result["steps_seen"] = llm, steps
            results[streaming] = result
        return results

    def test_fabricated_claim_triggers_one_retry_and_model_then_really_does_it(self):
        def responses():
            return [
                AIMessage(content=FAKE_SUBMITTED),                                     # 编造
                AIMessage(content="", tool_calls=[{"name": "create_purchase_draft", "id": "c1",
                                                   "args": {"lines": [{"item_name": "5070 显卡", "quantity": 10,
                                                                       "estimated_unit_price": 4999}]}}]),
                AIMessage(content="", tool_calls=[{"name": "submit_purchase_request", "id": "c2",
                                                   "args": {"request_id": 7}}]),
                AIMessage(content="已为您创建采购单 #7，并生成了提交确认，请点确认即提交。"),
            ]
        for streaming, result in self.run_both(responses).items():
            with self.subTest(streaming=streaming):
                self.assertEqual(result["answer"], "已为您创建采购单 #7，并生成了提交确认，请点确认即提交。")
                retry_prompt = result["llm"].calls[1][-1]
                self.assertIsInstance(retry_prompt, HumanMessage)
                self.assertIn("系统核对", retry_prompt.content)
                self.assertEqual(sum(s.get("step_type") == "guard" for s in result["steps_seen"]), 1)
                self.assertNotIn("系统核对：这次回答没有实际调用", result["answer"])
                if streaming:
                    # 页面上先清空假回答（answer 事件内容为空），最后是真实回答
                    self.assertIn('event: answer\ndata: {"content": ""}', result["events"])
                    self.assertTrue(result["events"].rstrip().endswith("请点确认即提交。\"}"))

    def test_still_fabricating_after_retry_appends_warning(self):
        for streaming, result in self.run_both(lambda: [AIMessage(content=FAKE_SUBMITTED),
                                                        AIMessage(content=FAKE_SUBMITTED)]).items():
            with self.subTest(streaming=streaming):
                self.assertTrue(result["answer"].startswith(FAKE_SUBMITTED))
                self.assertIn("系统核对：这次回答没有实际调用业务系统", result["answer"])
                self.assertEqual(len(result["llm"].calls), 2)        # 只重试一次，不会无限循环
                self.assertEqual(sum(s.get("step_type") == "guard" for s in result["steps_seen"]), 2)

    def test_real_action_in_this_run_is_not_flagged(self):
        def responses():
            return [AIMessage(content="", tool_calls=[{"name": "create_purchase_draft", "id": "c1",
                                                       "args": {"lines": [{"sku": "A", "quantity": 1}]}}]),
                    AIMessage(content="已为您创建采购申请草稿 #7。")]
        for streaming, result in self.run_both(responses).items():
            with self.subTest(streaming=streaming):
                self.assertEqual(result["answer"], "已为您创建采购申请草稿 #7。")
                self.assertEqual(len(result["llm"].calls), 2)

    def test_failed_action_does_not_count(self):
        self.tools[1] = _lc_tool("create_purchase_draft", json.dumps({"error": "未知产品: X"}))
        def responses():
            return [AIMessage(content="", tool_calls=[{"name": "create_purchase_draft", "id": "c1",
                                                       "args": {"lines": [{"sku": "X", "quantity": 1}]}}]),
                    AIMessage(content="采购申请创建成功！"),
                    AIMessage(content="创建失败：产品目录里没有 X。")]
        result = self.run_both(responses)[False]
        self.assertEqual(result["answer"], "创建失败：产品目录里没有 X。")

    def test_honest_question_is_not_flagged(self):
        result = self.run_both(lambda: [AIMessage(content="目录里没有 5070 显卡，请告诉我每张的预估单价，我就替你起草。")])[False]
        self.assertEqual(len(result["llm"].calls), 1)
        self.assertNotIn("系统核对", result["answer"])

    def test_agent_without_action_tools_is_not_guarded(self):
        llm = _FakeLLM([AIMessage(content="已为您提交申请")])
        result = ReActEngine(llm, [self.tools[0]]).invoke("", "hi")
        self.assertEqual(result["answer"], "已为您提交申请")
        self.assertEqual(len(llm.calls), 1)


if __name__ == "__main__":
    unittest.main()
