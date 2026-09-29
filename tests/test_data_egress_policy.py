"""第五轮审计 P0-1：数据密级出口策略。

分三块：
  1. `IsModelAllowedTest`：不依赖 DB，纯规则测试——public/internal 不限制，
     restricted 不管白名单怎么配都拒绝，confidential 只有模型在白名单里才放行、
     默认空白名单等于什么模型都不批准。
  2. `FilterHitsBySensitivityTest`：真实 DB，建三个不同密级的知识库空间，验证
     `filter_hits_by_sensitivity_async` 按 hit 的 source.space_id 批量查密级、
     正确拆成"放行"/"拒绝"两组；以及 mode="agent"（遗留 Agent 私有库检索，没有
     空间概念）时改用 Agent 自己的 sensitivity。
  3. `AgentRuntimeEgressIntegrationTest`：真实调用 `agent_runtime._kb_retrieve_async`
     （mock 掉最底层的 `search_entry.search_for_agent_async`，其它全走真代码），
     证明混合密级的检索结果真的会被拦掉一部分，且剩下的 hits 重新拼出的 context/
     citations 编号是连续的（不会因为中间一条被拦掉就留空洞的"来源2"）。
"""
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from models.init_db import KnowledgeSpace, SessionLocal
from service import data_egress_policy
from tests import _route_client as rc
from tests._async_helpers import run_async as _run

_AVAILABLE, _WHY = rc.route_tests_available()


class IsModelAllowedTest(unittest.TestCase):
    def test_public_and_internal_always_allowed(self):
        self.assertTrue(data_egress_policy.is_model_allowed("deepseek-chat", "public"))
        self.assertTrue(data_egress_policy.is_model_allowed("deepseek-chat", "internal"))

    def test_restricted_always_blocked_even_with_allowlist(self):
        with patch.dict(os.environ, {"CONFIDENTIAL_TIER_ALLOWED_MODELS": "deepseek-chat"}, clear=False):
            self.assertFalse(data_egress_policy.is_model_allowed("deepseek-chat", "restricted"))

    def test_confidential_blocked_by_default_empty_allowlist(self):
        env = dict(os.environ)
        env.pop("CONFIDENTIAL_TIER_ALLOWED_MODELS", None)
        with patch.dict(os.environ, env, clear=True):
            self.assertFalse(data_egress_policy.is_model_allowed("glm-4", "confidential"))

    def test_confidential_allowed_only_for_models_on_allowlist(self):
        with patch.dict(os.environ, {"CONFIDENTIAL_TIER_ALLOWED_MODELS": "glm-4, deepseek-chat"}, clear=False):
            self.assertTrue(data_egress_policy.is_model_allowed("glm-4", "confidential"))
            self.assertTrue(data_egress_policy.is_model_allowed("deepseek-chat", "confidential"))
            self.assertFalse(data_egress_policy.is_model_allowed("qwen-plus", "confidential"))


def _hit(knowledge_id, content, space_id, **extra):
    return {
        "knowledge_id": knowledge_id, "content": content, "score": 0.9,
        "source": {"space_id": space_id, "space_name": f"space-{space_id}", "file_name": "f.pdf"},
        **extra,
    }


@unittest.skipUnless(_AVAILABLE, f"需要本地 MySQL：{_WHY}")
class FilterHitsBySensitivityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.user = rc.create_user("egress-user")
        db = SessionLocal()
        try:
            internal = KnowledgeSpace(user_id=cls.user["id"], name="egress-internal", sensitivity="internal")
            confidential = KnowledgeSpace(user_id=cls.user["id"], name="egress-confidential", sensitivity="confidential")
            restricted = KnowledgeSpace(user_id=cls.user["id"], name="egress-restricted", sensitivity="restricted")
            db.add_all([internal, confidential, restricted])
            db.commit()
            cls.internal_id = internal.id
            cls.confidential_id = confidential.id
            cls.restricted_id = restricted.id
        finally:
            db.close()

    @classmethod
    def tearDownClass(cls):
        rc.cleanup()

    def _filter(self, hits, mode="spaces", agent_sensitivity="internal", model_name="deepseek-chat"):
        async def _do():
            from models.async_db import AsyncSessionLocal
            async with AsyncSessionLocal() as db:
                return await data_egress_policy.filter_hits_by_sensitivity_async(
                    db, hits, mode, agent_sensitivity, model_name,
                )
        return _run(_do())

    def test_spaces_mode_blocks_confidential_and_restricted_by_default(self):
        hits = [
            _hit(1, "公开内容", self.internal_id),
            _hit(2, "机密内容", self.confidential_id),
            _hit(3, "限制内容", self.restricted_id),
        ]
        allowed, blocked = self._filter(hits)
        self.assertEqual([h["knowledge_id"] for h in allowed], [1])
        self.assertEqual({h["knowledge_id"] for h in blocked}, {2, 3})

    def test_spaces_mode_allows_confidential_when_model_on_allowlist(self):
        hits = [_hit(2, "机密内容", self.confidential_id)]
        with patch.dict(os.environ, {"CONFIDENTIAL_TIER_ALLOWED_MODELS": "deepseek-chat"}, clear=False):
            allowed, blocked = self._filter(hits)
        self.assertEqual(len(allowed), 1)
        self.assertEqual(blocked, [])

    def test_spaces_mode_still_blocks_restricted_even_on_confidential_allowlist(self):
        """白名单只对 confidential 生效——restricted 没有豁免路径。"""
        hits = [_hit(3, "限制内容", self.restricted_id)]
        with patch.dict(os.environ, {"CONFIDENTIAL_TIER_ALLOWED_MODELS": "deepseek-chat"}, clear=False):
            allowed, blocked = self._filter(hits)
        self.assertEqual(allowed, [])
        self.assertEqual(len(blocked), 1)

    def test_unknown_space_id_defaults_to_internal(self):
        hits = [_hit(9, "未知空间", 999_999_999)]
        allowed, blocked = self._filter(hits)
        self.assertEqual(len(allowed), 1)
        self.assertEqual(blocked, [])

    def test_agent_mode_uses_agent_sensitivity_not_space(self):
        """mode="agent"（遗留私有库检索）没有空间概念，source.space_id 是 None，
        必须整批用 Agent 自己的 sensitivity 判断，不能查空间表。"""
        hits = [_hit(1, "私有库内容", None)]
        allowed, blocked = self._filter(hits, mode="agent", agent_sensitivity="restricted")
        self.assertEqual(allowed, [])
        self.assertEqual(len(blocked), 1)

        allowed2, blocked2 = self._filter(hits, mode="agent", agent_sensitivity="internal")
        self.assertEqual(len(allowed2), 1)
        self.assertEqual(blocked2, [])

    def test_empty_hits_returns_empty_without_querying(self):
        allowed, blocked = self._filter([])
        self.assertEqual((allowed, blocked), ([], []))


