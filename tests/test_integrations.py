"""飞书 / 钉钉接入层：验签解密、回调校验（签名错、过期、重放）、去重、身份映射、卡片令牌、管理接口。

不连真实的飞书 / 钉钉：发出去的消息、调用的开放接口都用 mock 截住；回调请求按两家文档的算法在测试里自己签名、加密。
"""
import json
import time
import unittest
import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from service.integrations import base
from service.integrations.base import (BusinessCard, CardAction, CardField, ConfirmAction, Handshake,
                                       InboundMessage, OpenUrlAction, OrgChange, RejectAction, VerificationError,
                                       WebCardRenderer)
from service.integrations.dingtalk import callback as dt_callback
from service.integrations.dingtalk import messages as dt_messages
from service.integrations.dingtalk import signature as dt_sig
from service.integrations.dingtalk.cards import DingTalkCardRenderer
from service.integrations.feishu import callback as fs_callback
from service.integrations.feishu import signature as fs_sig
from service.integrations.feishu.cards import FeishuCardRenderer
from service.exceptions import InvalidInput
from tests import _route_client as rc

_AVAILABLE, _WHY = rc.route_tests_available()
AES_KEY = "abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG"          # 钉钉 aes_key 固定 43 位
TOKEN = "f" * 32


def fake_app(provider, **kw):
    values = dict(id=1, organization_id=1, provider=provider, app_id=f"app_{uuid.uuid4().hex[:8]}", app_secret="secret-x",
                  verification_token="vtoken", encrypt_key="ekey-123" if provider == "feishu" else AES_KEY,
                  robot_code="robot", card_template_id="", enabled=True)
    values.update(kw)
    return SimpleNamespace(**values)


# ------------------------------------------------------------------ 请求构造（模拟飞书 / 钉钉）

def feishu_request(app, payload, *, ts=None, nonce=None, tamper=False, bad_signature=False):
    body = json.dumps({"encrypt": fs_sig.encrypt(app.encrypt_key, json.dumps(payload, ensure_ascii=False))}).encode()
    ts = str(int(time.time()) if ts is None else ts)
    nonce = nonce or uuid.uuid4().hex
    sig = fs_sig.event_signature(ts, nonce, app.encrypt_key, body)
    if bad_signature:
        sig = "0" * 64
    if tamper:
        body = body[:-2] + b" }"
    return {"X-Lark-Request-Timestamp": ts, "X-Lark-Request-Nonce": nonce, "X-Lark-Signature": sig}, body


def feishu_message(app, open_id, text="你好", *, event_id=None, chat_type="p2p", mentions=None):
    message = {"message_id": f"om_{uuid.uuid4().hex[:8]}", "chat_id": "oc_1", "chat_type": chat_type,
               "message_type": "text", "content": json.dumps({"text": text})}
    if mentions:
        message["mentions"] = mentions
    return {"schema": "2.0", "header": {"event_id": event_id or uuid.uuid4().hex, "token": app.verification_token,
                                        "event_type": "im.message.receive_v1", "app_id": app.app_id},
            "event": {"sender": {"sender_type": "user", "sender_id": {"open_id": open_id}}, "message": message}}


def feishu_card(app, open_id, value):
    return {"schema": "2.0", "header": {"event_id": uuid.uuid4().hex, "token": app.verification_token,
                                        "event_type": "card.action.trigger", "app_id": app.app_id},
            "event": {"operator": {"open_id": open_id}, "action": {"value": value},
                      "context": {"open_message_id": "om_card"}}}


def dingtalk_robot_request(app, payload, *, ts_ms=None, bad_signature=False):
    ts = str(int(time.time() * 1000) if ts_ms is None else ts_ms)
    sign = dt_sig.robot_sign(ts, "wrong" if bad_signature else app.app_secret)
    return {"timestamp": ts, "sign": sign}, json.dumps(payload, ensure_ascii=False).encode()


def dingtalk_event_request(app, event):
    encrypted = dt_sig.encrypt(app.encrypt_key, app.app_id, json.dumps(event))
    ts, nonce = str(int(time.time() * 1000)), uuid.uuid4().hex[:8]
    query = {"msg_signature": dt_sig.event_signature(app.verification_token, ts, nonce, encrypted), "timestamp": ts,
             "nonce": nonce}
    return query, json.dumps({"encrypt": encrypted}).encode()


