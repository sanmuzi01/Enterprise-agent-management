"""员工要求时读取飞书群聊记录（read_feishu_group_chat）和按部门的人员绑定对应表。

读群聊的规矩：只读机器人在的群；提问人本人必须在群里；在 A 群里提问不能读 B 群（回答会发在 A 群）；
私聊里可以按群名读自己在的任何群；每次读取写审计；不绑定飞书不能用。飞书接口用 mock。
"""
import json
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import text

from service.integrations import chat_context
from service.integrations.feishu import client
from service.tools import feishu_chat
from tests import _route_client as rc
from tests.test_integrations import fake_app

_AVAILABLE, _WHY = rc.route_tests_available()
APP = SimpleNamespace(app_id="cli_read", app_secret="s")
CHATS = [{"chat_id": "oc_hx", "name": "华星项目群"}, {"chat_id": "oc_hx2", "name": "华星售后群"},
         {"chat_id": "oc_ly", "name": "远航物流对接群"}]
MEMBERS = {"oc_hx": {"ou_me", "ou_li"}, "oc_hx2": {"ou_me"}, "oc_ly": {"ou_li"}}
NAMES = {"ou_me": "孙销售", "ou_li": "李经理"}


def history_items():
    return [
        {"message_id": "m3", "msg_type": "text", "create_time": "1760090600000", "sender": {"id": "ou_cust", "sender_type": "user"},
         "body": {"content": json.dumps({"text": "那就下周三签合同"})}},
        {"message_id": "m2", "msg_type": "text", "create_time": "1760090500000", "deleted": True,
         "sender": {"id": "ou_me", "sender_type": "user"}, "body": {"content": json.dumps({"text": "撤回的话"})}},
        {"message_id": "m1", "msg_type": "text", "create_time": "1760090400000", "sender": {"id": "ou_me", "sender_type": "user"},
         "body": {"content": json.dumps({"text": "王总，报价单已更新"})}},
        {"message_id": "m0", "msg_type": "system", "create_time": "1760090300000", "sender": {"id": "", "sender_type": "user"},
         "body": {"content": "{}"}},
    ]


