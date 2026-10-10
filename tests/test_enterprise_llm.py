"""企业统一的模型连接：管理员按服务商配置一次 API Key，全公司共用。

保证三件事：
1. 密钥只写不读（接口只返回末四位，库里是密文）；
2. 员工没有个人密钥时自动用企业统一的，有个人密钥时仍然优先用个人的；
3. 企业连接一变，所有人的缓存立刻作废（不会继续用旧密钥或旧状态）。

这些测试会动 enterprise_llm_connection 表：开始前先把表里已有的真实连接备份下来、测完原样还原，不会弄丢你配好的密钥。"""
import unittest
from unittest import mock

from sqlalchemy import text

from models.init_db import EnterpriseLlmConnection, SessionLocal
from tests import _route_client as rc
from tests._async_helpers import run_async as _run

_AVAILABLE, _WHY = rc.route_tests_available()

KEY = "sk-enterprise-secret-1234567890"
PERSONAL_KEY = "sk-personal-override-0987654321"


def _run_db(fn):
    from models.async_db import AsyncSessionLocal

    async def _wrapper():
        async with AsyncSessionLocal() as db:
            return await fn(db)

    return _run(_wrapper())


@unittest.skipUnless(_AVAILABLE, f"需要本地 MySQL：{_WHY}")
class EnterpriseLlmTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.admin = rc.create_user("ell-admin")
        cls.staff = rc.create_user("ell-staff")
        cls.client = rc.make_client()
        cls.env = rc.admin_env(cls.admin["name"])
        cls.env.start()
        with SessionLocal() as db:        # 备份真实数据，测完还原
            cls.backup = [dict(r) for r in db.execute(text("SELECT * FROM enterprise_llm_connection")).mappings().all()]
            db.execute(text("DELETE FROM enterprise_llm_connection"))
            db.commit()

    @classmethod
    def tearDownClass(cls):
        cls.env.stop()
        with SessionLocal() as db:
            db.execute(text("DELETE FROM enterprise_llm_connection"))
            for row in cls.backup:
                db.add(EnterpriseLlmConnection(**row))
            db.commit()
        rc.cleanup()

    def setUp(self):
        with SessionLocal() as db:
            db.execute(text("DELETE FROM enterprise_llm_connection"))
            db.execute(text("DELETE FROM llm_config WHERE user_id IN (:a, :b)"), {"a": self.admin["id"], "b": self.staff["id"]})
            db.commit()
        from service.llm.enterprise_llm_service import invalidate_all_llm_caches
        invalidate_all_llm_caches()

    def api(self, method, path, user=None, **kwargs):
        return getattr(self.client, method)(f"/admin/llm-connections{path}", headers=(user or self.admin)["headers"], **kwargs)

    def connect(self, provider="zhipu", key=KEY):
        response = self.api("put", f"/{provider}", json={"api_key": key})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    @staticmethod
    def resolve(user, model_name):
        from service.llm.llm_config_service import get_api_config
        with SessionLocal() as db:
            return get_api_config(db, user["id"], model_name)

    @staticmethod
    def resolve_async(user, model_name):
        from service.llm.llm_config_service import async_get_api_config
        return _run_db(lambda db: async_get_api_config(db, user["id"], model_name))

    def save_personal(self, user, model_name, key=PERSONAL_KEY):
        from service.llm.llm_config_service import async_save_config
        from types import SimpleNamespace
        return _run_db(lambda db: async_save_config(db, SimpleNamespace(id=user["id"]), model_name, key))

    # ---------- 管理 ----------
    def test_secret_is_write_only(self):
        item = self.connect()
        self.assertTrue(item["connected"] and item["is_active"])
        self.assertEqual(item["key_hint"], "****7890")
        self.assertNotIn(KEY, str(item))
        self.assertNotIn(KEY, self.api("get", "").text)
        with SessionLocal() as db:
            stored = db.execute(text("SELECT api_key FROM enterprise_llm_connection WHERE provider='zhipu'")).scalar()
        self.assertNotIn(KEY, stored)
        self.assertNotEqual(stored, KEY)

    def test_list_shows_every_supported_provider(self):
        providers = {item["provider"]: item for item in self.api("get", "").json()}
        self.assertTrue({"zhipu", "openai", "deepseek", "moonshot", "qwen", "perplexity"} <= set(providers))
        self.assertFalse(providers["openai"]["connected"])
        self.assertIn("glm-4", providers["zhipu"]["chat_models"])
        self.assertIn("embedding-3", providers["zhipu"]["embedding_models"])

    def test_replacing_the_key_and_toggling(self):
        self.connect(key=KEY)
        self.connect(key="sk-replacement-key-abcdefgh")
        self.assertEqual(self.resolve(self.staff, "glm-4")["api_key"], "sk-replacement-key-abcdefgh")
        off = self.api("patch", "/zhipu", json={"is_active": False}).json()
        self.assertFalse(off["is_active"])
        self.assertIsNone(self.resolve(self.staff, "glm-4"), "停用后立即不再使用（缓存已作废）")
        self.api("patch", "/zhipu", json={"is_active": True})
        self.assertIsNotNone(self.resolve(self.staff, "glm-4"))

    def test_removing(self):
        self.connect()
        self.assertEqual(self.api("delete", "/zhipu").status_code, 200)
        self.assertIsNone(self.resolve(self.staff, "glm-4"))
        self.assertEqual(self.api("delete", "/zhipu").status_code, 404)

    def test_bad_input_is_rejected(self):
        for provider, key in (("nope", KEY), ("zhipu", "short"), ("zhipu", "has a space in it 123456")):
            response = self.api("put", f"/{provider}", json={"api_key": key})
            self.assertEqual(response.status_code, 400, (provider, key, response.text))
        self.assertEqual(self.api("patch", "/openai", json={"is_active": True}).status_code, 404)

    def test_only_admins_can_manage(self):
        for method, path, body in (("get", "", None), ("put", "/zhipu", {"api_key": KEY}), ("patch", "/zhipu", {"is_active": False}),
                                   ("delete", "/zhipu", None), ("post", "/zhipu/test", None)):
            kwargs = {"json": body} if body else {}
            self.assertEqual(self.api(method, path, user=self.staff, **kwargs).status_code, 403, (method, path))

    def test_connection_test_uses_the_enterprise_key_not_the_admins_personal_one(self):
        self.save_personal(self.admin, "glm-4", "sk-admins-own-key-zzzzzzzz")
        self.connect()
        seen = {}

        async def fake_probe(model_name, config):
            seen[model_name] = config["api_key"]
            return {"ok": True, "model_name": model_name, "message": "连接正常"}

        with mock.patch("service.llm.llm_config_service.probe_model", fake_probe):
            result = self.api("post", "/zhipu/test").json()
        self.assertTrue(result["ok"])
        self.assertEqual(set(seen), {"glm-4", "embedding-3"}, "聊天和资料读取各测一次")
        self.assertEqual(set(seen.values()), {KEY})

    def test_a_failed_probe_is_reported_not_raised(self):
        self.connect()

        async def failing(model_name, config):
            return {"ok": False, "model_name": model_name, "message": "连接测试失败", "error": "401"}

        with mock.patch("service.llm.llm_config_service.probe_model", failing):
            result = self.api("post", "/zhipu/test").json()
        self.assertFalse(result["ok"])

    # ---------- 员工侧：怎么用 ----------
    def test_staff_without_a_personal_key_use_the_enterprise_one(self):
        self.assertIsNone(self.resolve(self.staff, "glm-4"))
        self.connect()
        for fetch in (self.resolve, self.resolve_async):
            config = fetch(self.staff, "glm-4")
            self.assertEqual(config["api_key"], KEY)
            self.assertIn("bigmodel", config["api_url"])
        self.assertEqual(self.resolve(self.staff, "embedding-3")["api_key"], KEY, "同一个服务商下的资料读取模型也能用")
        self.assertIsNone(self.resolve(self.staff, "gpt-4o"), "别的服务商没连接就不能用")

    def test_a_personal_key_still_wins(self):
        self.connect()
        self.save_personal(self.staff, "glm-4")
        self.assertEqual(self.resolve(self.staff, "glm-4")["api_key"], PERSONAL_KEY)
        self.assertEqual(self.resolve_async(self.staff, "glm-4")["api_key"], PERSONAL_KEY)
        self.assertEqual(self.resolve(self.staff, "glm-4-flash")["api_key"], KEY, "没有个人密钥的其他模型仍用企业的")

    def test_a_disabled_personal_key_falls_back_to_the_enterprise_one(self):
        self.connect()
        self.save_personal(self.staff, "glm-4")
        with SessionLocal() as db:
            db.execute(text("UPDATE llm_config SET is_active=0 WHERE user_id=:u"), {"u": self.staff["id"]})
            db.commit()
        from service.llm.enterprise_llm_service import invalidate_all_llm_caches
        invalidate_all_llm_caches()
        self.assertEqual(self.resolve(self.staff, "glm-4")["api_key"], KEY)

    def test_the_personal_page_never_shows_or_changes_the_enterprise_connection(self):
        from service.llm.llm_config_service import async_delete_config_by_model, async_list_configs
        from types import SimpleNamespace
        self.connect()
        staff = SimpleNamespace(id=self.staff["id"])
        self.assertEqual(_run_db(lambda db: async_list_configs(db, staff)), [], "个人配置页里没有企业连接")
        self.save_personal(self.staff, "glm-4")
        self.assertEqual([c["model_name"] for c in _run_db(lambda db: async_list_configs(db, staff))], ["glm-4"])
        _run_db(lambda db: async_delete_config_by_model(db, staff, "glm-4"))
        self.assertEqual(self.resolve(self.staff, "glm-4")["api_key"], KEY, "删掉个人配置后回到企业统一连接，企业连接本身没被动")

    def test_every_model_of_a_connected_provider_becomes_available(self):
        from models.llm_config_dao import list_configs_by_user
        self.connect()
        with SessionLocal() as db:
            names = {c.model_name for c in list_configs_by_user(db, self.staff["id"])}
        self.assertTrue({"glm-4", "glm-4-flash", "glm-4-plus", "embedding-3"} <= names)
        self.assertFalse({"gpt-4o", "deepseek-chat"} & names)
        self.assertNotIn("BAAI/bge-small-zh-v1.5", names, "本地向量模型不靠企业连接")

    def test_the_default_embedding_model_follows_the_enterprise_connection(self):
        from service.llm.llm_config_service import async_get_first_embedding_config
        self.assertIsNone(_run_db(lambda db: async_get_first_embedding_config(db, self.staff["id"])))
        self.connect()
        config = _run_db(lambda db: async_get_first_embedding_config(db, self.staff["id"]))
        self.assertEqual((config["model_name"], config["api_key"]), ("embedding-3", KEY))

    def test_staff_can_see_which_providers_are_connected_but_never_a_key(self):
        self.connect()
        self.connect("deepseek", "sk-deepseek-key-abcdefghij")
        self.api("patch", "/deepseek", json={"is_active": False})
        response = self.client.get("/llm_config/enterprise_connections", headers=self.staff["headers"])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), [{"provider": "zhipu", "label": "智谱 AI"}])
        self.assertNotIn("key", response.text.lower())

    # ---------- 智能体编辑器里的提示 ----------
    def test_agent_options_and_readiness_know_which_models_are_connected(self):
        import service.agent_admin_service as agents
        from models.init_db import Agent
        options = _run_db(lambda db: agents.agent_options(db, self.admin["id"]))
        self.assertEqual(options["connected_models"], [])
        self.connect()
        options = _run_db(lambda db: agents.agent_options(db, self.admin["id"]))
        self.assertIn("glm-4", options["connected_models"])
        self.assertNotIn("gpt-4o", options["connected_models"])

        for model, expected in (("glm-4", "ok"), ("gpt-4o", "warn")):
            result = _run_db(lambda db, m=model: agents.readiness_of(
                db, Agent(user_id=self.admin["id"], name="ell-x", model_name=m, agent_type="central"),
                {"prompt": {"role": "r"}, "space_ids": [], "skill_ids": [], "knowledge_gaps": []}))
            level = next(i["level"] for i in result["items"] if i["key"] == "model")
            self.assertEqual(level, expected, model)


