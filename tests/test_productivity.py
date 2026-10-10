"""提效事实表与仪表盘：指标口径（节省时间最低为 0、采纳率、原样采纳率、完成率、失败率）、三种时间分开、
重复整理不重复计数、部门看板权限、员工反馈单独记。"""
import json
import unittest
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import text

from service import productivity_service as ps
from service.exceptions import PermissionDenied
from tests import _route_client as rc
from tests._async_helpers import run_async

_AVAILABLE, _WHY = rc.route_tests_available()


def fact(**kw):
    base = dict(source_type="automation", user_id=1, team_id=1, agent_id=None, saved_minutes=0.0, agent_seconds=0.0,
                review_seconds=0.0, reported_saved_minutes=None, draft_created=1, draft_adopted=0, adopted_as_is=0,
                business_initiated=0, completed=0, failed=0, failure_code=None, fields_changed_json=None,
                started_at=datetime(2026, 10, 1, 2))
    base.update(kw)
    return SimpleNamespace(**base)


class FormulaTest(unittest.TestCase):
    def test_saved_minutes_never_negative_and_only_when_counted(self):
        self.assertEqual(ps.saved(10, 120, 60, True), 7.0)
        self.assertEqual(ps.saved(2, 600, 600, True), 0.0)              # 用时超过基准：不出现负节省
        self.assertEqual(ps.saved(10, 0, 0, False), 0.0)                # 没被采纳：不算节省

    def test_rates_and_three_kinds_of_time_stay_separate(self):
        facts = [
            fact(draft_adopted=1, adopted_as_is=1, business_initiated=1, completed=1, saved_minutes=8, agent_seconds=60,
                 review_seconds=60, reported_saved_minutes=15),
            fact(draft_adopted=1, business_initiated=1, saved_minutes=5, fields_changed_json=json.dumps(["amount"])),
            fact(),                                                       # 生成了草稿但没采纳
            fact(source_type="agent_run", draft_created=0, completed=1, agent_id=7),
            fact(source_type="agent_run", draft_created=0, failed=1, failure_code="MODEL_TIMEOUT", agent_id=7),
        ]
        out = ps.summarize(facts, {"team": {1: "销售部"}, "agent": {7: "销售助手"}})
        self.assertEqual(out["rates"], {"adoption": round(2 / 3, 4), "as_is": 0.5, "completion": 0.5, "failure": 0.5})
        t = out["totals"]
        self.assertEqual((t["estimated_saved_minutes"], t["measured_minutes"], t["reported_saved_minutes"]), (13.0, 2.0, 15.0))
        self.assertEqual(t["items"], 3)                                   # Agent 运行不算“处理量”
        self.assertEqual(out["failure_reasons"], [{"code": "MODEL_TIMEOUT", "count": 1}])
        self.assertEqual(out["changed_fields"], [{"field": "amount", "count": 1}])
        self.assertEqual(out["by_agent"][0]["failure_rate"], 0.5)
        self.assertEqual(out["by_team"][0]["name"], "销售部")

    def test_failure_code_from_old_runs(self):
        run = lambda **kw: SimpleNamespace(**{"error_code": None, "status": "failed", "error_msg": None, **kw})  # noqa: E731
        self.assertEqual(ps.failure_code(run(error_code="DATABASE_ERROR")), "DATABASE_ERROR")
        self.assertEqual(ps.failure_code(run(status="max_iter")), "MAX_ITERATIONS")
        self.assertEqual(ps.failure_code(run(error_msg="Read timed out")), "MODEL_TIMEOUT")
        self.assertEqual(ps.failure_code(run(error_msg="请先在【模型配置】中配置 glm-4 的 API Key")), "MODEL_NOT_CONFIGURED")
        self.assertEqual(ps.failure_code(run(error_msg="奇怪的错误")), "UNKNOWN")

    def test_automation_fact(self):
        created = datetime(2026, 10, 1, 1, 0, 0)
        work = SimpleNamespace(id="w1", kind="no_such_kind", status="applied", user_id=1, team_id=2, elapsed_ms=30000,
                               created_at=created, applied_at=created + timedelta(minutes=3), edited=1,
                               proposal_json=json.dumps({"amount": 1, "reason": "a"}),
                               accepted_json=json.dumps({"amount": 2, "reason": "a"}),
                               business_result_json=json.dumps({"id": 55}), completed_tasks_json="[]")
        f = ps.from_automation(work, 10)
        self.assertEqual((f["draft_adopted"], f["adopted_as_is"], f["completed"], f["business_object_id"]), (1, 0, 1, "55"))
        self.assertEqual((f["agent_seconds"], f["review_seconds"]), (30.0, 150.0))
        self.assertEqual(f["saved_minutes"], 7.0)                           # 10 − 0.5 − 2.5
        self.assertEqual(json.loads(f["fields_changed_json"]), ["amount"])

    def test_crm_saving_subtracts_human_review_time(self):
        created = datetime(2026, 10, 1, 1, 0, 0)
        suggestion = SimpleNamespace(
            id=9, status="created", decided_by=1, team_id=2, work_item_key="crm:9", decision="create",
            created_at=created, decided_at=created + timedelta(minutes=2),
        )
        result = ps.from_crm_suggestion(suggestion, baseline=5, work_done=False)
        self.assertEqual(result["review_seconds"], 120.0)
        self.assertEqual(result["saved_minutes"], 3.0)


