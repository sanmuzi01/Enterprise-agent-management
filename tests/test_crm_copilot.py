"""CRM Copilot：客户自动关联、邮件 / 日历解析、活动去重、人工归属记住映射、增量摘要只用有权限的数据、
风险必须带证据、建议不自动写入且不重复生成、聊天“保存到CRM”。

业务系统（Java）用替身：客户、联系人、客户摘要都从 mock 返回；平台自己的表用真实测试库。
"""
import unittest
import uuid
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
from unittest.mock import AsyncMock, patch

from sqlalchemy import text

from service.crm import matching, risk_rules
from service.crm.sources import parse_calendar, parse_email
from tests import _route_client as rc
from tests._async_helpers import run_async

_AVAILABLE, _WHY = rc.route_tests_available()

CUSTOMERS = [{"id": 501, "name": "华星科技有限公司"}, {"id": 502, "name": "蓝海物流"}, {"id": 503, "name": "北辰制造"}]
CONTACTS = [{"id": 1, "customerId": 501, "name": "王总", "title": "采购总监", "phone": "13800001111", "email": "wang@huaxing.com"},
            {"id": 2, "customerId": 502, "name": "李经理", "title": "IT 经理", "phone": "13900002222", "email": "li@lanhai.cn"},
            {"id": 3, "customerId": 503, "name": "赵工", "title": "工程师", "phone": None, "email": "zhao@qq.com"}]


def directory(aliases=()):
    return matching.Directory(CUSTOMERS, CONTACTS, list(aliases))


def make_eml(sender="wang@huaxing.com", to="me@ourcorp.com", subject="关于报价", body="王总您好，附件是报价。",
             message_id=None, attachment=None):
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = sender, to, subject
    msg["Date"] = "Fri, 09 Oct 2026 10:00:00 +0800"
    if message_id is not False:
        msg["Message-ID"] = message_id or f"<{uuid.uuid4().hex}@huaxing.com>"
    msg.set_content(body)
    if attachment:
        msg.add_attachment(attachment[1], maintype="text", subtype="plain", filename=attachment[0])
    return msg.as_bytes()


# ------------------------------------------------------------------ 关联规则

class MatchingTest(unittest.TestCase):
    def test_priority_and_confidence(self):
        r = matching.match(directory(), explicit_customer_id=502, emails=["wang@huaxing.com"])
        self.assertEqual((r.customer_id, r.status), (502, "explicit"))
        r = matching.match(directory(), emails=["Wang <WANG@huaxing.com>"])
        self.assertEqual((r.customer_id, r.method, r.status), (501, "contact_email", "auto"))
        r = matching.match(directory(), phones=["+86 139-0000-2222"])
        self.assertEqual((r.customer_id, r.method), (502, "contact_phone"))
        r = matching.match(directory(), emails=["new.person@huaxing.com"])
        self.assertEqual((r.customer_id, r.method, r.status), (501, "email_domain", "auto"))
        r = matching.match(directory(), text="今天和蓝海物流开了会")
        self.assertEqual((r.customer_id, r.method, r.status), (502, "company_name", "auto"))

    def test_public_mail_domain_is_not_a_company(self):
        r = matching.match(directory(), emails=["someone.else@qq.com"])
        self.assertIsNone(r.customer_id)
        self.assertEqual(r.status, "unmatched")

    def test_fuzzy_match_never_auto_links(self):
        r = matching.match(directory(), text="华星科枝那边说预算还没批")
        self.assertIsNone(r.customer_id)
        self.assertEqual(r.status, "pending")
        self.assertLessEqual(r.candidates[0]["confidence"], matching.FUZZY_CAP)
        self.assertLess(r.candidates[0]["confidence"], matching.AUTO)
        self.assertEqual(r.candidates[0]["customer_id"], 501)

    def test_low_similarity_is_not_linked(self):
        r = matching.match(directory(), text="今天天气不错")
        self.assertEqual((r.customer_id, r.status, r.candidates), (None, "unmatched", []))

    def test_people_of_two_customers_ask_the_user(self):
        r = matching.match(directory(), emails=["wang@huaxing.com", "li@lanhai.cn"])
        self.assertIsNone(r.customer_id)
        self.assertEqual({c["customer_id"] for c in r.candidates}, {501, 502})

    def test_learned_alias_matches(self):
        r = matching.match(directory([("email", "boss@gmail.com", 503)]), emails=["boss@gmail.com"])
        self.assertEqual((r.customer_id, r.status), (503, "auto"))