class IntegrationAppConfigTest(unittest.TestCase):
    def test_feishu_cannot_be_enabled_without_callback_security_fields(self):
        from service.integrations import apps

        with patch.object(apps, "get_row_sync", return_value=None):
            with self.assertRaisesRegex(InvalidInput, "Verification Token、Encrypt Key"):
                apps.save_sync(
                    MagicMock(), "feishu", 1, app_id="cli_test", app_secret="secret",
                    verification_token=None, encrypt_key=None, robot_code=None,
                    card_template_id=None, enabled=True,
                )

    def test_verification_token_is_encrypted_at_rest(self):
        from service.integrations import apps
        from utils.crypto import encrypt

        row = SimpleNamespace(
            id=1, organization_id=1, provider="feishu", app_id="cli_old",
            encrypted_app_secret=encrypt("secret"), verification_token=None,
            encrypted_encrypt_key=encrypt("encrypt-key"), robot_code=None,
            card_template_id=None, enabled=0, updated_by=None,
            last_health_at=None, last_error=None,
        )
        db = MagicMock()
        with patch.object(apps, "get_row_sync", return_value=row), patch("service.audit_service.record"):
            apps.save_sync(
                db, "feishu", 1, app_id="cli_old", app_secret=None,
                verification_token="verify-token", encrypt_key=None, robot_code=None,
                card_template_id=None, enabled=True,
            )
        self.assertNotEqual(row.verification_token, "verify-token")
        self.assertTrue(row.verification_token.startswith("fernet:"))
        self.assertEqual(apps.credentials(row).verification_token, "verify-token")


# ------------------------------------------------------------------ 纯算法 / 解析

class SignatureTest(unittest.TestCase):
    def test_feishu_encrypt_round_trip_and_wrong_key(self):
        cipher = fs_sig.encrypt("key-1", '{"a": "中文"}')
        self.assertEqual(fs_sig.decrypt("key-1", cipher), '{"a": "中文"}')
        with self.assertRaises(VerificationError):
            fs_sig.decrypt("key-2", cipher)

    def test_feishu_signature_matches_documented_formula(self):
        import hashlib
        body = b'{"encrypt":"x"}'
        expected = hashlib.sha256(b"1700000000" + b"nonce" + b"key" + body).hexdigest()
        self.assertEqual(fs_sig.event_signature("1700000000", "nonce", "key", body), expected)

    def test_dingtalk_crypto_round_trip_and_app_key_check(self):
        cipher = dt_sig.encrypt(AES_KEY, "dingappkey", '{"EventType": "check_url"}')
        self.assertEqual(dt_sig.decrypt(AES_KEY, "dingappkey", cipher), '{"EventType": "check_url"}')
        with self.assertRaises(VerificationError):
            dt_sig.decrypt(AES_KEY, "another-app", cipher)

    def test_dingtalk_robot_sign_matches_documented_formula(self):
        import base64
        import hashlib
        import hmac
        expected = base64.b64encode(hmac.new(b"sec", b"1700000000000\nsec", hashlib.sha256).digest()).decode()
        self.assertEqual(dt_sig.robot_sign("1700000000000", "sec"), expected)

    def test_dingtalk_reply_is_encrypted_success(self):
        reply = dt_sig.encrypted_reply("tok", AES_KEY, "appk")
        self.assertEqual(dt_sig.decrypt(AES_KEY, "appk", reply["encrypt"]), "success")
        self.assertEqual(reply["msg_signature"],
                         dt_sig.event_signature("tok", reply["timeStamp"], reply["nonce"], reply["encrypt"]))


