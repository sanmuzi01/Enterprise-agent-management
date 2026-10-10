"""飞书聊天记录进 CRM：合并转发的聊天记录、按群开启的“群消息记录到 CRM”。

要验证的规矩：
- 员工合并转发给机器人的聊天记录整段存成一条客户活动（每条带发送人、时间），同一次转发只存一次；不是销售不能存；
- 没开启记录的群，群里没 @ 机器人的消息一律不保存；
- 只有销售部门负责人或群主能开启；带客户名开启时，客户名必须对上唯一客户（模糊的不开启）；
- 开启后的消息先暂存，对话停下 30 分钟后整理成一条群聊活动并删掉暂存；撤回的消息不进 CRM；
- 关闭时把已收的整理完，之后的消息不再保存；网页上能看到、能关闭（只有开启人或负责人）。

不连真实飞书：开放接口（取消息、群信息、姓名）和发消息都用 mock；回调按飞书的算法签名加密后从 HTTP 入口打进来。
"""
import json
import time
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import text

from service.crm import group_capture
from service.integrations.base import InboundMessage, MessageRecalled
from service.integrations.feishu import callback as fs_callback
from service.integrations.feishu import content
from tests import _route_client as rc
from tests._async_helpers import run_async
from tests.test_integrations import fake_app, feishu_request

_AVAILABLE, _WHY = rc.route_tests_available()

CUSTOMERS = [{"id": 501, "name": "华星科技有限公司"}, {"id": 502, "name": "蓝海物流"}]
CONTACTS = [{"id": 1, "customerId": 501, "name": "王总", "title": "采购总监", "phone": "13800001111", "email": "wang@huaxing.com"}]
NAMES = {"ou_leader": "林经理", "ou_member": "孙销售", "ou_owner": "群主老周"}     # 本企业员工；其他 open_id 是外部联系人


def ms(seconds_ago: float = 0) -> str:
    return str(int((time.time() - seconds_ago) * 1000))


def feishu_event(app, open_id, *, msg_type="text", body=None, chat_type="p2p", mentions=None, message_id=None,
                 create_time=None, event_id=None):
    message = {"message_id": message_id or f"om_{uuid.uuid4().hex[:10]}", "chat_id": "oc_huaxing", "chat_type": chat_type,
               "message_type": msg_type, "content": json.dumps(body if body is not None else {"text": "你好"}, ensure_ascii=False),
               "create_time": create_time or ms()}
    if mentions:
        message["mentions"] = mentions
    return {"schema": "2.0", "header": {"event_id": event_id or uuid.uuid4().hex, "token": app.verification_token,
                                        "event_type": "im.message.receive_v1", "app_id": app.app_id},
            "event": {"sender": {"sender_type": "user", "sender_id": {"open_id": open_id}}, "message": message}}


AT_BOT = [{"key": "@_user_1", "id": {"open_id": "ou_bot"}, "name": "CRM 助手"}]


# ------------------------------------------------------------------ 不需要数据库的部分