class ReadGroupChatTest(unittest.TestCase):
    def setUp(self):
        self.audits = []
        self.history_calls = []
        chat_context.forget(7)

        def history(app, chat_id, start, end, limit):
            self.history_calls.append((chat_id, end - start, limit))
            from service.integrations.base import ChatLine
            from datetime import datetime
            return [ChatLine("ou_me", "孙销售", f"{chat_id} 里的一句话", datetime(2026, 10, 10, 2, 0))]
        patches = [
            patch("service.integrations.apps.load_enabled_sync", return_value=APP),
            patch.object(feishu_chat, "_binding", side_effect=lambda db, p, t, uid: "ou_me" if uid == 7 else None),
            patch.object(client, "bot_chats", return_value=CHATS),
            patch.object(client, "chat_has_member", side_effect=lambda app, chat_id, open_id: open_id in MEMBERS.get(chat_id, set())),
            patch.object(client, "chat_info", side_effect=lambda app, chat_id: {"name": next(c["name"] for c in CHATS if c["chat_id"] == chat_id)}),
            patch.object(client, "chat_history", side_effect=history),
            patch("service.audit_service.record", side_effect=lambda *a, **k: self.audits.append((a, k))),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def read(self, user_id=7, **kwargs):
        return feishu_chat.read_group_chat(user_id, **kwargs)

    def test_private_chat_reads_a_group_by_name_and_audits(self):
        result = self.read(chat_name="华星项目群", hours=48)
        self.assertEqual((result["chat_name"], result["count"]), ("华星项目群", 1))
        self.assertIn("oc_hx 里的一句话", result["transcript"])
        self.assertEqual(self.history_calls, [("oc_hx", 48 * 3600, 200)])
        (args, kwargs), = self.audits
        self.assertEqual((args[0], args[1]), (7, "integration.chat_read"))
        self.assertEqual((kwargs["detail"]["chat_name"], kwargs["detail"]["messages"]), ("华星项目群", 1))

    def test_requester_must_be_in_the_group(self):
        result = self.read(chat_name="远航物流")
        self.assertIn("你不在「远航物流对接群」这个群里", result["error"])
        self.assertEqual((self.history_calls, self.audits), ([], []))

    def test_bot_must_be_in_the_group_and_names_must_be_unique(self):
        self.assertIn("机器人不在", self.read(chat_name="蓝海")["error"])
        result = self.read(chat_name="华星")
        self.assertEqual(result["candidates"], ["华星项目群", "华星售后群"])
        self.assertEqual(self.history_calls, [])

    def test_in_a_group_only_the_current_group_can_be_read(self):
        chat_context.remember(7, "feishu", "oc_hx", "group")
        self.assertEqual(self.read()["chat_name"], "华星项目群")                     # 不说群名：读当前群
        result = self.read(chat_name="华星售后群")
        self.assertIn("在群里只能读当前这个群", result["error"])                       # 回答会发在当前群，不能带出别的群
        self.assertEqual(len(self.history_calls), 1)

    def test_needs_a_group_and_a_binding(self):
        self.assertIn("请告诉我要读哪个群", self.read()["error"])                      # 私聊里没说群名
        chat_context.remember(7, "feishu", "oc_p2p", "p2p")
        self.assertIn("请告诉我要读哪个群", self.read()["error"])
        self.assertIn("还没有绑定飞书", self.read(user_id=8, chat_name="华星项目群")["error"])

    def test_limits(self):
        self.read(chat_name="华星项目群", hours=10000, limit=99999)
        self.assertEqual(self.history_calls, [("oc_hx", feishu_chat.MAX_HOURS * 3600, feishu_chat.MAX_MESSAGES)])

    def test_tool_wrapper(self):
        tool = feishu_chat.ReadFeishuGroupChatTool()
        tool.set_context(SimpleNamespace(user_id=7, system_prompt=None))
        self.assertEqual(json.loads(tool.execute(chat_name="华星项目群"))["chat_name"], "华星项目群")


class ChatHistoryParsingTest(unittest.TestCase):
    def test_history_is_ordered_and_named(self):
        calls = []

        def call(app, method, path, **kw):
            calls.append((path, kw["params"]))
            return {"items": history_items(), "has_more": False}
        with patch.object(client, "call", side_effect=call), \
                patch.object(client, "user_name", side_effect=lambda app, oid: NAMES.get(oid, "")):
            lines = client.chat_history(APP, "oc_hx", 100, 200, 50)
        self.assertEqual(calls[0][0], "/open-apis/im/v1/messages")
        self.assertEqual((calls[0][1]["container_id"], calls[0][1]["start_time"], calls[0][1]["end_time"]), ("oc_hx", "100", "200"))
        self.assertEqual([(l.sender_name, l.text) for l in lines], [("孙销售", "王总，报价单已更新"), ("外部成员1", "那就下周三签合同")])

    def test_membership_pages(self):
        pages = [{"items": [{"member_id": "ou_a"}], "has_more": True, "page_token": "p2"}, {"items": [{"member_id": "ou_me"}], "has_more": False}]
        with patch.object(client, "call", side_effect=pages):
            self.assertTrue(client.chat_has_member(APP, "oc_hx", "ou_me"))


@unittest.skipUnless(_AVAILABLE, _WHY)
class BindingDirectoryTest(unittest.TestCase):
    """后台“人员绑定”：按部门列出每个员工对应的飞书账号，没绑的和没对应到员工的外部账号也列出来。"""

    @classmethod
    def setUpClass(cls):
        from models.init_db import SessionLocal
        from service.integrations import apps, identity
        from tests.test_enterprise_access import _add_org_member, _add_team_member, _create_team
        cls.db = SessionLocal()
        if cls.db.execute(text("SELECT COUNT(*) FROM collaboration_app")).scalar():
            cls.db.close()
            raise unittest.SkipTest("库里已经有真实的飞书 / 钉钉配置，不在这个库上跑")
        cls.client = rc.make_client()
        cls.admin = rc.create_user("dir-admin")
        cls.db.execute(text("INSERT INTO user_role (user_id, role_id) SELECT :u, id FROM role WHERE role_name='admin'"), {"u": cls.admin["id"]})
        cls.db.commit()
        cls.a, cls.b, cls.c = rc.create_user("dir-a"), rc.create_user("dir-b"), rc.create_user("dir-c")
        cls.app = fake_app("feishu")
        apps.save_sync(cls.db, "feishu", cls.admin["id"], app_id=cls.app.app_id, app_secret="s", verification_token="v",
                       encrypt_key=cls.app.encrypt_key, robot_code=None, card_template_id=None, enabled=True)
        cls.org = apps.enterprise_id_sync(cls.db)
        for u in (cls.a, cls.b, cls.c):
            _add_org_member(cls.db, cls.org, u["id"], "member")
        cls.team = _create_team(cls.db, cls.org, "dir-team-" + uuid.uuid4().hex[:6], cls.a["id"])
        _add_team_member(cls.db, cls.team, cls.a["id"], "admin")
        _add_team_member(cls.db, cls.team, cls.b["id"], "member")
        identity.bind_sync(cls.db, cls.admin["id"], cls.org, "feishu", cls.app.app_id, "ou_dir_a", cls.a["id"], external_name="阿甲")
        identity.bind_sync(cls.db, cls.admin["id"], cls.org, "feishu", cls.app.app_id, "ou_dir_x", None, external_name="外部的某人")

    @classmethod
    def tearDownClass(cls):
        cls.db.rollback()
        cls.db.execute(text("DELETE FROM external_user_binding WHERE external_tenant_id=:t"), {"t": cls.app.app_id})
        cls.db.execute(text("DELETE FROM collaboration_app WHERE app_id=:t"), {"t": cls.app.app_id})
        cls.db.commit()
        rc.cleanup()
        cls.db.execute(text("DELETE FROM team_members WHERE team_id=:t"), {"t": cls.team})
        cls.db.execute(text("DELETE FROM teams WHERE id=:t"), {"t": cls.team})
        cls.db.commit()
        cls.db.close()

    def directory(self):
        r = self.client.get("/admin/integrations/feishu/directory", headers=self.admin["headers"])
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def member(self, data, user):
        return next(m for d in data["departments"] for m in d["members"] if m["user_id"] == user["id"])

    def test_grouped_by_department_one_account_each(self):
        data = self.directory()
        dept = next(d for d in data["departments"] if d["team_id"] == self.team)
        self.assertEqual({m["user_id"] for m in dept["members"]}, {self.a["id"], self.b["id"]})
        self.assertEqual((dept["bound"], dept["total"]), (1, 2))
        self.assertEqual(self.member(data, self.a)["binding"]["external_name"], "阿甲")
        self.assertIsNone(self.member(data, self.b)["binding"])
        unassigned = next(d for d in data["departments"] if d["team_id"] is None)
        self.assertIn(self.c["id"], {m["user_id"] for m in unassigned["members"]})         # 没有部门的员工也列出来
        self.assertIn("ou_dir_x", {u["external_user_id"] for u in data["unlinked"]})        # 没对应到员工的飞书账号

    def test_assign_unlinked_account_then_unbind(self):
        r = self.client.put("/admin/integrations/feishu/bindings", headers=self.admin["headers"],
                            json={"external_user_id": "ou_dir_x", "local_user_id": self.b["id"]})
        self.assertEqual(r.status_code, 200, r.text)
        data = self.directory()
        self.assertEqual(self.member(data, self.b)["binding"]["external_user_id"], "ou_dir_x")
        self.assertNotIn("ou_dir_x", {u["external_user_id"] for u in data["unlinked"]})
        r = self.client.put("/admin/integrations/feishu/bindings", headers=self.admin["headers"],
                            json={"external_user_id": "ou_dir_a", "local_user_id": self.b["id"]})
        self.assertEqual(r.status_code, 400)                                                  # 一人只能绑一个飞书账号
        self.client.put("/admin/integrations/feishu/bindings", headers=self.admin["headers"],
                        json={"external_user_id": "ou_dir_x", "local_user_id": None})
        self.assertIsNone(self.member(self.directory(), self.b)["binding"])

    def test_admin_only(self):
        r = self.client.get("/admin/integrations/feishu/directory", headers=self.a["headers"])
        self.assertEqual(r.status_code, 403)


if __name__ == "__main__":
    unittest.main()