class FeishuCallbackTest(unittest.TestCase):
    def setUp(self):
        self.app = fake_app("feishu")

    def test_valid_message(self):
        headers, body = feishu_request(self.app, feishu_message(self.app, "ou_1", "@_user_1 帮我请假"))
        parsed = fs_callback.parse(self.app, headers, body)
        self.assertIsInstance(parsed, InboundMessage)
        self.assertEqual((parsed.external_user_id, parsed.text, parsed.tenant_id), ("ou_1", "帮我请假", self.app.app_id))

    def test_wrong_signature_rejected(self):
        headers, body = feishu_request(self.app, feishu_message(self.app, "ou_1"), bad_signature=True)
        with self.assertRaises(VerificationError):
            fs_callback.parse(self.app, headers, body)

    def test_tampered_body_rejected(self):
        headers, body = feishu_request(self.app, feishu_message(self.app, "ou_1"), tamper=True)
        with self.assertRaises(VerificationError):
            fs_callback.parse(self.app, headers, body)

    def test_expired_callback_rejected(self):
        headers, body = feishu_request(self.app, feishu_message(self.app, "ou_1"), ts=int(time.time()) - 3600)
        with self.assertRaisesRegex(VerificationError, "过期"):
            fs_callback.parse(self.app, headers, body)

    def test_replayed_nonce_rejected(self):
        headers, body = feishu_request(self.app, feishu_message(self.app, "ou_1"))
        fs_callback.parse(self.app, headers, body)
        with self.assertRaisesRegex(VerificationError, "重复"):
            fs_callback.parse(self.app, headers, body)

    def test_unencrypted_callback_rejected(self):
        body = json.dumps(feishu_message(self.app, "ou_1")).encode()
        with self.assertRaises(VerificationError):
            fs_callback.parse(self.app, {}, body)

    def test_wrong_verification_token_rejected(self):
        payload = feishu_message(self.app, "ou_1")
        payload["header"]["token"] = "other"
        headers, body = feishu_request(self.app, payload)
        with self.assertRaises(VerificationError):
            fs_callback.parse(self.app, headers, body)

    def test_url_verification_handshake(self):
        body = json.dumps({"encrypt": fs_sig.encrypt(self.app.encrypt_key, json.dumps(
            {"type": "url_verification", "challenge": "abc", "token": "vtoken"}))}).encode()
        self.assertEqual(fs_callback.parse(self.app, {}, body), Handshake({"challenge": "abc"}))

    def test_group_message_without_mention_is_not_for_the_bot(self):
        # 解析出来交给 dispatcher：本群开启了 CRM 记录就暂存，否则丢弃（见 test_crm_chat_capture）；不会当成对机器人说的话
        from service.integrations.feishu.adapter import FeishuAdapter
        headers, body = feishu_request(self.app, feishu_message(self.app, "ou_1", chat_type="group"))
        parsed = fs_callback.parse(self.app, headers, body)
        self.assertIsInstance(parsed, InboundMessage)
        self.assertEqual((parsed.chat_type, parsed.mention_ids), ("group", []))
        self.assertFalse(FeishuAdapter().addressed_to_bot(self.app, parsed))

    def test_card_action_only_accepts_action_and_token(self):
        headers, body = feishu_request(self.app, feishu_card(self.app, "ou_1", {"action": "confirm", "token": TOKEN}))
        parsed = fs_callback.parse(self.app, headers, body)
        self.assertIsInstance(parsed, CardAction)
        self.assertEqual((parsed.action, parsed.token), ("confirm", TOKEN))
        headers, body = feishu_request(self.app, feishu_card(self.app, "ou_1", {"action": "confirm", "amount": 999}))
        with self.assertRaises(VerificationError):
            fs_callback.parse(self.app, headers, body)

    def test_resigned_user_event(self):
        payload = {"schema": "2.0", "header": {"event_id": "e1", "token": "vtoken", "event_type": "contact.user.deleted_v3",
                                               "app_id": self.app.app_id}, "event": {"object": {"open_id": "ou_left"}}}
        headers, body = feishu_request(self.app, payload)
        parsed = fs_callback.parse(self.app, headers, body)
        self.assertIsInstance(parsed, OrgChange)
        self.assertEqual(parsed.left_user_ids, ["ou_left"])