class FeishuContentTest(unittest.TestCase):
    def test_text_mentions(self):
        raw = json.dumps({"text": "@_user_1 王总说下周给答复 @_user_2"})
        mentions = [{"key": "@_user_1", "name": "机器人"}, {"key": "@_user_2", "name": "林经理"}]
        self.assertEqual(content.render("text", raw, mentions, strip_mentions=True), "王总说下周给答复")
        self.assertEqual(content.render("text", raw, mentions, strip_mentions=False), "@机器人 王总说下周给答复 @林经理")

    def test_rich_text_and_placeholders(self):
        post = {"zh_cn": {"title": "报价确认", "content": [[{"tag": "text", "text": "单价 "}, {"tag": "at", "user_name": "王总"},
                                                            {"tag": "text", "text": " 请看"}], [{"tag": "img", "image_key": "x"}]]}}
        self.assertEqual(content.render("post", json.dumps(post), strip_mentions=False), "报价确认\n单价 @王总 请看\n[图片]")
        self.assertEqual(content.render("post", json.dumps(post), strip_mentions=True), "报价确认\n单价  请看")
        self.assertEqual(content.render("file", json.dumps({"file_name": "报价单.pdf"}), strip_mentions=False), "[文件：报价单.pdf]")
        self.assertEqual(content.render("image", "{}", strip_mentions=False), "[图片]")
        self.assertEqual(content.render("image", "{}", strip_mentions=True), "")          # 助手只处理文字

    def test_commands(self):
        self.assertEqual(group_capture.parse_command("开启CRM记录"), ("enable", ""))
        self.assertEqual(group_capture.parse_command("开启 crm 记录：华星科技"), ("enable", "华星科技"))
        self.assertEqual(group_capture.parse_command("关闭CRM记录"), ("disable", ""))
        self.assertEqual(group_capture.parse_command("CRM记录状态"), ("status", ""))
        self.assertIsNone(group_capture.parse_command("帮我开启一下报价流程"))
        self.assertIsNone(group_capture.parse_command("保存到CRM：开启CRM记录"))

    def test_recall_event(self):
        app = fake_app("feishu")
        payload = {"schema": "2.0", "header": {"event_id": "e1", "token": app.verification_token,
                                               "event_type": "im.message.recalled_v1", "app_id": app.app_id},
                   "event": {"message_id": "om_x", "chat_id": "oc_1"}}
        headers, body = feishu_request(app, payload)
        parsed = fs_callback.parse(app, headers, body)
        self.assertIsInstance(parsed, MessageRecalled)
        self.assertEqual(parsed.message_id, "om_x")

    def test_forwarded_lines(self):
        from service.integrations.feishu import client
        parent = "om_fwd"
        items = [
            {"message_id": parent, "msg_type": "merge_forward", "create_time": "1760000000000", "body": {"content": "{}"}},
            {"message_id": "s3", "upper_message_id": parent, "msg_type": "image", "create_time": "1760000300000",
             "sender": {"id": "ou_cust", "id_type": "open_id", "sender_type": "user"}, "body": {"content": "{}"}},
            {"message_id": "s1", "upper_message_id": parent, "msg_type": "text", "create_time": "1760000100000",
             "sender": {"id": "ou_member", "id_type": "open_id", "sender_type": "user"},
             "body": {"content": json.dumps({"text": "王总，报价发您了"})}},
            {"message_id": "s2", "upper_message_id": parent, "msg_type": "text", "create_time": "1760000200000",
             "sender": {"id": "ou_cust", "id_type": "open_id", "sender_type": "user"},
             "body": {"content": json.dumps({"text": "收到，下周三前答复"})}},
            {"message_id": "s4", "upper_message_id": parent, "msg_type": "text", "create_time": "1760000400000", "deleted": True,
             "sender": {"id": "ou_cust", "id_type": "open_id", "sender_type": "user"}, "body": {"content": json.dumps({"text": "撤回了"})}},
            {"message_id": "n1", "upper_message_id": "s9", "msg_type": "text", "create_time": "1760000500000",
             "sender": {"id": "ou_cust", "id_type": "open_id", "sender_type": "user"}, "body": {"content": json.dumps({"text": "嵌套的"})}},
            {"message_id": "s5", "upper_message_id": parent, "msg_type": "text", "create_time": "1760000600000",
             "sender": {"id": "cli_bot", "id_type": "app_id", "sender_type": "app"}, "body": {"content": json.dumps({"text": "提醒"})}},
        ]
        with patch.object(client, "call", return_value={"items": items}) as call, \
                patch.object(client, "user_name", side_effect=lambda app, oid: NAMES.get(oid, "")):
            lines = client.forwarded_lines(SimpleNamespace(app_id="cli_x"), parent)
        self.assertEqual(call.call_args.args[2], f"/open-apis/im/v1/messages/{parent}")
        self.assertEqual([(l.sender_name, l.text) for l in lines],
                         [("孙销售", "王总，报价发您了"), ("外部成员1", "收到，下周三前答复"), ("外部成员1", "[图片]"), ("机器人", "提醒")])

    def test_platform_without_group_messages_cannot_enable(self):
        from service.integrations.dingtalk.adapter import DingTalkAdapter
        msg = InboundMessage("dingtalk", "t", "e", "staff_1", "开启CRM记录", chat_id="cid", chat_type="group")
        reply = run_async(group_capture.handle_command(None, DingTalkAdapter(), None, msg, 1, "enable", "", AsyncMock()))
        self.assertIn("只能收到 @ 它的消息", reply)


# ------------------------------------------------------------------ 从回调入口打进来