@unittest.skipUnless(_AVAILABLE, f"需要本地 MySQL：{_WHY}")
class AgentRuntimeEgressIntegrationTest(unittest.TestCase):
    """证明策略拦截真的接进了 agent_runtime._kb_retrieve_async，不只是
    data_egress_policy 这个模块本身能跑。"""

    @classmethod
    def setUpClass(cls):
        cls.user = rc.create_user("egress-runtime-user")
        db = SessionLocal()
        try:
            internal = KnowledgeSpace(user_id=cls.user["id"], name="egress-rt-internal", sensitivity="internal")
            confidential = KnowledgeSpace(user_id=cls.user["id"], name="egress-rt-confidential", sensitivity="confidential")
            db.add_all([internal, confidential])
            db.commit()
            cls.internal_id = internal.id
            cls.confidential_id = confidential.id
        finally:
            db.close()

    @classmethod
    def tearDownClass(cls):
        rc.cleanup()

    def test_mixed_sensitivity_hits_are_filtered_and_context_renumbered(self):
        from service.runtime import agent_runtime

        fake_res = {
            "mode": "spaces",
            "hits": [
                _hit(101, "第一条：可以给外部模型看", self.internal_id),
                _hit(102, "第二条：机密，不能给外部模型看", self.confidential_id),
                _hit(103, "第三条：又一条可以看的", self.internal_id),
            ],
            "context": "占位（真实值应该被重新组装，不是这个）",
            "citations": [{"index": 1}, {"index": 2}, {"index": 3}],
            "refused": False,
            "stats": None,
        }
        agent = SimpleNamespace(
            model_name="deepseek-chat", sensitivity="internal",
            kb_top_k=5, kb_rerank_enabled=0, kb_refuse_when_empty=1,
        )

        async def _do():
            from models.async_db import AsyncSessionLocal
            with patch("service.rag.search_entry.search_for_agent_async", AsyncMock(return_value=fake_res)):
                async with AsyncSessionLocal() as db:
                    return await agent_runtime._kb_retrieve_async(db, agent, self.user["id"], 1, "查一下")

        result = _run(_do())
        self.assertEqual(result["hit_count"], 2)
        self.assertEqual(result["blocked_count"], 1)
        self.assertEqual([h["knowledge_id"] for h in result["hits"]], [101, 103])
        # 机密内容不能出现在最终拼给模型的 context 里——这是这次修复最核心的断言。
        self.assertNotIn("机密", result["context"])
        self.assertIn("第一条", result["context"])
        self.assertIn("第三条", result["context"])
        # 重新编号后是连续的 来源1/来源2，不是留着空洞的 来源1/来源3。
        self.assertIn("【来源1】", result["context"])
        self.assertIn("【来源2】", result["context"])
        self.assertNotIn("【来源3】", result["context"])
        self.assertEqual(len(result["citations"]), 2)

    def test_no_blocked_hits_reuses_original_context_unchanged(self):
        """回归保护：全部放行时不应该触发重新组装，直接用检索模块自己产出的
        context/citations（避免"改动本身"引入格式差异）。"""
        from service.runtime import agent_runtime

        fake_res = {
            "mode": "spaces",
            "hits": [_hit(201, "都可以看", self.internal_id)],
            "context": "【来源1】原样保留的 context",
            "citations": [{"index": 1, "knowledge_id": 201}],
            "refused": False,
            "stats": None,
        }
        agent = SimpleNamespace(
            model_name="deepseek-chat", sensitivity="internal",
            kb_top_k=5, kb_rerank_enabled=0, kb_refuse_when_empty=1,
        )

        async def _do():
            from models.async_db import AsyncSessionLocal
            with patch("service.rag.search_entry.search_for_agent_async", AsyncMock(return_value=fake_res)):
                async with AsyncSessionLocal() as db:
                    return await agent_runtime._kb_retrieve_async(db, agent, self.user["id"], 1, "查一下")

        result = _run(_do())
        self.assertEqual(result["blocked_count"], 0)
        self.assertEqual(result["context"], "【来源1】原样保留的 context")


if __name__ == "__main__":
    unittest.main()
