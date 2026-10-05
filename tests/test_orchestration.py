"""跨部门协同办理：拆分规则、计划持久化、按部门判断能否在本部门整理、开始整理复用 AI 工作成果。"""
import json
import unittest
import uuid
from unittest.mock import AsyncMock, patch

from tests import _route_client as rc  # 先导入：测试用的异步连接不复用（必须在导入 models 之前）

from sqlalchemy import text

from models.init_db import SessionLocal
from service import automation_work_service as works
from service import orchestration_service as orch
from tests.test_enterprise_access import _add_org_member, _add_team_member, _create_org, _create_team

_AVAILABLE, _WHY = rc.route_tests_available()

REQUEST = ("下周一到周三我要请年假，回来后报销上个月出差的高铁票 260 元。另外我的笔记本电脑蓝屏了，需要报修；"
           "还要给客户张总安排一次回访。")


class PlannerTest(unittest.TestCase):
    def test_splits_and_classifies_with_reasons(self):
        steps = orch.plan_steps(REQUEST)
        self.assertEqual([s["kind"] for s in steps], ["leave", "expense", "ticket", "crm"])
        self.assertIn("命中“", steps[2]["reason"])
        self.assertIn("蓝屏", steps[2]["reason"])
        self.assertEqual(steps[3]["department_code"], "sales")

    def test_unmatched_clause_attaches_to_previous_and_same_kind_merges(self):
        steps = orch.plan_steps("电脑打不开了。型号是 ThinkPad T14。显示器也报修一下")
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0]["kind"], "ticket")
        self.assertIn("ThinkPad T14", steps[0]["clause"])
        self.assertIn("显示器", steps[0]["clause"])

    def test_hr_case_and_nothing_matched(self):
        self.assertEqual([s["kind"] for s in orch.plan_steps("新同事下周一入职，需要开通账号和配电脑")], ["hr_case"])
        self.assertEqual(orch.plan_steps("今天天气不错"), [])


