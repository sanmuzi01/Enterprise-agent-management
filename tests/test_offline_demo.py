"""离线演示模型：只在显式开启且非生产时存在；五类整理都能产出通过 schema 与原文依据校验的结果；不编造。"""
import json
import os
import unittest
from unittest.mock import patch

from service.automation_spec import extraction_prompt, parse_answer
from service.exceptions import InvalidInput
from service.llm import offline_demo as demo
from service.llm.factory import LLMFactory

SOURCES = {
    "leave": "我要申请年假，2026年10月12日至2026年10月14日，原因是家庭事务。",
    "expense": "10月1日出差高铁票260元，发票号G001；出租车48元，暂无发票。",
    "ticket": "三楼打印机卡纸后一直脱机，整个部门都没法打印，下午客户会议要用资料，比较着急。",
    "procurement": "行政部需要采购 PAPER-A4 共3件；另需 CHAIR01 共2件。",
    "crm": "2026年10月1日与客户沟通，对方希望先试用。约定2026年10月8日发送方案；预算尚未确定。",
}


def answer(kind, source):
    text, usage = demo.respond([{"role": "system", "content": extraction_prompt(kind)}, {"role": "user", "content": source}])
    return text, usage


class GatingTest(unittest.TestCase):
    def test_disabled_by_default_and_in_production(self):
        with patch.dict(os.environ, {"OFFLINE_DEMO_MODEL": ""}):
            self.assertFalse(demo.enabled())
        with patch.dict(os.environ, {"OFFLINE_DEMO_MODEL": "1"}), patch("service.config_validation.is_production", return_value=True):
            self.assertFalse(demo.enabled())
        with patch.dict(os.environ, {"OFFLINE_DEMO_MODEL": "1"}), patch("service.config_validation.is_production", return_value=False):
            self.assertTrue(demo.enabled())

    def test_factory_only_builds_the_offline_client_when_enabled(self):
        with patch.dict(os.environ, {"OFFLINE_DEMO_MODEL": "1"}), patch("service.config_validation.is_production", return_value=False):
            self.assertIsInstance(LLMFactory.create(demo.MODEL_NAME, "x"), demo.OfflineDemoClient)
        with patch.dict(os.environ, {"OFFLINE_DEMO_MODEL": ""}):
            self.assertNotIsInstance(LLMFactory.create(demo.MODEL_NAME, "x"), demo.OfflineDemoClient)


class ExtractionTest(unittest.TestCase):
    def test_every_workflow_output_passes_schema_and_evidence_checks(self):
        for kind, source in SOURCES.items():
            with self.subTest(kind=kind):
                text, usage = answer(kind, source)
                result = parse_answer(kind, text, source)
                self.assertTrue(usage["total_tokens"] > 0)
                self.assertTrue(result)

    def test_leave_values(self):
        result = json.loads(answer("leave", SOURCES["leave"])[0])
        self.assertEqual((result["leave_type_code"], result["start_date"], result["end_date"], result["reason"]),
                         ("annual", "2026-10-12", "2026-10-14", "家庭事务"))

    def test_leave_with_relative_dates_is_not_guessed(self):
        result = json.loads(answer("leave", "我想请年假，下周二到周四，家里有事")[0])
        self.assertIsNone(result["start_date"])
        self.assertTrue(result["warnings"])

    def test_expense_lines_categories_and_missing_invoice_warning(self):
        result = json.loads(answer("expense", SOURCES["expense"])[0])
        self.assertEqual([(l["category"], l["amount"], l["invoice_no"]) for l in result["lines"]],
                         [("TRAVEL", "260.00", "G001"), ("TRANSPORT", "48.00", None)])
        self.assertTrue(any("没有发票号" in w for w in result["warnings"]))

    def test_expense_without_amount_returns_no_invented_lines(self):
        result = json.loads(answer("expense", "上周出差回来了，晚点再报销")[0])
        self.assertEqual(result["lines"], [])
        with self.assertRaises(InvalidInput):   # 空清单不满足 schema，不会被当成有效结果保存
            parse_answer("expense", json.dumps(result, ensure_ascii=False), "上周出差回来了，晚点再报销")

    def test_ticket_classification_and_priority(self):
        result = json.loads(answer("ticket", SOURCES["ticket"])[0])
        self.assertEqual((result["category"], result["priority"]), ("INCIDENT", "URGENT"))
        self.assertEqual(json.loads(answer("ticket", "申请一台笔记本电脑用于外勤")[0])["category"], "DEVICE")

    def test_procurement_unknown_sku_is_left_blank_not_invented(self):
        result = json.loads(answer("procurement", "需要买一批办公用品")[0])
        self.assertIsNone(result["items"][0]["sku"])
        self.assertTrue(result["warnings"])

    def test_crm_task_dates(self):
        result = json.loads(answer("crm", SOURCES["crm"])[0])
        self.assertEqual(result["tasks"][0]["due_date"], "2026-10-08")


class ChatTest(unittest.TestCase):
    def test_plain_chat_is_honest_about_being_offline(self):
        text, _ = demo.respond([{"role": "user", "content": "帮我写一首诗"}])
        self.assertIn("离线演示模型", text)

    def test_connection_test(self):
        text, _ = demo.respond([{"role": "system", "content": "只需要简短回复"}, {"role": "user", "content": "请回复：连接正常"}])
        self.assertIn("连接正常", text)

    def test_langchain_model_never_calls_tools(self):
        model = demo.langchain_model().bind_tools([])
        message = model.invoke("你好")
        self.assertIn("离线演示模型", message.content)
        self.assertFalse(getattr(message, "tool_calls", []))


if __name__ == "__main__":
    unittest.main()
