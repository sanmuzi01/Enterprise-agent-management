"""企业助手用企业的模型连接：员工自己没配模型时，企业助手（中央 / 部门）运行期间用助手创建人（企业管理员）配置的连接；
员工自己配了仍用自己的；个人助手不受影响；运行结束后恢复。"""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from service.llm import llm_config_service as svc
from tests._async_helpers import run_async

CONFIGS = {(1, "glm-4"): {"model_name": "glm-4", "api_key": "admin-key", "api_url": "u"},
           (9, "deepseek-chat"): {"model_name": "deepseek-chat", "api_key": "owner-key", "api_url": "u"},
           (3, "glm-4"): {"model_name": "glm-4", "api_key": "own-key", "api_url": "u"}}


def own(db, user_id, model_name):
    return CONFIGS.get((user_id, model_name))


class EnterpriseCredentialsTest(unittest.TestCase):
    def setUp(self):
        for p in (patch.object(svc, "_own_api_config", side_effect=own),
                  patch.object(svc, "_own_api_key", side_effect=lambda db, u, m: (own(db, u, m) or {}).get("api_key")),
                  patch.object(svc, "_own_api_config_async", new=AsyncMock(side_effect=own))):
            p.start()
            self.addCleanup(p.stop)

    def test_no_fallback_outside_enterprise_agent_runs(self):
        self.assertIsNone(svc.get_api_config(None, 2, "glm-4"))

    def test_enterprise_agent_uses_admin_connection_when_employee_has_none(self):
        for agent_type in ("central", "department"):
            token = svc.use_agent_credentials(SimpleNamespace(agent_type=agent_type, user_id=1))
            try:
                self.assertEqual(svc.get_api_config(None, 2, "glm-4")["api_key"], "admin-key")
                self.assertEqual(svc.get_api_key(None, 2, "glm-4"), "admin-key")
                self.assertEqual(run_async(svc.async_get_api_config(None, 2, "glm-4"))["api_key"], "admin-key")
                self.assertEqual(svc.get_api_config(None, 3, "glm-4")["api_key"], "own-key")    # 自己配了用自己的
                self.assertIsNone(svc.get_api_config(None, 2, "deepseek-chat"))                  # 管理员也没配的模型
            finally:
                svc.reset_agent_credentials(token)
        self.assertIsNone(svc.get_api_config(None, 2, "glm-4"))                                  # 运行结束后恢复

    def test_falls_back_to_enterprise_owner_or_admin(self):
        # 部门助手的创建人（5）没配模型：依次找企业所有者 / 管理员里配了这个模型的人
        token = svc.use_agent_credentials(SimpleNamespace(agent_type="department", user_id=5), enterprise_admins=(9, 1))
        try:
            self.assertEqual(svc.get_api_config(None, 2, "glm-4")["api_key"], "admin-key")
            self.assertEqual(svc.get_api_config(None, 2, "deepseek-chat")["api_key"], "owner-key")
            self.assertEqual(run_async(svc.async_get_api_config(None, 2, "deepseek-chat"))["api_key"], "owner-key")
        finally:
            svc.reset_agent_credentials(token)

    def test_personal_agents_do_not_borrow_keys(self):
        token = svc.use_agent_credentials(SimpleNamespace(agent_type="personal", user_id=1), enterprise_admins=(1,))
        try:
            self.assertIsNone(svc.get_api_config(None, 2, "glm-4"))
        finally:
            svc.reset_agent_credentials(token)
        token = svc.use_agent_credentials(None)
        svc.reset_agent_credentials(token)


if __name__ == "__main__":
    unittest.main()