@unittest.skipUnless(_AVAILABLE, _WHY)
class PlanLifecycleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = SessionLocal()
        cls.client = rc.make_client()
        cls.owner = rc.create_user("orc-owner")
        cls.emp = rc.create_user("orc-emp")
        cls.other = rc.create_user("orc-oth")
        cls.org = _create_org(cls.db, "orc-org-" + uuid.uuid4().hex[:6], cls.owner["id"])
        cls.hr_team = _create_team(cls.db, cls.org, "orc-hr", cls.owner["id"])
        cls.db.execute(text("UPDATE teams SET department_code='hr' WHERE id=:t"), {"t": cls.hr_team})
        cls.db.commit()
        _add_team_member(cls.db, cls.hr_team, cls.emp["id"], "member")
        _add_org_member(cls.db, cls.org, cls.emp["id"], "member")
        _add_org_member(cls.db, cls.org, cls.other["id"], "member")

    @classmethod
    def tearDownClass(cls):
        rc.cleanup()
        cls.db.execute(text("DELETE FROM team_members WHERE team_id=:t"), {"t": cls.hr_team})
        cls.db.execute(text("DELETE FROM teams WHERE id=:t"), {"t": cls.hr_team})
        cls.db.execute(text("DELETE FROM organization_members WHERE organization_id=:o"), {"o": cls.org})
        cls.db.execute(text("DELETE FROM organizations WHERE id=:o"), {"o": cls.org})
        cls.db.commit()
        cls.db.close()

    def post(self, path, body, user=None, method="post"):
        return getattr(self.client, method)(f"/enterprise/orchestration{path}", json=body, headers=(user or self.emp)["headers"])

    def create(self):
        response = self.post("/plans", {"team_id": self.hr_team, "text": REQUEST})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_plan_marks_what_this_department_can_do(self):
        plan = self.create()
        states = {s["kind"]: s["state"] for s in plan["steps"]}
        self.assertEqual(states, {"leave": "pending", "expense": "pending", "ticket": "pending", "crm": "handoff"})
        crm = next(s for s in plan["steps"] if s["kind"] == "crm")
        self.assertIn("仅对销售部门开放", crm["reason"])
        self.assertEqual(plan["progress"], {"done": 0, "total": 3, "handoff": 1})

    def test_only_owner_and_members(self):
        plan = self.create()
        self.assertEqual(self.client.get(f"/enterprise/orchestration/plans/{plan['id']}", headers=self.other["headers"]).status_code, 404)
        self.assertEqual(self.post("/plans", {"team_id": self.hr_team, "text": REQUEST}, self.other).status_code, 403)
        self.assertEqual(self.post("/plans", {"team_id": self.hr_team, "text": "今天天气不错呀"}).status_code, 400)

    def test_edit_skip_and_reclassify_before_start(self):
        plan = self.create()
        crm = next(s for s in plan["steps"] if s["kind"] == "crm")
        updated = self.post(f"/plans/{plan['id']}/steps/{crm['id']}", {"kind": "ticket"}, method="patch").json()
        step = next(s for s in updated["steps"] if s["id"] == crm["id"])
        self.assertEqual((step["kind"], step["state"]), ("ticket", "pending"))
        self.assertIn("已手动改为IT 工单", step["reason"])
        skipped = self.post(f"/plans/{plan['id']}/steps/{crm['id']}", {"skip": True}, method="patch").json()
        self.assertEqual(next(s for s in skipped["steps"] if s["id"] == crm["id"])["status"], "skipped")
        self.assertEqual(self.post(f"/plans/{plan['id']}/steps/{crm['id']}", {"kind": "magic"}, method="patch").status_code, 400)

    def test_start_links_an_automation_work_and_is_idempotent(self):
        plan = self.create()
        leave = next(s for s in plan["steps"] if s["kind"] == "leave")
        crm = next(s for s in plan["steps"] if s["kind"] == "crm")
        answer = {"leave_type_code": "annual", "start_date": None, "end_date": None, "reason": "",
                  "evidence": "我要请年假", "warnings": ["日期不明确"]}
        with patch.object(works, "async_get_api_config", AsyncMock(return_value={"api_key": "t"})), \
                patch.object(works, "enforce_quota_async", AsyncMock()), \
                patch.object(works, "run_checks", AsyncMock(return_value=[])), \
                patch.object(works, "async_chat_with_usage", AsyncMock(return_value=(json.dumps(answer, ensure_ascii=False), {"total_tokens": 10}))) as model:
            started = self.post(f"/plans/{plan['id']}/steps/{leave['id']}/start", {"model_name": "test-model"})
            self.assertEqual(started.status_code, 200, started.text)
            step = next(s for s in started.json()["steps"] if s["id"] == leave["id"])
            self.assertEqual((step["state"], step["status"]), ("linked", "ready"))
            self.assertTrue(step["automation_work_id"])
            again = self.post(f"/plans/{plan['id']}/steps/{leave['id']}/start", {"model_name": "test-model"})
            self.assertEqual(again.status_code, 400)
            self.assertEqual(model.await_count, 1)
            handoff = self.post(f"/plans/{plan['id']}/steps/{crm['id']}/start", {"model_name": "test-model"})
            self.assertEqual(handoff.status_code, 400)
        work = self.db.execute(text("SELECT kind, team_id, source_text FROM automation_work WHERE id=:i"),
                               {"i": step["automation_work_id"]}).first()
        self.db.commit()
        self.assertEqual((work[0], work[1]), ("leave", self.hr_team))
        self.assertIn("年假", work[2])
        edited = self.post(f"/plans/{plan['id']}/steps/{leave['id']}", {"skip": True}, method="patch")
        self.assertEqual(edited.status_code, 400)   # 已开始整理，以工作成果为准
        listed = self.client.get(f"/enterprise/orchestration/plans?team_id={self.hr_team}", headers=self.emp["headers"]).json()
        self.assertTrue(any(p["id"] == plan["id"] for p in listed))


if __name__ == "__main__":
    unittest.main()