class DingTalkCallbackTest(unittest.TestCase):
    def setUp(self):
        self.app = fake_app("dingtalk")

    def robot(self, **kw):
        return {"msgId": uuid.uuid4().hex, "msgtype": "text", "text": {"content": " 查一下报销 "}, "senderStaffId": "staff1",
                "conversationType": "1", "sessionWebhook": "https://oapi.dingtalk.com/robot/sendBySession?session=x",
                "robotCode": "robot", **kw}

    def test_valid_robot_message(self):
        headers, body = dingtalk_robot_request(self.app, self.robot())
        parsed = dt_callback.parse(self.app, headers, {}, body)
        self.assertIsInstance(parsed, InboundMessage)
        self.assertEqual((parsed.external_user_id, parsed.text), ("staff1", "查一下报销"))

    def test_wrong_signature_and_expired_rejected(self):
        headers, body = dingtalk_robot_request(self.app, self.robot(), bad_signature=True)
        with self.assertRaises(VerificationError):
            dt_callback.parse(self.app, headers, {}, body)
        headers, body = dingtalk_robot_request(self.app, self.robot(), ts_ms=int(time.time() * 1000) - 3_600_000)
        with self.assertRaisesRegex(VerificationError, "过期"):
            dt_callback.parse(self.app, headers, {}, body)

    def test_other_robot_rejected(self):
        headers, body = dingtalk_robot_request(self.app, self.robot(robotCode="someone-else"))
        with self.assertRaises(VerificationError):
            dt_callback.parse(self.app, headers, {}, body)

    def test_check_url_and_leave_org(self):
        query, body = dingtalk_event_request(self.app, {"EventType": "check_url"})
        parsed = dt_callback.parse(self.app, {}, query, body)
        self.assertIsInstance(parsed, Handshake)
        self.assertEqual(dt_sig.decrypt(AES_KEY, self.app.app_id, parsed.response["encrypt"]), "success")

        query, body = dingtalk_event_request(self.app, {"EventType": "user_leave_org", "UserId": ["u9"]})
        parsed = dt_callback.parse(self.app, {}, query, body)
        self.assertIsInstance(parsed, OrgChange)
        self.assertEqual(parsed.left_user_ids, ["u9"])

    def test_event_with_wrong_signature_rejected(self):
        query, body = dingtalk_event_request(self.app, {"EventType": "check_url"})
        query["msg_signature"] = "0" * 40
        with self.assertRaises(VerificationError):
            dt_callback.parse(self.app, {}, query, body)

    def test_card_action(self):
        headers, body = dingtalk_robot_request(self.app, {"outTrackId": "t1", "userId": "staff1", "content": json.dumps(
            {"cardPrivateData": {"params": {"action": "reject", "token": TOKEN}}})})
        parsed = dt_callback.parse_card_action(self.app, headers, {}, body)
        self.assertEqual((parsed.action, parsed.token, parsed.external_user_id), ("reject", TOKEN, "staff1"))

    def test_session_webhook_must_be_dingtalk_host(self):
        future = int(time.time() * 1000) + 600_000
        self.assertTrue(dt_messages._webhook({"session_webhook": "https://oapi.dingtalk.com/robot/x",
                                              "session_webhook_expires": future}))
        self.assertEqual(dt_messages._webhook({"session_webhook": "http://169.254.169.254/latest",
                                               "session_webhook_expires": future}), "")
        self.assertEqual(dt_messages._webhook({"session_webhook": "https://oapi.dingtalk.com/robot/x",
                                               "session_webhook_expires": int(time.time() * 1000) - 1}), "")


class CardRenderTest(unittest.TestCase):
    card = BusinessCard("需要你确认：提交报销", [CardField("金额", "300")],
                        [ConfirmAction(TOKEN), RejectAction(TOKEN), OpenUrlAction("https://example.test/confirmations")])

    def test_buttons_carry_only_action_and_token(self):
        feishu = FeishuCardRenderer.render(self.card)
        buttons = [e for e in feishu["elements"] if e["tag"] == "action"][0]["actions"]
        self.assertEqual([b.get("value") for b in buttons[:2]],
                         [{"action": "confirm", "token": TOKEN}, {"action": "reject", "token": TOKEN}])
        self.assertEqual(buttons[2]["url"], "https://example.test/confirmations")
        dingtalk = DingTalkCardRenderer.render(self.card)
        self.assertEqual(json.loads(dingtalk["confirm_params"]), {"action": "confirm", "token": TOKEN})
        web = WebCardRenderer.render(self.card)
        self.assertEqual([b["type"] for b in web["buttons"]], ["confirm", "reject", "open_url"])

    def test_markdown_fallback_points_to_web(self):
        _, text = DingTalkCardRenderer.markdown(self.card)
        self.assertIn("网页工作台", text)
        self.assertNotIn(TOKEN, text)

    def test_button_value_validation(self):
        for bad in ({"action": "approve", "token": TOKEN}, {"action": "confirm", "token": "x"}, "confirm", None):
            with self.assertRaises(VerificationError):
                base.parse_button_value(bad)