class SourcesTest(unittest.TestCase):
    def test_email_fields_participants_and_attachment_text(self):
        draft = parse_email(make_eml(message_id="<abc@huaxing.com>", attachment=("需求.txt", b"need 50 seats")),
                            own_addresses=["me@ourcorp.com"])
        self.assertEqual((draft.activity_type, draft.external_source_id, draft.title), ("email", "abc@huaxing.com", "关于报价"))
        self.assertEqual(draft.emails, ["wang@huaxing.com"])                # 自己的地址不算参与人
        self.assertEqual(draft.occurred_at, datetime(2026, 10, 9, 2, 0))     # 转成 UTC
        self.assertIn("need 50 seats", draft.content)                        # 附件走文档解析

    def test_email_without_message_id_uses_content_hash(self):
        raw = make_eml(message_id=False)
        self.assertEqual(parse_email(raw).external_source_id, parse_email(raw).external_source_id)
        self.assertTrue(parse_email(raw).external_source_id.startswith("sha256:"))

    def test_calendar_events(self):
        ics = ("BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:m-1\r\nSUMMARY:华星科技方案评审\r\n"
               "DTSTART;TZID=Asia/Shanghai:20261012T140000\r\nDTEND;TZID=Asia/Shanghai:20261012T150000\r\n"
               "ORGANIZER;CN=我:mailto:me@ourcorp.com\r\nATTENDEE;CN=王总:mailto:wang@huaxing.com\r\n"
               "DESCRIPTION:讨论报价\\n确认上线时间\r\nURL:https://docs.example.com/minutes\r\nEND:VEVENT\r\n"
               "BEGIN:VEVENT\r\nUID:m-2\r\nSUMMARY:取消的会\r\nSTATUS:CANCELLED\r\nDTSTART:20261013T020000Z\r\nEND:VEVENT\r\n"
               "END:VCALENDAR\r\n").encode()
        drafts = parse_calendar(ics, own_addresses=["me@ourcorp.com"])
        self.assertEqual(len(drafts), 1)                                     # 取消的会议不算
        meeting = drafts[0]
        self.assertEqual((meeting.activity_type, meeting.external_source_id), ("meeting", "ics:m-1"))
        self.assertEqual(meeting.occurred_at, datetime(2026, 10, 12, 6, 0))
        self.assertEqual(meeting.emails, ["wang@huaxing.com"])
        self.assertIn("确认上线时间", meeting.content)
        self.assertIn("https://docs.example.com/minutes", meeting.content)


# ------------------------------------------------------------------ 风险规则