class FriendlyErrorTests(unittest.TestCase):
    """测试连接失败时，管理员看到的是人话：不带服务商地址、不带代码路径。"""

    def test_known_failures_are_translated(self):
        from service.llm.enterprise_llm_service import friendly_error
        cases = {
            "大模型请求失败: Client error '401 Unauthorized' for url 'https://open.bigmodel.cn/api/paas/v4/chat/completions'": "拒绝了这个密钥",
            "Error code: 429 - rate limit reached": "额度",
            "httpx.ConnectTimeout: timed out": "连不上服务商",
            "不支持的嵌入模型: embedding-3\n当前已注册: []\n新增模型：在 service/rag/embedding/ 下新建 xxx_embedding.py": "联系维护平台的工程师",
        }
        for raw, expected in cases.items():
            message = friendly_error(raw)
            self.assertIn(expected, message, raw)
            self.assertNotIn("http", message)
            self.assertNotIn("service/", message)

    def test_unknown_failures_are_short(self):
        from service.llm.enterprise_llm_service import friendly_error
        message = friendly_error("x" * 500)
        self.assertLessEqual(len(message), 90)


class EmbeddingFactoryLoadsClientsOnDemandTests(unittest.TestCase):
    def test_a_fresh_process_can_create_an_embedding_client_without_importing_anything_else(self):
        """管理后台的“测试连接”是第一个用到它的地方：注册表还是空的也要能创建（曾经报“当前已注册: []”）。"""
        import subprocess
        import sys
        from pathlib import Path
        root = Path(__file__).resolve().parent.parent
        code = ("from service.rag.embedding.factory import EmbeddingFactory;"
                "c = EmbeddingFactory.create('embedding-3', api_key='k'); print(type(c).__name__)")
        import os
        env = dict(os.environ, PYTHONIOENCODING="utf-8", CONSOLE_LOG_LEVEL="CRITICAL")
        result = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=60, env=env)
        self.assertEqual(result.returncode, 0, result.stderr[-500:])
        self.assertIn("Embedding", result.stdout)


if __name__ == "__main__":
    unittest.main()