# ------------------------------------------------------------------ 走真实路由和数据库

@unittest.skipUnless(_AVAILABLE, _WHY)
class IntegrationRouteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from sqlalchemy import text
        from models.async_db import AsyncSessionLocal
        from models.init_db import SessionLocal
        from service import organization_admin_service
        from service.integrations import apps
        from tests._async_helpers import run_async

        cls.db = SessionLocal()
        existing = cls.db.execute(text("SELECT COUNT(*) FROM collaboration_app")).scalar()
        if existing:
            cls.db.close()
            raise unittest.SkipTest("库里已经有真实的飞书 / 钉钉配置，不在这个库上跑（不覆盖真实配置）")
        cls.client = rc.make_client()
        cls.admin = rc.create_user("integ-admin")
        cls.alice = rc.create_user("integ-alice")
        cls.bob = rc.create_user("integ-bob")
        cls.carol = rc.create_user("integ-carol")

        async def enroll():
            async with AsyncSessionLocal() as db:
                for user in (cls.alice, cls.bob, cls.carol):
                    await organization_admin_service.add_org_member(db, cls.admin["id"], user["id"])
        run_async(enroll())

        cls.feishu = fake_app("feishu")
        cls.dingtalk = fake_app("dingtalk")
        for app in (cls.feishu, cls.dingtalk):
            apps.save_sync(cls.db, app.provider, cls.admin["id"], app_id=app.app_id, app_secret=app.app_secret,
                           verification_token=app.verification_token, encrypt_key=app.encrypt_key,
                           robot_code=app.robot_code, card_template_id="", enabled=True)
        org = apps.enterprise_id_sync(cls.db)
        from service.integrations import identity
        identity.bind_sync(cls.db, cls.admin["id"], org, "feishu", cls.feishu.app_id, "ou_alice", cls.alice["id"])
        identity.bind_sync(cls.db, cls.admin["id"], org, "feishu", cls.feishu.app_id, "ou_carol", cls.carol["id"])
        identity.bind_sync(cls.db, cls.admin["id"], org, "dingtalk", cls.dingtalk.app_id, "staff_alice", cls.alice["id"])

    @classmethod
    def tearDownClass(cls):
        from sqlalchemy import text
        tenants = {"a": cls.feishu.app_id, "b": cls.dingtalk.app_id}
        cls.db.execute(text("DELETE FROM external_event_inbox WHERE tenant_id IN (:a, :b)"), tenants)
        cls.db.execute(text("DELETE FROM external_user_binding WHERE external_tenant_id IN (:a, :b)"), tenants)
        cls.db.execute(text("DELETE FROM external_department_binding WHERE external_tenant_id IN (:a, :b)"), tenants)
        cls.db.execute(text("DELETE FROM collaboration_app WHERE app_id IN (:a, :b)"), tenants)
        cls.db.commit()
        cls.db.close()
        rc.cleanup()

    def setUp(self):
        from service.integrations import dispatcher
        dispatcher._conversations.invalidate(prefix=("conv",))
        self.sent = []
        self.cards = []
        patches = [
            patch("service.integrations.feishu.messages.send_text", side_effect=lambda app, ctx, text: self.sent.append((ctx, text))),
            patch("service.integrations.feishu.messages.send_card", side_effect=lambda app, ctx, card: self.cards.append((ctx, card))),
            patch("service.integrations.dingtalk.messages.send_text", side_effect=lambda app, ctx, text: self.sent.append((ctx, text))),
            patch("service.integrations.dispatcher.choose_agent", new=AsyncMock(return_value=(101, "中央助手"))),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.run_turn = AsyncMock(side_effect=self._answer)
        p = patch("service.chat_pipeline.run_turn", new=self.run_turn)
        p.start()
        self.addCleanup(p.stop)

    async def _answer(self, db, user, agent_id, message, conversation_id=None):
        return {"answer": f"收到：{message}", "conversation_id": conversation_id or 9001}, SimpleNamespace(public=lambda: None)

    def fresh(self):
        """类里共用的会话：先结束旧事务、丢掉缓存的对象，才能读到请求刚写进去的数据。"""
        self.db.commit()
        self.db.expire_all()
        return self.db

    def post_feishu(self, payload, path="events", **kw):
        headers, body = feishu_request(self.feishu, payload, **kw)
        return self.client.post(f"/integrations/feishu/{path}", content=body,
                                headers={**headers, "Content-Type": "application/json"})

    # -------- 回调校验

    def test_bad_signature_and_expired_are_401(self):
        r = self.post_feishu(feishu_message(self.feishu, "ou_alice"), bad_signature=True)
        self.assertEqual(r.status_code, 401)
        r = self.post_feishu(feishu_message(self.feishu, "ou_alice"), ts=int(time.time()) - 3600)
        self.assertEqual(r.status_code, 401)
        self.run_turn.assert_not_called()

    def test_disabled_provider_is_404(self):
        r = self.client.post("/integrations/wechat/events", content=b"{}")
        self.assertEqual(r.status_code, 404)

    # -------- 消息

    def test_bound_user_message_reaches_agent_and_reply_is_sent(self):
        r = self.post_feishu(feishu_message(self.feishu, "ou_alice", "帮我查一下请假余额"))
        self.assertEqual(r.status_code, 200)
        self.run_turn.assert_awaited_once()
        args = self.run_turn.await_args.args
        self.assertEqual((args[1].id, args[2], args[3]), (self.alice["id"], 101, "帮我查一下请假余额"))
        self.assertEqual(self.sent[-1][1], "收到：帮我查一下请假余额")

    def test_duplicate_event_is_processed_once(self):
        event_id = uuid.uuid4().hex
        for _ in range(2):                                   # 平台重推：同一个 event_id，新的 nonce / 时间戳
            r = self.post_feishu(feishu_message(self.feishu, "ou_alice", "报销 300 元", event_id=event_id))
            self.assertEqual(r.status_code, 200)
        self.assertEqual(self.run_turn.await_count, 1)
        from sqlalchemy import text
        status = self.fresh().execute(text("SELECT status FROM external_event_inbox WHERE tenant_id=:t AND event_id=:e"),
                                      {"t": self.feishu.app_id, "e": event_id}).scalar()
        self.assertEqual(status, "done")

    def test_unbound_user_cannot_use_agent(self):
        r = self.post_feishu(feishu_message(self.feishu, "ou_stranger", "帮我审批"))
        self.assertEqual(r.status_code, 200)
        self.run_turn.assert_not_called()
        self.assertIn("还没有绑定平台账号", self.sent[-1][1])

    def test_disabled_account_cannot_use_agent(self):
        from sqlalchemy import text
        self.db.execute(text("UPDATE `user` SET is_disabled=1 WHERE id=:u"), {"u": self.carol["id"]})
        self.db.commit()
        try:
            self.post_feishu(feishu_message(self.feishu, "ou_carol", "你好"))
        finally:
            self.db.execute(text("UPDATE `user` SET is_disabled=0 WHERE id=:u"), {"u": self.carol["id"]})
            self.db.commit()
        self.run_turn.assert_not_called()
        self.assertIn("已停用", self.sent[-1][1])

    def test_department_change_takes_effect_on_next_message(self):
        from service.integrations import dispatcher
        self.post_feishu(feishu_message(self.feishu, "ou_alice", "第一句"))
        self.assertEqual(self.run_turn.await_args.args[2:], (101, "第一句", None))
        self.post_feishu(feishu_message(self.feishu, "ou_alice", "第二句"))
        self.assertEqual(self.run_turn.await_args.args[2:], (101, "第二句", 9001))      # 同一个助手：接着原会话
        dispatcher.choose_agent.return_value = (202, "财务助手")                         # 调岗后可用的助手变了
        self.post_feishu(feishu_message(self.feishu, "ou_alice", "第三句"))
        self.assertEqual(self.run_turn.await_args.args[2:], (202, "第三句", None))       # 按新部门重新开始

    def test_stale_conversation_restarts_when_agent_no_longer_usable(self):
        calls = []

        async def flaky(db, user, agent_id, message, conversation_id=None):
            calls.append(conversation_id)
            if conversation_id is not None:
                raise ValueError("智能体不存在或无权使用")
            return {"answer": "ok", "conversation_id": 9100}, None
        self.run_turn.side_effect = flaky
        self.post_feishu(feishu_message(self.feishu, "ou_alice", "一"))
        self.post_feishu(feishu_message(self.feishu, "ou_alice", "二"))
        self.assertEqual(calls, [None, 9100, None])
        self.assertEqual(self.sent[-1][1], "ok")

    def test_dingtalk_robot_message(self):
        payload = {"msgId": uuid.uuid4().hex, "msgtype": "text", "text": {"content": "查报销"}, "senderStaffId": "staff_alice",
                   "conversationType": "1", "robotCode": "robot"}
        headers, body = dingtalk_robot_request(self.dingtalk, payload)
        r = self.client.post("/integrations/dingtalk/events", content=body, headers={**headers, "Content-Type": "application/json"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.run_turn.await_args.args[1].id, self.alice["id"])

    def test_leave_org_event_disables_binding(self):
        from sqlalchemy import text
        from service.integrations import identity
        org = self.db.execute(text("SELECT MIN(id) FROM organizations")).scalar()
        identity.bind_sync(self.db, self.admin["id"], org, "dingtalk", self.dingtalk.app_id, "staff_bob", self.bob["id"])
        query, body = dingtalk_event_request(self.dingtalk, {"EventType": "user_leave_org", "UserId": ["staff_bob"]})
        r = self.client.post("/integrations/dingtalk/events", params=query, content=body,
                             headers={"Content-Type": "application/json"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("encrypt", r.json())
        self.assertEqual(identity.resolve_sync(self.fresh(), "dingtalk", self.dingtalk.app_id, "staff_bob"), (None, "disabled"))

    # -------- 卡片

    def _confirmation(self, user_id, *, expired=False):
        from models.init_db import ToolConfirmation
        from utils.timeutil import utcnow
        token = uuid.uuid4().hex
        self.db.add(ToolConfirmation(token=token, user_id=user_id, agent_id=None, tool_name="no_such_tool",
                                     tool_args=json.dumps({"amount": 300}), status="pending",
                                     expires_at=utcnow() + timedelta(minutes=-1 if expired else 10)))
        self.db.commit()
        return token

    def _status(self, token):
        from sqlalchemy import text
        return self.fresh().execute(text("SELECT status FROM tool_confirmation WHERE token=:t"), {"t": token}).scalar()

    def test_card_reject_by_owner(self):
        token = self._confirmation(self.alice["id"])
        r = self.post_feishu(feishu_card(self.feishu, "ou_alice", {"action": "reject", "token": token}))
        self.assertEqual(r.status_code, 200)
        self.assertIn("toast", r.json())
        self.assertEqual(self._status(token), "rejected")
        self.assertIn("已取消", self.sent[-1][1])

    def test_expired_card_token(self):
        token = self._confirmation(self.alice["id"], expired=True)
        self.post_feishu(feishu_card(self.feishu, "ou_alice", {"action": "confirm", "token": token}))
        self.assertEqual(self._status(token), "expired")
        self.assertIn("过期", self.sent[-1][1])

    def test_card_token_of_another_user_cannot_be_used(self):
        token = self._confirmation(self.bob["id"])                 # 是 Bob 的确认单
        self.post_feishu(feishu_card(self.feishu, "ou_alice", {"action": "confirm", "token": token}))
        self.assertEqual(self._status(token), "pending")             # Alice 点了也不会执行
        self.assertIn("不属于", self.sent[-1][1])

    def test_pending_confirmation_is_sent_as_card_privately(self):
        async def with_confirmation(db, user, agent_id, message, conversation_id=None):
            self._confirmation(self.alice["id"])
            return {"answer": "需要你确认", "conversation_id": 9200}, None
        self.run_turn.side_effect = with_confirmation
        self.post_feishu(feishu_message(self.feishu, "ou_alice", "@_user_1 提交报销", chat_type="group",
                                        mentions=[{"key": "@_user_1", "id": {"open_id": "ou_bot"}}]))
        self.assertEqual(len(self.cards), 1)
        context, card = self.cards[0]
        self.assertEqual(context, {"open_id": "ou_alice"})           # 群里问的，确认卡片私发给本人
        self.assertEqual([type(a).__name__ for a in card.actions[:2]], ["ConfirmAction", "RejectAction"])

    # -------- 管理接口

    def test_admin_endpoints(self):
        with rc.admin_env(self.admin["name"]):
            headers = rc.auth_headers(self.admin["id"])
            listing = self.client.get("/admin/integrations", headers=headers).json()
            feishu = next(i for i in listing if i["provider"] == "feishu")
            self.assertEqual(feishu["app_secret"], "…et-x")                       # 只露末四位
            self.assertNotIn(self.feishu.app_secret, json.dumps(listing))
            self.assertNotIn(self.feishu.encrypt_key, json.dumps(listing))
            health = self.client.get("/admin/integrations/feishu/health", headers=headers).json()
            self.assertEqual(health["callback_paths"]["events"], "/integrations/feishu/events")
            r = self.client.put("/admin/integrations/feishu/bindings", headers=headers,
                                json={"external_user_id": "ou_bob", "local_user_id": self.bob["id"]})
            self.assertEqual(r.status_code, 200, r.text)
            bindings = self.client.get("/admin/integrations/feishu/bindings", headers=headers).json()
            self.assertIn("ou_bob", [b["external_user_id"] for b in bindings])
            with patch("service.integrations.feishu.auth.fetch", return_value=("t-123", 7200)):
                self.assertTrue(self.client.post("/admin/integrations/feishu/test", headers=headers).json()["ok"])
        self.assertEqual(self.client.get("/admin/integrations", headers=rc.auth_headers(self.bob["id"])).status_code, 403)

    def test_organization_sync_matches_by_phone_and_disables_missing(self):
        from sqlalchemy import text
        from service.integrations import dispatcher, identity
        phone = "19" + str(uuid.uuid4().int)[:9]
        self.db.execute(text("UPDATE `user` SET phone=:p WHERE id=:u"), {"p": phone, "u": self.bob["id"]})
        self.db.commit()
        org_data = {"tenant_id": self.feishu.app_id, "departments": [{"id": "od1", "name": "不存在的部门", "parent_id": "0"}],
                    "users": [{"user_id": "ou_bob2", "name": "Bob", "mobile": f"+86{phone}", "active": True},
                              {"user_id": "ou_nobody", "name": "无名", "mobile": "13000000000", "active": True}]}
        with patch("service.integrations.feishu.contacts.fetch_organization", return_value=org_data):
            summary = dispatcher.sync_organization_sync(self.db, "feishu", self.admin["id"])
        self.assertEqual(summary["users_matched"], 1)
        self.assertIn("无名", summary["users_unmatched"])
        self.assertEqual(identity.resolve_sync(self.fresh(), "feishu", self.feishu.app_id, "ou_bob2"), (self.bob["id"], None))
        # 之前绑定过、这次通讯录里没有的人（ou_alice 等）被停用
        self.assertEqual(identity.resolve_sync(self.db, "feishu", self.feishu.app_id, "ou_alice"), (None, "disabled"))
        # 还原，别影响其他用例
        self.db.execute(text("UPDATE external_user_binding SET status='active' WHERE external_tenant_id=:t "
                             "AND external_user_id IN ('ou_alice', 'ou_carol')"), {"t": self.feishu.app_id})
        self.db.commit()


if __name__ == "__main__":
    unittest.main()