@unittest.skipUnless(_AVAILABLE, _WHY)
class ProductivityDbTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from models.init_db import SessionLocal
        from tests.test_enterprise_access import _add_org_member, _add_team_member, _create_org, _create_team
        cls.db = SessionLocal()
        cls.client = rc.make_client()
        cls.head = rc.create_user("prod-head")
        cls.member = rc.create_user("prod-mem")
        cls.org = _create_org(cls.db, "prod-org-" + uuid.uuid4().hex[:6], cls.head["id"])
        cls.team = _create_team(cls.db, cls.org, "prod-team", cls.head["id"])
        _add_org_member(cls.db, cls.org, cls.head["id"], "member")
        _add_org_member(cls.db, cls.org, cls.member["id"], "member")
        _add_team_member(cls.db, cls.team, cls.head["id"], "admin")
        _add_team_member(cls.db, cls.team, cls.member["id"], "member")

    @classmethod
    def tearDownClass(cls):
        cls.db.execute(text("DELETE FROM productivity_fact WHERE user_id IN (:a, :b)"), {"a": cls.head["id"], "b": cls.member["id"]})
        cls.db.execute(text("DELETE FROM invoice_extraction WHERE user_id IN (:a, :b)"), {"a": cls.head["id"], "b": cls.member["id"]})
        cls.db.commit()
        rc.cleanup()
        cls.db.execute(text("DELETE FROM teams WHERE id=:t"), {"t": cls.team})
        cls.db.execute(text("DELETE FROM organizations WHERE id=:o"), {"o": cls.org})
        cls.db.commit()
        cls.db.close()

    def run_db(self, fn):
        from models.async_db import AsyncSessionLocal

        async def go():
            async with AsyncSessionLocal() as db:
                return await fn(db)
        return run_async(go())

    def test_refresh_is_idempotent_and_department_view_is_scoped(self):
        from models.init_db import InvoiceExtraction
        from utils.timeutil import utcnow
        now = utcnow()
        self.db.add(InvoiceExtraction(user_id=self.member["id"], team_id=self.team, file_sha256=uuid.uuid4().hex,
                                      status="used", claim_id=9, created_at=now - timedelta(minutes=3), confirmed_at=now,
                                      corrected_fields_json=json.dumps(["total_amount"])))
        self.db.commit()
        self.run_db(lambda db: ps.refresh(db, force=True))
        self.run_db(lambda db: ps.refresh(db, force=True))                 # 再整理一次不能重复计数
        self.db.commit()
        count = self.db.execute(text("SELECT COUNT(*) FROM productivity_fact WHERE user_id=:u"), {"u": self.member["id"]}).scalar()
        self.assertEqual(count, 1)

        with self.assertRaises(PermissionDenied):                         # 普通成员不能看部门看板
            self.run_db(lambda db: ps.department_dashboard(db, self.member["id"], self.team))
        board = self.run_db(lambda db: ps.department_dashboard(db, self.head["id"], self.team))
        self.assertEqual(board["totals"]["items"], 1)
        self.assertEqual(len(board["trend"]), 30)                        # 趋势按天补齐
        self.assertEqual(board["totals"]["completed"], 1)
        self.assertEqual(board["changed_fields"], [{"field": "total_amount", "count": 1}])

        path = f"/department/productivity?team_id={self.team}&days=30"
        self.assertEqual(self.client.get(path, headers=self.member["headers"]).status_code, 403)
        route_board = self.client.get(path, headers=self.head["headers"])
        self.assertEqual(route_board.status_code, 200, route_board.text)
        self.assertEqual(route_board.json()["totals"]["items"], 1)

        key = self.db.execute(text("SELECT source_key FROM productivity_fact WHERE user_id=:u"), {"u": self.member["id"]}).scalar()
        self.db.commit()
        r = self.client.post("/productivity/feedback", headers=self.member["headers"], json={"source_key": key, "minutes": 12})
        self.assertEqual(r.status_code, 200, r.text)
        self.run_db(lambda db: ps.refresh(db, force=True))                 # 重新整理不会覆盖员工反馈
        board = self.run_db(lambda db: ps.department_dashboard(db, self.head["id"], self.team))
        self.assertEqual(board["totals"]["reported_saved_minutes"], 12.0)
        self.assertNotEqual(board["totals"]["estimated_saved_minutes"], 12.0)
        r = self.client.post("/productivity/feedback", headers=self.head["headers"], json={"source_key": key, "minutes": 99})
        self.assertEqual(r.status_code, 404)                              # 只能反馈自己的记录


if __name__ == "__main__":
    unittest.main()