@unittest.skipUnless(_AVAILABLE, _WHY)
class ChatCaptureRouteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from models.init_db import SessionLocal
        from service.integrations import apps, identity
        from tests.test_enterprise_access import _add_org_member, _add_team_member, _create_team

        cls.db = SessionLocal()
        if cls.db.execute(text("SELECT COUNT(*) FROM collaboration_app")).scalar():
            cls.db.close()
            raise unittest.SkipTest("库里已经有真实的飞书 / 钉钉配置，不在这个库上跑（不覆盖真实配置）")
        cls.client = rc.make_client()
        cls.admin = rc.create_user("cap-admin")
        cls.leader = rc.create_user("cap-leader")
        cls.member = rc.create_user("cap-member")
        cls.outsider = rc.create_user("cap-outsider")
        cls.feishu = fake_app("feishu")
        apps.save_sync(cls.db, "feishu", cls.admin["id"], app_id=cls.feishu.app_id, app_secret="s", verification_token="vtoken",
                       encrypt_key=cls.feishu.encrypt_key, robot_code=None, card_template_id=None, enabled=True)
        org = apps.enterprise_id_sync(cls.db)
        for user in (cls.leader, cls.member, cls.outsider):
            _add_org_member(cls.db, org, user["id"], "member")
        cls.team = _create_team(cls.db, org, "cap-sales-" + uuid.uuid4().hex[:6], cls.leader["id"])
        cls.db.execute(text("UPDATE teams SET department_code='sales' WHERE id=:t"), {"t": cls.team})
        cls.db.commit()
        _add_team_member(cls.db, cls.team, cls.leader["id"], "admin")
        _add_team_member(cls.db, cls.team, cls.member["id"], "member")
        for open_id, user in (("ou_leader", cls.leader), ("ou_member", cls.member), ("ou_outsider", cls.outsider)):
            identity.bind_sync(cls.db, cls.admin["id"], org, "feishu", cls.feishu.app_id, open_id, user["id"])

    @classmethod
    def tearDownClass(cls):
        cls.cleanup_rows()
        tenant = {"t": cls.feishu.app_id}
        cls.db.execute(text("DELETE FROM external_event_inbox WHERE tenant_id=:t"), tenant)
        cls.db.execute(text("DELETE FROM external_user_binding WHERE external_tenant_id=:t"), tenant)
        cls.db.execute(text("DELETE FROM collaboration_app WHERE app_id=:t"), tenant)
        cls.db.commit()
        rc.cleanup()
        cls.db.execute(text("DELETE FROM team_members WHERE team_id=:t"), {"t": cls.team})
        cls.db.execute(text("DELETE FROM teams WHERE id=:t"), {"t": cls.team})
        cls.db.commit()
        cls.db.close()

    @classmethod
    def cleanup_rows(cls):
        cls.db.commit()
        cls.db.execute(text("DELETE FROM crm_chat_message WHERE group_id IN (SELECT id FROM crm_chat_group WHERE tenant_id=:t)"),
                       {"t": cls.feishu.app_id})
        cls.db.execute(text("DELETE FROM crm_chat_group WHERE tenant_id=:t"), {"t": cls.feishu.app_id})
        cls.db.execute(text("DELETE FROM customer_activity WHERE team_id=:t"), {"t": cls.team})
        cls.db.execute(text("DELETE FROM crm_customer_alias WHERE team_id=:t"), {"t": cls.team})
        cls.db.commit()

    def setUp(self):
        from service.integrations.feishu import client
        self.cleanup_rows()
        self.sent = []
        self.forwarded = {}
        self.chat = {"name": "华星项目群", "owner_id": "ou_owner"}
        self.run_turn = AsyncMock(return_value=({"answer": "助手回答", "conversation_id": 1}, None))
        patches = [
            patch("service.crm.activities.hub_get", new=AsyncMock(side_effect=self._hub)),
            patch("service.integrations.feishu.messages.send_text", side_effect=lambda app, ctx, t: self.sent.append((ctx, t))),
            patch.object(client, "bot_open_id", return_value="ou_bot"),
            patch.object(client, "chat_info", side_effect=lambda app, chat_id: dict(self.chat)),
            patch.object(client, "user_name", side_effect=lambda app, oid: NAMES.get(oid, "")),
            patch.object(client, "call", side_effect=self._feishu_api),
            patch("service.integrations.dispatcher.choose_agent", new=AsyncMock(return_value=(101, "助手"))),
            patch("service.chat_pipeline.run_turn", new=self.run_turn),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    async def _hub(self, user_id, team_id, path, operation):
        return {"/crm/customers": CUSTOMERS, "/crm/contacts": CONTACTS}[path]

    def _feishu_api(self, app, method, path, **kw):
        message_id = path.rsplit("/", 1)[-1]
        if method == "GET" and message_id in self.forwarded:
            return {"items": self.forwarded[message_id]}
        raise AssertionError(f"没有预期的飞书接口调用：{method} {path}")

    # -------- 工具

    def post(self, payload):
        headers, body = feishu_request(self.feishu, payload)
        r = self.client.post("/integrations/feishu/events", content=body, headers={**headers, "Content-Type": "application/json"})
        self.assertEqual(r.status_code, 200, r.text)
        return payload["header"]["event_id"]

    def say_to_bot(self, open_id, words):
        self.post(feishu_event(self.feishu, open_id, body={"text": f"@_user_1 {words}"}, chat_type="group", mentions=AT_BOT))
        return self.sent[-1][1]

    def group_says(self, open_id, words, *, seconds_ago=0, msg_type="text", message_id=None):
        body = {"text": words} if msg_type == "text" else {}
        return self.post(feishu_event(self.feishu, open_id, msg_type=msg_type, body=body, chat_type="group",
                                      create_time=ms(seconds_ago), message_id=message_id))

    def rows(self, sql, **params):
        self.db.commit()
        return self.db.execute(text(sql), {"team": self.team, "tenant": self.feishu.app_id, **params}).all()

    def activities(self):
        return self.rows("SELECT customer_id, match_status, title, content, external_source_id FROM customer_activity "
                         "WHERE team_id=:team ORDER BY id")

    def buffered(self):
        return self.rows("SELECT m.content FROM crm_chat_message m JOIN crm_chat_group g ON g.id=m.group_id "
                         "WHERE g.tenant_id=:tenant ORDER BY m.sent_at")

    def flush(self):
        from models.async_db import AsyncSessionLocal

        async def go():
            async with AsyncSessionLocal() as db:
                return await group_capture.flush_due(db)
        return run_async(go())[0]

    # -------- 合并转发

    def forward(self, open_id, message_id):
        self.forwarded[message_id] = [
            {"message_id": message_id, "msg_type": "merge_forward", "create_time": ms(), "body": {"content": "{}"}},
            {"message_id": "a", "upper_message_id": message_id, "msg_type": "text", "create_time": ms(3600),
             "sender": {"id": "ou_member", "id_type": "open_id", "sender_type": "user"},
             "body": {"content": json.dumps({"text": "王总，华星科技这期的报价发您了"})}},
            {"message_id": "b", "upper_message_id": message_id, "msg_type": "text", "create_time": ms(3000),
             "sender": {"id": "ou_wang", "id_type": "open_id", "sender_type": "user"},
             "body": {"content": json.dumps({"text": "收到，预算下周三前答复"})}},
            {"message_id": "c", "upper_message_id": message_id, "msg_type": "file", "create_time": ms(2900),
             "sender": {"id": "ou_wang", "id_type": "open_id", "sender_type": "user"},
             "body": {"content": json.dumps({"file_name": "采购清单.xlsx"})}},
        ]
        self.post(feishu_event(self.feishu, open_id, msg_type="merge_forward",
                               body={"content": "Merged and Forwarded Message"}, message_id=message_id))
        return self.sent[-1][1]

    def test_forwarded_chat_record_is_saved_once(self):
        reply = self.forward("ou_member", "om_fwd_1")
        self.assertIn("收到 3 条聊天记录", reply)
        self.assertIn("华星科技有限公司", reply)
        [(customer, status, title, body, source)] = self.activities()
        self.assertEqual((customer, status, source), (501, "auto", "feishu:om_fwd_1"))
        self.assertIn("孙销售、外部成员1", title)
        self.assertIn("外部成员1：收到，预算下周三前答复", body)
        self.assertIn("外部成员1：[文件：采购清单.xlsx]", body)
        self.run_turn.assert_not_called()                       # 转发的聊天记录不交给助手
        self.assertIn("已经保存过", self.forward("ou_member", "om_fwd_1"))   # 同一次转发（平台重推 / 再点一次）只存一次
        self.assertEqual(len(self.activities()), 1)

    def test_forward_from_non_sales_user_is_not_saved(self):
        self.assertIn("只有销售部门", self.forward("ou_outsider", "om_fwd_2"))
        self.assertEqual(self.activities(), [])

    # -------- 群消息

    def test_group_messages_are_not_kept_unless_enabled(self):
        event_id = self.group_says("ou_member", "王总说预算砍了一半")
        self.assertEqual(self.buffered(), [])
        self.assertEqual(self.sent, [])                          # 不回复、不交给助手
        self.run_turn.assert_not_called()
        status = self.rows("SELECT status FROM external_event_inbox WHERE tenant_id=:tenant AND event_id=:e", e=event_id)
        self.assertEqual(status[0][0], "ignored")

    def test_who_can_enable(self):
        self.assertIn("只有销售部门的同事", self.say_to_bot("ou_outsider", "开启CRM记录"))
        self.assertIn("只有销售部门负责人或群主", self.say_to_bot("ou_member", "开启CRM记录"))
        self.assertEqual(self.rows("SELECT id FROM crm_chat_group WHERE tenant_id=:tenant"), [])
        self.chat["owner_id"] = "ou_member"                       # 普通销售，但是群主
        self.assertIn("已开启", self.say_to_bot("ou_member", "开启CRM记录"))

    def test_customer_name_must_match_one_customer(self):
        reply = self.say_to_bot("ou_leader", "开启CRM记录 华星科枝")
        self.assertIn("没有开启", reply)
        self.assertIn("华星科技有限公司", reply)                  # 给出候选，但不替员工选
        self.assertEqual(self.rows("SELECT id FROM crm_chat_group WHERE tenant_id=:tenant"), [])

    def test_enabled_group_is_recorded_and_tidied_into_activities(self):
        reply = self.say_to_bot("ou_leader", "开启CRM记录 华星科技")
        self.assertIn("已开启", reply)
        self.assertIn("华星科技有限公司", reply)
        self.assertIn("请各位知悉", reply)                        # 在群里公告
        self.group_says("ou_member", "王总，合同我今天发您", seconds_ago=7200)
        self.group_says("ou_wang", "好的，法务看完回复", seconds_ago=7100)
        self.group_says("ou_wang", "", seconds_ago=7050, msg_type="image")
        self.group_says("ou_wang", "这句发错了", seconds_ago=7000, message_id="om_oops")
        self.post({"schema": "2.0", "header": {"event_id": uuid.uuid4().hex, "token": "vtoken",
                                               "event_type": "im.message.recalled_v1", "app_id": self.feishu.app_id},
                   "event": {"message_id": "om_oops", "chat_id": "oc_huaxing"}})
        self.group_says("ou_member", "那我等您消息", seconds_ago=60)          # 还在进行中的对话
        self.assertEqual(len(self.buffered()), 4)                  # 撤回的那条已经删了
        self.assertIn("已记录 4 条", self.say_to_bot("ou_member", "CRM记录状态"))
        self.run_turn.assert_not_called()

        self.assertEqual(self.flush(), 1)                          # 两小时前那段对话整理成一条活动
        [(customer, status, title, body, source)] = self.activities()
        self.assertEqual((customer, status), (501, "explicit"))
        self.assertTrue(source.startswith("feishu-group:oc_huaxing:"))
        self.assertIn("群聊「华星项目群」", title)
        self.assertIn("（3 条）", title)
        self.assertIn("孙销售：王总，合同我今天发您", body)
        self.assertIn("外部成员：[图片]", body)
        self.assertNotIn("这句发错了", body)
        self.assertEqual([r[0] for r in self.buffered()], ["那我等您消息"])   # 进行中的留着

        self.assertIn("只有开启人、群主或该销售部门的负责人", self.say_to_bot("ou_member", "关闭CRM记录"))
        reply = self.say_to_bot("ou_leader", "关闭CRM记录")
        self.assertIn("已关闭", reply)
        self.assertIn("整理成 1 条", reply)                        # 关闭时把剩下的也整理了
        self.assertEqual(len(self.activities()), 2)
        self.assertEqual(self.buffered(), [])
        self.group_says("ou_member", "关闭之后说的话")
        self.assertEqual(self.buffered(), [])

    def test_web_list_and_close(self):
        self.say_to_bot("ou_leader", "开启CRM记录")
        url = f"/enterprise/crm-copilot/chat-groups?team_id={self.team}"
        body = self.client.get(url, headers=self.leader["headers"]).json()
        self.assertTrue(body["can_manage"])
        [group] = body["items"]
        self.assertEqual((group["chat_name"], group["status"], group["enabled_by"]), ("华星项目群", "active", self.leader["name"]))
        self.assertFalse(self.client.get(url, headers=self.member["headers"]).json()["can_manage"])
        self.assertEqual(self.client.get(url, headers=self.outsider["headers"]).status_code, 403)
        close = f"/enterprise/crm-copilot/chat-groups/{group['id']}/close"
        self.assertEqual(self.client.post(close, headers=self.member["headers"], json={"team_id": self.team}).status_code, 403)
        r = self.client.post(close, headers=self.leader["headers"], json={"team_id": self.team})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["group"]["status"], "closed")
        context, words = self.sent[-1]
        self.assertEqual(context, {"chat_id": "oc_huaxing"})       # 在群里告知已关闭
        self.assertIn("在网页上关闭", words)


if __name__ == "__main__":
    unittest.main()
