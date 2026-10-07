"""dry_run_agent 的知识库检索分支：有命中时提示词要带上“资料不可信”的规则，检索不能因为内部错误被悄悄关掉。

回归：这个分支曾经用了没导入的 UNTRUSTED_RULE，NameError 被外层 except 吞掉，表现为“有命中资料时检索总是显示失败”。"""
import unittest
from types import SimpleNamespace
from unittest import mock

from service import agent_service
from service.prompt_guard import UNTRUSTED_RULE


class DryRunRagTests(unittest.TestCase):
    def run_dry(self, hits_context: str):
        agent = SimpleNamespace(id=7, user_id=3, rag_enabled=1, kb_top_k=5, kb_rerank_enabled=0, kb_refuse_when_empty=0)
        debug = {"agent": {"id": 7}, "readiness": {}, "skills": [], "tool_names": [], "tool_defaults_map": {},
                 "prompt": {"base_prompt": "你是助手", "skill_prompt": "", "final_prompt": "你是助手"}}
        found = {"hits": [{"id": 1}] if hits_context else [], "citations": [], "mode": "agent", "context": hits_context}
        with mock.patch.object(agent_service, "get_agent_by_id", return_value=agent), \
                mock.patch.object(agent_service, "get_agent_debug", return_value=debug), \
                mock.patch("service.rag.search_entry.search_for_agent", return_value=found):
            return agent_service.dry_run_agent(None, SimpleNamespace(id=3), 7, "报销流程是什么")

    def test_hits_add_reference_block_and_untrusted_rule(self):
        result = self.run_dry("[1] 报销需要发票")
        self.assertTrue(result["rag"]["ok"], result["rag"]["error"])
        self.assertEqual(result["rag"]["hit_count"], 1)
        prompt = result["prompt"]["final_prompt"]
        self.assertIn("报销需要发票", prompt)
        self.assertIn(UNTRUSTED_RULE, prompt)

    def test_no_hits_is_not_an_error(self):
        result = self.run_dry("")
        self.assertTrue(result["rag"]["ok"])
        self.assertEqual(result["rag"]["hit_count"], 0)


if __name__ == "__main__":
    unittest.main()