class RiskRulesTest(unittest.TestCase):
    today = date(2026, 10, 10)

    def evaluate(self, customer=None, activities=(), history=None, open_tasks=0):
        base = {"name": "华星科技", "contacts": [{"name": "王总", "title": "采购总监"}], "recentFollowUps": [],
                "opportunities": []}
        base.update(customer or {})
        return {r.risk_code: r for r in risk_rules.evaluate(self.today, base, list(activities), history or {}, open_tasks)}

    def test_quote_without_response(self):
        quote = {"id": 7, "activity_type": "quote", "occurred_at": datetime(2026, 9, 20, 2), "title": "报价单 v2"}
        risks = self.evaluate(activities=[quote])
        risk = risks["QUOTE_NO_RESPONSE"]
        self.assertEqual(risk.level, "high")
        self.assertIn("报价发送于 2026-09-20", risk.evidence)
        self.assertIn("20 天没有有效沟通", risk.evidence)
        reply = {"id": 8, "activity_type": "email", "occurred_at": datetime(2026, 9, 25, 2), "title": "回复"}
        self.assertNotIn("QUOTE_NO_RESPONSE", self.evaluate(activities=[quote, reply]))

    def test_opportunity_rules_carry_evidence(self):
        opp = {"id": 9, "stage": "PROPOSAL", "amount": 70000, "expectedCloseDate": "2026-10-14", "nextStep": None,
               "updatedAt": "2026-09-01T00:00:00Z"}
        history = {9: [
            {"stage": "PROPOSAL", "amount": "100000", "expected_close_date": "2026-09-15", "captured_at": datetime(2026, 8, 20)},
            {"stage": "PROPOSAL", "amount": "100000", "expected_close_date": "2026-09-30", "captured_at": datetime(2026, 9, 10)},
            {"stage": "PROPOSAL", "amount": "70000", "expected_close_date": "2026-10-14", "captured_at": datetime(2026, 9, 28)},
        ]}
        recent = {"id": 1, "activity_type": "call", "occurred_at": datetime(2026, 10, 9), "title": "电话"}
        risks = self.evaluate({"opportunities": [opp]}, [recent], history)
        self.assertIn("降到 70,000", risks["AMOUNT_DECREASED"].evidence)
        self.assertIn("已推迟 2 次", risks["MULTIPLE_DELAYS"].evidence)
        self.assertIn("预计成交日期 2026-10-14", risks["CLOSE_DATE_NEAR_STAGE_STALE"].evidence)
        self.assertIn("NO_NEXT_STEP", risks)
        self.assertNotIn("NO_FOLLOWUP_LONG", risks)
        for risk in risks.values():
            self.assertTrue(risk.evidence and risk.suggested_action, risk.risk_code)
        self.assertNotIn("NO_NEXT_STEP", self.evaluate({"opportunities": [opp]}, [recent], history, open_tasks=1))

    def test_stale_and_key_contact(self):
        opp = {"id": 9, "stage": "NEGOTIATION", "amount": 1, "nextStep": "下周签约"}
        old = {"id": 1, "activity_type": "email", "occurred_at": datetime(2026, 9, 1), "title": "上次邮件"}
        risks = self.evaluate({"opportunities": [opp], "contacts": [{"name": "小张", "title": "专员"}]}, [old])
        self.assertIn("已经 39 天没有跟进", risks["NO_FOLLOWUP_LONG"].evidence)
        self.assertIn("没有决策人", risks["KEY_CONTACT_MISSING"].evidence)

    def test_model_risk_must_quote_the_source(self):
        acts = {3: {"id": 3, "title": "周会", "content": "客户说今年预算只剩 20 万，可能不够。", "occurred_at": datetime(2026, 10, 1),
                    "type_label": "会议"}}
        raw = {"risks": [
            {"risk_code": "BUDGET_INSUFFICIENT", "activity_id": 3, "evidence": "今年预算只剩 20 万", "level": "high"},
            {"risk_code": "COMPETITOR", "activity_id": 3, "evidence": "客户在和友商比价"},          # 原文里没有：丢掉
            {"risk_code": "MADE_UP", "activity_id": 3, "evidence": "今年预算只剩 20 万"},           # 不认识的类型：丢掉
        ]}
        risks = risk_rules.grounded_model_risks(raw, acts)
        self.assertEqual([r.risk_code for r in risks], ["BUDGET_INSUFFICIENT"])
        self.assertIn("今年预算只剩 20 万", risks[0].evidence)
        self.assertEqual(risks[0].source, "model")


# ------------------------------------------------------------------ 数据库 + 路由

def run_db(fn):
    from models.async_db import AsyncSessionLocal

    async def go():
        async with AsyncSessionLocal() as db:
            return await fn(db)
    return run_async(go())


