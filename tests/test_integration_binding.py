"""员工自助绑定飞书 / 钉钉（绑定码、一键授权）、未绑定来访者、群里只处理 @ 本机器人、接入自检、提醒未绑定员工。

飞书 / 钉钉的请求按两家文档的算法在测试里自己签名加密（复用 test_integrations 的构造函数），不连真实平台。
"""
import os
import unittest
import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlparse

from service.integrations import self_binding
from tests import _route_client as rc
from tests.test_integrations import dingtalk_robot_request, fake_app, feishu_message, feishu_request
from utils.timeutil import utcnow

_AVAILABLE, _WHY = rc.route_tests_available()


class CommandTest(unittest.TestCase):
    def test_parse_bind_command(self):
        self.assertEqual(self_binding.parse_command("绑定 123456"), "123456")
        self.assertEqual(self_binding.parse_command("  绑定：654321 "), "654321")
        self.assertEqual(self_binding.parse_command("bind 000001"), "000001")
        self.assertIsNone(self_binding.parse_command("绑定 12345"))
        self.assertIsNone(self_binding.parse_command("帮我绑定一下 123456 这个客户"))


@unittest.skipUnless(_AVAILABLE, _WHY)
class SelfBindingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from sqlalchemy import text
        from models.async_db import AsyncSessionLocal
        from models.init_db import SessionLocal
        from service import organization_admin_service
        from service.integrations import apps
        from tests._async_helpers import run_async

        cls.db = SessionLocal()
        if cls.db.execute(text("SELECT COUNT(*) FROM collaboration_app")).scalar():
            cls.db.close()
            raise unittest.SkipTest("库里已经有真实的飞书 / 钉钉配置，不在这个库上跑（不覆盖真实配置）")
        cls.client = rc.make_client()
        cls.admin = rc.create_user("bind-admin")
        cls.alice = rc.create_user("bind-alice")
        cls.bob = rc.create_user("bind-bob")

        async def enroll():
            async with AsyncSessionLocal() as db:
                for user in (cls.alice, cls.bob):
                    await organization_admin_service.add_org_member(db, cls.admin["id"], user["id"])
        run_async(enroll())
        cls.feishu = fake_app("feishu")
        cls.dingtalk = fake_app("dingtalk")
        for app in (cls.feishu, cls.dingtalk):
            apps.save_sync(cls.db, app.provider, cls.admin["id"], app_id=app.app_id, app_secret=app.app_secret,
                           verification_token=app.verification_token, encrypt_key=app.encrypt_key,
                           robot_code=app.robot_code, card_template_id="", enabled=True)

    @classmethod
    def tearDownClass(cls):
        from sqlalchemy import text
        tenants = {"a": cls.feishu.app_id, "b": cls.dingtalk.app_id}
        users = {"u": cls.alice["id"], "v": cls.bob["id"], "w": cls.admin["id"]}
        cls.db.execute(text("DELETE FROM external_event_inbox WHERE tenant_id IN (:a, :b)"), tenants)
        cls.db.execute(text("DELETE FROM external_user_binding WHERE external_tenant_id IN (:a, :b)"), tenants)
        cls.db.execute(text("DELETE FROM collaboration_app WHERE app_id IN (:a, :b)"), tenants)
        cls.db.execute(text("DELETE FROM external_bind_code WHERE user_id IN (:u, :v, :w)"), users)
        cls.db.execute(text("DELETE FROM notification WHERE user_id IN (:u, :v, :w)"), users)
        cls.db.commit()
        cls.db.close()
        rc.cleanup()

    def setUp(self):
        from sqlalchemy import text
        self.db.commit()
        self.db.execute(text("DELETE FROM external_user_binding WHERE external_tenant_id IN (:a, :b)"),
                        {"a": self.feishu.app_id, "b": self.dingtalk.app_id})
        self.db.execute(text("DELETE FROM external_bind_code WHERE user_id IN (:u, :v)"), {"u": self.alice["id"], "v": self.bob["id"]})
        self.db.commit()
        self.sent = []
        patches = [
            patch("service.integrations.feishu.messages.send_text", side_effect=lambda app, ctx, t: self.sent.append(t)),
            patch("service.integrations.dingtalk.messages.send_text", side_effect=lambda app, ctx, t: self.sent.append(t)),
            patch("service.integrations.feishu.client.user_name", return_value="艾丽斯"),
            patch("service.integrations.feishu.client.bot_open_id", return_value="ou_bot"),
            patch("service.integrations.dingtalk.adapter.DingTalkAdapter.user_name", return_value="鲍勃"),
            patch("service.integrations.dispatcher.choose_agent", new=AsyncMock(return_value=(101, "中央助手"))),
            patch.dict(os.environ, {"INTEGRATION_BIND_RATE_LIMIT": "1000"}),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.run_turn = AsyncMock(return_value=({"answer": "好的", "conversation_id": 1}, SimpleNamespace(public=lambda: None)))
        p = patch("service.chat_pipeline.run_turn", new=self.run_turn)
        p.start()
        self.addCleanup(p.stop)

    def fresh(self):
        self.db.commit()
        self.db.expire_all()
        return self.db

    def post_feishu(self, open_id, text_, **kw):
        headers, body = feishu_request(self.feishu, feishu_message(self.feishu, open_id, text_, **kw))
        return self.client.post("/integrations/feishu/events", content=body, headers={**headers, "Content-Type": "application/json"})

    def code_for(self, user, provider="feishu"):
        r = self.client.post(f"/me/integrations/{provider}/bind-code", headers=user["headers"])
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["code"]

    def status(self, user, provider="feishu"):
        items = self.client.get("/me/integrations", headers=user["headers"]).json()["items"]
        return next(i for i in items if i["provider"] == provider)

    # -------- 绑定码

    def test_bind_with_code_then_use_the_agent(self):
        self.assertEqual(self.status(self.alice)["status"], "unbound")
        self.post_feishu("ou_alice", "你好")
        self.run_turn.assert_not_called()
        self.assertIn("获取绑定码", self.sent[-1])                       # 未绑定：回复具体绑定步骤
        code = self.code_for(self.alice)
        self.post_feishu("ou_alice", f"绑定 {code}")
        self.assertIn("绑定成功", self.sent[-1])
        status = self.status(self.alice)
        self.assertEqual((status["status"], status["external_name"]), ("bound", "艾丽斯"))
        self.assertTrue(status["bot_link"].startswith("https://applink.feishu.cn/"))
        self.post_feishu("ou_alice", "帮我查报销")
        self.assertEqual(self.run_turn.await_args.args[1].id, self.alice["id"])

    def test_code_is_single_use_and_expires(self):
        code = self.code_for(self.alice)
        self.post_feishu("ou_alice", f"绑定 {code}")
        self.post_feishu("ou_mallory", f"绑定 {code}")                  # 别人拿同一个码再用
        self.assertIn("绑定码不对或已过期", self.sent[-1])
        from sqlalchemy import text
        code = self.code_for(self.bob)
        self.db.execute(text("UPDATE external_bind_code SET expires_at=:t WHERE user_id=:u AND used_at IS NULL"),
                        {"t": utcnow() - timedelta(seconds=2), "u": self.bob["id"]})
        self.db.commit()
        self.post_feishu("ou_bob", f"绑定 {code}")
        self.assertIn("已过期", self.sent[-1])

    def test_new_code_invalidates_the_old_one(self):
        old = self.code_for(self.alice)
        new = self.code_for(self.alice)
        self.post_feishu("ou_alice", f"绑定 {old}")
        self.assertIn("已过期", self.sent[-1])
        self.post_feishu("ou_alice", f"绑定 {new}")
        self.assertIn("绑定成功", self.sent[-1])

    def test_code_must_be_sent_privately(self):
        code = self.code_for(self.alice)
        self.post_feishu("ou_alice", f"@_user_1 绑定 {code}", chat_type="group",
                         mentions=[{"key": "@_user_1", "id": {"open_id": "ou_bot"}}])
        self.assertIn("私聊", self.sent[-1])
        self.assertEqual(self.status(self.alice)["status"], "unbound")

    def test_one_to_one_rules(self):
        code = self.code_for(self.alice)
        self.post_feishu("ou_alice", f"绑定 {code}")
        r = self.client.post("/me/integrations/feishu/bind-code", headers=self.alice["headers"])
        self.assertEqual(r.status_code, 409)                              # 已绑定：要换绑先解绑
        code = self.code_for(self.bob)
        self.post_feishu("ou_alice", f"绑定 {code}")                      # 外部账号已属于 Alice
        self.assertIn("已经绑定了另一个平台账号", self.sent[-1])
        self.assertEqual(self.status(self.bob)["status"], "unbound")

    def test_admin_disabled_binding_cannot_be_self_restored(self):
        from sqlalchemy import text
        code = self.code_for(self.alice)
        self.post_feishu("ou_alice", f"绑定 {code}")
        self.db.execute(text("UPDATE external_user_binding SET status='disabled' WHERE external_user_id='ou_alice'"))
        self.db.commit()
        self.assertEqual(self.status(self.alice)["status"], "disabled")
        r = self.client.post("/me/integrations/feishu/bind-code", headers=self.alice["headers"])
        self.assertEqual(r.status_code, 409)

    def test_unbind_takes_effect_immediately(self):
        from service.integrations import identity
        code = self.code_for(self.alice)
        self.post_feishu("ou_alice", f"绑定 {code}")
        r = self.client.delete("/me/integrations/feishu/binding", headers=self.alice["headers"])
        self.assertEqual(r.status_code, 200)
        self.assertEqual(identity.resolve_sync(self.fresh(), "feishu", self.feishu.app_id, "ou_alice"), (None, "unbound"))

    def test_wrong_codes_are_rate_limited(self):
        with patch.dict(os.environ, {"INTEGRATION_BIND_RATE_LIMIT": "3"}):
            for _ in range(3):
                self.post_feishu("ou_guess", "绑定 000000")
            self.post_feishu("ou_guess", "绑定 000000")
        self.assertIn("尝试次数太多", self.sent[-1])

    def test_unbound_visitor_is_listed_for_admin(self):
        self.post_feishu("ou_visitor", "你好")
        with rc.admin_env(self.admin["name"]):
            rows = self.client.get("/admin/integrations/feishu/bindings", headers=rc.auth_headers(self.admin["id"])).json()
        visitor = next(r for r in rows if r["external_user_id"] == "ou_visitor")
        self.assertEqual((visitor["status"], visitor["external_name"]), ("unmatched", "艾丽斯"))

    def test_dingtalk_bind(self):
        code = self.code_for(self.bob, "dingtalk")
        payload = {"msgId": uuid.uuid4().hex, "msgtype": "text", "text": {"content": f"绑定 {code}"},
                   "senderStaffId": "staff_bob", "conversationType": "1", "robotCode": "robot"}
        headers, body = dingtalk_robot_request(self.dingtalk, payload)
        self.client.post("/integrations/dingtalk/events", content=body, headers={**headers, "Content-Type": "application/json"})
        self.assertIn("绑定成功", self.sent[-1])
        self.assertEqual(self.status(self.bob, "dingtalk")["external_name"], "鲍勃")

    # -------- 群消息只处理 @ 本机器人的

    def test_group_message_not_addressed_to_bot_is_ignored(self):
        code = self.code_for(self.alice)
        self.post_feishu("ou_alice", f"绑定 {code}")
        before = len(self.sent)
        self.post_feishu("ou_alice", "@_user_1 你看下", chat_type="group", mentions=[{"key": "@_user_1", "id": {"open_id": "ou_someone"}}])
        self.assertEqual(len(self.sent), before)
        self.run_turn.assert_not_called()
        self.post_feishu("ou_alice", "@_user_1 查报销", chat_type="group", mentions=[{"key": "@_user_1", "id": {"open_id": "ou_bot"}}])
        self.run_turn.assert_awaited_once()

    # -------- 一键授权

    def test_oauth_bind_and_state_is_single_use(self):
        r = self.client.get("/me/integrations/feishu/oauth/start", headers=self.alice["headers"])
        self.assertEqual(r.status_code, 400)                              # 没配公网地址：提示改用绑定码
        env = {"INTEGRATION_PUBLIC_BASE_URL": "https://agent.example.test/api", "INTEGRATION_WEB_BASE_URL": "https://agent.example.test"}
        with patch.dict(os.environ, env):
            url = self.client.get("/me/integrations/feishu/oauth/start", headers=self.alice["headers"]).json()["url"]
            query = parse_qs(urlparse(url).query)
            self.assertEqual(query["redirect_uri"], ["https://agent.example.test/api/integrations/feishu/oauth/callback"])
            state = query["state"][0]
            bad = self.client.get("/integrations/feishu/oauth/callback?code=c&state=forged", follow_redirects=False)
            self.assertIn("result=error", bad.headers["location"])
            with patch("service.integrations.oauth._feishu_identity",
                       return_value={"external_user_id": "ou_alice_oauth", "union_id": "on_1", "name": "艾丽斯"}):
                ok = self.client.get(f"/integrations/feishu/oauth/callback?code=c&state={state}", follow_redirects=False)
                again = self.client.get(f"/integrations/feishu/oauth/callback?code=c&state={state}", follow_redirects=False)
        self.assertTrue(ok.headers["location"].startswith("https://agent.example.test/settings/integrations?"))
        self.assertIn("result=ok", ok.headers["location"])
        self.assertIn("result=error", again.headers["location"])           # state 只能用一次
        self.assertEqual(self.status(self.alice)["status"], "bound")

    # -------- 管理员

    def test_readiness_and_invite(self):
        with rc.admin_env(self.admin["name"]):
            headers = rc.auth_headers(self.admin["id"])
            r = self.client.get("/admin/integrations/feishu/readiness", headers=headers).json()
            checks = {c["key"]: c for c in r["checks"]}
            self.assertFalse(checks["public_url"]["ok"])
            self.assertIn("INTEGRATION_PUBLIC_BASE_URL", checks["public_url"]["fix"])
            with patch.dict(os.environ, {"INTEGRATION_PUBLIC_BASE_URL": "https://agent.example.test/api",
                                         "TRUSTED_HOSTS": "127.0.0.1,localhost,testserver"}):
                checks = {c["key"]: c for c in self.client.get("/admin/integrations/feishu/readiness", headers=headers).json()["checks"]}
            self.assertTrue(checks["public_url"]["ok"])
            self.assertFalse(checks["trusted_host"]["ok"])                  # 公网域名不在 Host 白名单：回调会被拒
            sent = self.client.post("/admin/integrations/feishu/invite-unbound", headers=headers).json()
            self.assertGreaterEqual(sent["unbound"], 2)
        from sqlalchemy import text
        n = self.fresh().execute(text("SELECT COUNT(*) FROM notification WHERE user_id=:u AND link='/settings/integrations'"),
                                 {"u": self.alice["id"]}).scalar()
        self.assertEqual(n, 1)


if __name__ == "__main__":
    unittest.main()