@unittest.skipUnless(_AVAILABLE, _WHY)
class CrmCopilotRouteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from models.init_db import SessionLocal
        from tests.test_enterprise_access import _add_org_member, _add_team_member, _create_org, _create_team
        cls.db = SessionLocal()
        cls.client = rc.make_client()
        cls.sales = rc.create_user("crm-sales")
        cls.other = rc.create_user("crm-other")
        cls.org = _create_org(cls.db, "crm-org-" + uuid.uuid4().hex[:6], cls.sales["id"])
        cls.team = _create_team(cls.db, cls.org, "crm-team", cls.sales["id"])
        cls.team2 = _create_team(cls.db, cls.org, "crm-team2", cls.other["id"])
        cls.db.execute(text("UPDATE teams SET department_code='sales' WHERE id IN (:a, :b)"), {"a": cls.team, "b": cls.team2})
        cls.db.commit()
        _add_org_member(cls.db, cls.org, cls.sales["id"], "member")
        _add_org_member(cls.db, cls.org, cls.other["id"], "member")
        _add_team_member(cls.db, cls.team, cls.sales["id"], "member")
        _add_team_member(cls.db, cls.team2, cls.other["id"], "member")

    @classmethod
    def tearDownClass(cls):
        cls.cleanup_rows()
        rc.cleanup()
        cls.db.execute(text("DELETE FROM teams WHERE id IN (:a, :b)"), {"a": cls.team, "b": cls.team2})
        cls.db.execute(text("DELETE FROM organizations WHERE id=:o"), {"o": cls.org})
        cls.db.commit()
        cls.db.close()

    @classmethod
    def cleanup_rows(cls):
        cls.db.commit()
        teams = {"a": cls.team, "b": cls.team2}
        for table in ("customer_activity", "crm_customer_alias", "customer_summary_snapshot", "crm_opportunity_snapshot",
                      "crm_risk_finding", "crm_action_suggestion", "crm_mail_account"):
            cls.db.execute(text(f"DELETE FROM {table} WHERE team_id IN (:a, :b)"), teams)
        cls.db.execute(text("DELETE FROM work_item WHERE team_id IN (:a, :b)"), teams)
        cls.db.commit()

    def setUp(self):
        self.cleanup_rows()
        env = patch.dict("os.environ", {"CRM_INTERNAL_EMAIL_DOMAINS": "ourcorp.com"})   # 本公司邮箱域名
        env.start()
        self.addCleanup(env.stop)
        self.detail = {"id": 501, "name": "华星科技有限公司", "contacts": [{"name": "王总", "title": "采购总监"}],
                       "recentFollowUps": [], "opportunities": []}
        self.hub = AsyncMock(side_effect=self._hub)
        p = patch("service.crm.activities.hub_get", new=self.hub)
        p.start()
        self.addCleanup(p.stop)

    async def _hub(self, user_id, team_id, path, operation):
        if path == "/crm/customers":
            return CUSTOMERS
        if path == "/crm/contacts":
            return CONTACTS
        if path.startswith("/crm/customers/"):
            from service.exceptions import NotFound
            if int(path.rsplit("/", 1)[1]) not in (501, 502, 503):
                raise NotFound("客户不存在")
            return self.detail
        raise AssertionError(path)

    def upload(self, raw, user=None, team=None, name="mail.eml"):
        user = user or self.sales
        return self.client.post("/enterprise/crm-copilot/ingest/email", headers=user["headers"],
                                data={"team_id": str(team or self.team)}, files={"file": (name, raw, "message/rfc822")})

    def activity_count(self):
        self.db.commit()
        return self.db.execute(text("SELECT COUNT(*) FROM customer_activity WHERE team_id=:t"), {"t": self.team}).scalar()

    # -------- 入库、去重、关联

    def test_same_email_is_only_stored_once(self):
        raw = make_eml(message_id="<dup-1@huaxing.com>")
        first = self.upload(raw).json()
        self.assertEqual((first["duplicate"], first["activity"]["customer_id"], first["activity"]["match_method"]),
                         (False, 501, "contact_email"))
        again = self.upload(raw).json()
        self.assertTrue(again["duplicate"])
        self.assertEqual(self.activity_count(), 1)

    def test_fuzzy_match_is_not_saved_to_a_customer(self):
        r = self.upload(make_eml(sender="x@unknown.com", subject="华星科枝的需求", body="对方想要演示")).json()
        self.assertIsNone(r["activity"]["customer_id"])
        self.assertEqual(r["activity"]["match_status"], "pending")
        self.assertEqual(r["activity"]["match_candidates"][0]["customer_id"], 501)
        pending = self.client.get(f"/enterprise/crm-copilot/activities?team_id={self.team}", headers=self.sales["headers"]).json()
        self.assertEqual(pending["pending_count"], 1)

    def test_manual_assignment_is_remembered(self):
        first = self.upload(make_eml(sender="ceo@gmail.com", subject="合作意向", body="我们想谈谈")).json()["activity"]
        self.assertEqual(first["match_status"], "unmatched")
        pending_too = self.upload(make_eml(sender="ceo@gmail.com", subject="补充", body="再说一句")).json()["activity"]
        r = self.client.post(f"/enterprise/crm-copilot/activities/{first['id']}/assign", headers=self.sales["headers"],
                             json={"team_id": self.team, "customer_id": 503})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["activity"]["customer_id"], 503)
        self.assertEqual(body["learned"], [{"type": "email", "value": "ceo@gmail.com"}])   # 公共邮箱不记域名
        self.assertEqual(body["rematched"], 1)                                              # 之前那条也对上了
        later = self.upload(make_eml(sender="ceo@gmail.com", subject="第三封", body="确认时间")).json()["activity"]
        self.assertEqual((later["customer_id"], later["match_status"]), (503, "auto"))
        self.db.commit()
        status = self.db.execute(text("SELECT customer_id FROM customer_activity WHERE id=:i"), {"i": pending_too["id"]}).scalar()
        self.assertEqual(status, 503)

    def test_other_department_cannot_see_or_assign(self):
        mine = self.upload(make_eml()).json()["activity"]
        r = self.client.get(f"/enterprise/crm-copilot/activities?team_id={self.team}", headers=self.other["headers"])
        self.assertEqual(r.status_code, 403)
        r = self.client.post(f"/enterprise/crm-copilot/activities/{mine['id']}/assign", headers=self.other["headers"],
                             json={"team_id": self.team2, "customer_id": 501})
        self.assertEqual(r.status_code, 404)                 # 别的部门的活动：按不存在处理

    def test_calendar_and_manual_activity_in_timeline(self):
        ics = ("BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:tl-1\nSUMMARY:方案评审\nDTSTART:20261008T020000Z\n"
               "ATTENDEE:mailto:wang@huaxing.com\nEND:VEVENT\nEND:VCALENDAR\n").encode()
        r = self.client.post("/enterprise/crm-copilot/ingest/calendar", headers=self.sales["headers"],
                             data={"team_id": str(self.team)}, files={"file": ("m.ics", ics, "text/calendar")})
        self.assertEqual(r.json()["created"], 1)
        r = self.client.post("/enterprise/crm-copilot/activities", headers=self.sales["headers"], json={
            "team_id": self.team, "customer_id": 501, "activity_type": "call", "title": "电话", "content": "确认了需求"})
        self.assertEqual(r.status_code, 200, r.text)
        timeline = self.client.get(f"/enterprise/crm-copilot/customers/501/timeline?team_id={self.team}",
                                   headers=self.sales["headers"]).json()
        self.assertEqual({i["kind"] for i in timeline["items"]}, {"meeting", "call"})

    # -------- 摘要

    def test_summary_is_incremental_and_only_uses_permitted_data(self):
        from service.crm import insights
        self.upload(make_eml(subject="需求沟通", body="王总说需要 50 个账号"))
        # 另一个部门对同一个客户 ID 记的活动：不能进这个部门的摘要
        self.upload(make_eml(subject="二部的秘密报价", body="二部内部价格 8 折"), user=self.other, team=self.team2)
        prompts = []

        async def fake_ask(db, user_id, model_name, system, message):
            prompts.append(message)
            return {"summary": f"第{len(prompts)}版摘要", "needs": ["50 个账号"], "stakeholders": ["王总（采购总监）"],
                    "risks": [], "next_actions": ["发送正式报价"]}
        with patch.object(insights, "_ask_json", fake_ask), patch.object(insights, "_check_model", AsyncMock(return_value="m")):
            first = self.client.post("/enterprise/crm-copilot/customers/501/summary/refresh", headers=self.sales["headers"],
                                     json={"team_id": self.team, "model_name": "m"}).json()
            self.assertEqual(first["snapshot"]["summary"], "第1版摘要")
            self.assertIn("50 个账号", prompts[0])
            self.assertNotIn("8 折", prompts[0])
            again = self.client.post("/enterprise/crm-copilot/customers/501/summary/refresh", headers=self.sales["headers"],
                                     json={"team_id": self.team, "model_name": "m"}).json()
            self.assertTrue(again["unchanged"])
            self.assertEqual(len(prompts), 1)                                     # 没有新活动：不调用模型
            self.upload(make_eml(subject="新进展", body="对方要求下周演示"))
            third = self.client.post("/enterprise/crm-copilot/customers/501/summary/refresh", headers=self.sales["headers"],
                                     json={"team_id": self.team, "model_name": "m"}).json()
        self.assertEqual(third["activities_used"], 1)
        self.assertIn("第1版摘要", prompts[1])                                   # 旧摘要 + 新活动
        self.assertIn("下周演示", prompts[1])
        self.assertNotIn("50 个账号", prompts[1].split("【新的客户活动】")[1])    # 旧活动不再送一遍
        suggestions = self.client.get(f"/enterprise/crm-copilot/suggestions?team_id={self.team}",
                                      headers=self.sales["headers"]).json()
        self.assertEqual([s["title"] for s in suggestions], ["发送正式报价"])     # 同一条建议只生成一次

    # -------- 风险与建议

    def test_risk_scan_has_evidence_and_does_not_repeat(self):
        self.client.post("/enterprise/crm-copilot/activities", headers=self.sales["headers"], json={
            "team_id": self.team, "customer_id": 501, "activity_type": "quote", "title": "报价单",
            "occurred_at": (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=30)).isoformat() + "Z"})
        url = "/enterprise/crm-copilot/customers/501/risks/scan"
        first = self.client.post(url, headers=self.sales["headers"], json={"team_id": self.team}).json()
        codes = {r["risk_code"] for r in first["risks"]}
        self.assertIn("QUOTE_NO_RESPONSE", codes)
        self.assertTrue(all(r["evidence"] for r in first["risks"]))
        self.client.post(url, headers=self.sales["headers"], json={"team_id": self.team})
        self.db.commit()
        findings = self.db.execute(text("SELECT COUNT(*) FROM crm_risk_finding WHERE team_id=:t"), {"t": self.team}).scalar()
        suggestions = self.db.execute(text("SELECT COUNT(*) FROM crm_action_suggestion WHERE team_id=:t"), {"t": self.team}).scalar()
        self.assertEqual(findings, len(first["risks"]))                       # 再扫一次不会多出风险
        self.assertEqual(suggestions, len(first["risks"]))                    # 也不会多出建议

    def test_suggestions_are_not_written_automatically(self):
        from service import enterprise_hub_client as hub
        self.client.post("/enterprise/crm-copilot/activities", headers=self.sales["headers"], json={
            "team_id": self.team, "customer_id": 501, "activity_type": "quote", "title": "报价单",
            "occurred_at": (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=30)).isoformat() + "Z"})
        with patch.object(hub, "call") as hub_call:
            self.client.post("/enterprise/crm-copilot/customers/501/risks/scan", headers=self.sales["headers"],
                             json={"team_id": self.team})
            hub_call.assert_not_called()                                      # 没有改商机、没有联系客户
        self.db.commit()
        items = self.db.execute(text("SELECT COUNT(*) FROM work_item WHERE team_id=:t"), {"t": self.team}).scalar()
        self.assertEqual(items, 0)                                            # 销售没确认：不进待办
        suggestions = self.client.get(f"/enterprise/crm-copilot/suggestions?team_id={self.team}",
                                      headers=self.sales["headers"]).json()
        quote = next(s for s in suggestions if "评审进度" in s["title"])
        r = self.client.post(f"/enterprise/crm-copilot/suggestions/{quote['id']}/decide", headers=self.sales["headers"],
                             json={"team_id": self.team, "decision": "edit_create", "title": "周三前电话王总确认评审",
                                   "due_date": "2026-10-14"})
        self.assertEqual(r.json()["status"], "created")
        self.db.commit()
        title = self.db.execute(text("SELECT title FROM work_item WHERE team_id=:t"), {"t": self.team}).scalar()
        self.assertEqual(title, "周三前电话王总确认评审")
        r = self.client.post(f"/enterprise/crm-copilot/suggestions/{quote['id']}/decide", headers=self.sales["headers"],
                             json={"team_id": self.team, "decision": "ignore"})
        self.assertEqual(r.status_code, 409)

    def test_ignored_and_snoozed_suggestions(self):
        self.client.post("/enterprise/crm-copilot/activities", headers=self.sales["headers"], json={
            "team_id": self.team, "customer_id": 501, "activity_type": "quote", "title": "报价单",
            "occurred_at": (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=30)).isoformat() + "Z"})
        url = "/enterprise/crm-copilot/customers/501/risks/scan"
        self.client.post(url, headers=self.sales["headers"], json={"team_id": self.team})
        listed = self.client.get(f"/enterprise/crm-copilot/suggestions?team_id={self.team}", headers=self.sales["headers"]).json()
        first, *rest = listed
        self.client.post(f"/enterprise/crm-copilot/suggestions/{first['id']}/decide", headers=self.sales["headers"],
                         json={"team_id": self.team, "decision": "ignore"})
        if rest:
            self.client.post(f"/enterprise/crm-copilot/suggestions/{rest[0]['id']}/decide", headers=self.sales["headers"],
                             json={"team_id": self.team, "decision": "snooze", "remind_days": 2})
        self.client.post(url, headers=self.sales["headers"], json={"team_id": self.team})       # 再扫一次
        again = self.client.get(f"/enterprise/crm-copilot/suggestions?team_id={self.team}", headers=self.sales["headers"]).json()
        ids = {s["id"] for s in again}
        self.assertNotIn(first["id"], ids)                                   # 忽略的不会重新冒出来
        if rest:
            self.assertNotIn(rest[0]["id"], ids)                             # 稍后提醒：到时间前不显示
            self.db.execute(text("UPDATE crm_action_suggestion SET remind_at=:t WHERE id=:i"),
                            {"t": datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=1), "i": rest[0]["id"]})
            self.db.commit()
            back = self.client.get(f"/enterprise/crm-copilot/suggestions?team_id={self.team}", headers=self.sales["headers"]).json()
            self.assertIn(rest[0]["id"], {s["id"] for s in back})

    # -------- 聊天进 CRM

    def test_chat_message_saved_only_when_asked(self):
        from service.crm import chat_ingest
        self.assertFalse(chat_ingest.wants_crm("帮我查一下华星科技的报价"))
        self.assertTrue(chat_ingest.wants_crm("保存到CRM：华星科技王总说预算下周批"))
        self.assertTrue(chat_ingest.wants_crm("#crm 蓝海物流要求延期"))
        reply = run_db(lambda db: chat_ingest.save(db, self.sales["id"], "feishu", "om_1", "保存到CRM：华星科技王总说预算下周批"))
        self.assertIn("华星科技有限公司", reply)
        again = run_db(lambda db: chat_ingest.save(db, self.sales["id"], "feishu", "om_1", "保存到CRM：华星科技王总说预算下周批"))
        self.assertIn("已经保存过", again)
        self.assertEqual(self.activity_count(), 1)


if __name__ == "__main__":
    unittest.main()
