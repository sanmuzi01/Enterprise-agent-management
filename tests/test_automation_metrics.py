"""AI 工作成果效果指标。"""
import json
import unittest
import uuid
from datetime import timedelta
from types import SimpleNamespace

from sqlalchemy import text

from models.init_db import AutomationWork, SessionLocal
from service import automation_metrics as m
from service.exceptions import InvalidInput
from tests import _route_client as rc
from tests._async_helpers import run_async
from tests.test_enterprise_access import _add_org_member, _add_team_member, _create_org, _create_team
from utils.timeutil import utcnow

_AVAILABLE, _WHY = rc.route_tests_available()


def work(**kw):
    now = utcnow()
    base = dict(status="applied", edited=0, apply_attempts=1, elapsed_ms=4000, total_tokens=1000, user_id=1,
                created_at=now - timedelta(minutes=3), applied_at=now, proposal_json=None, accepted_json=None,
                business_checks_json="[]", business_result_json=None)
    base.update(kw)
    return SimpleNamespace(**base)


class EditRatioTest(unittest.TestCase):
    def test_unchanged_changed_added_and_ignored_fields(self):
        original = {"content": "a", "evidence": "x", "tasks": [{"title": "t1", "due_date": None, "evidence": "e"}],
                    "warnings": ["w"]}
        self.assertEqual(m.edit_ratio(original, original), 0.0)
        edited = {**original, "content": "b", "warnings": []}   # 疑点和依据不算员工修改
        self.assertEqual(m.edit_ratio(original, edited), 1 / 3)
        extra = {**original, "tasks": original["tasks"] + [{"title": "t2", "due_date": None, "evidence": "e"}]}
        self.assertEqual(m.edit_ratio(original, extra), 2 / 5)   # 新增的一条待办两个字段都算改动

    def test_missing_side_is_zero(self):
        self.assertEqual(m.edit_ratio(None, {"a": 1}), 0.0)


class SummarizeTest(unittest.TestCase):
    def test_rates_medians_and_savings(self):
        now = utcnow()
        works = [
            work(applied_at=now, created_at=now - timedelta(minutes=2)),                       # 一次通过
            work(edited=1, applied_at=now, created_at=now - timedelta(minutes=4)),             # 修改过
            work(apply_attempts=2, applied_at=now, created_at=now - timedelta(minutes=6)),     # 重试过
            work(status="failed", elapsed_ms=2000, apply_attempts=0, applied_at=None),
            work(status="ready", apply_attempts=0, applied_at=None,
                 business_checks_json=json.dumps([{"level": "blocker", "text": "x"}])),
        ]
        row = m.summarize("procurement", works, 15)
        self.assertEqual((row["total"], row["applied"], row["failed"]), (5, 3, 1))
        self.assertEqual(row["apply_rate"], 0.6)
        self.assertEqual(row["first_pass_rate"], round(1 / 3, 3))
        self.assertEqual(row["retry_rate"], round(1 / 3, 3))
        self.assertEqual(row["business_blocked"], 1)
        self.assertEqual(row["median_apply_seconds"], 240.0)
        self.assertEqual(row["estimated_saved_minutes"], round((15 - 4) * 3, 1))
        self.assertEqual(row["tokens_per_applied"], round(5000 / 3))
        self.assertIsNone(row["cost_per_applied"])

    def test_savings_never_negative_and_empty_is_none(self):
        slow = m.summarize("expense", [work(created_at=utcnow() - timedelta(minutes=30))], 10)
        self.assertEqual(slow["estimated_saved_minutes"], 0.0)
        empty = m.summarize("expense", [], 10)
        self.assertEqual((empty["apply_rate"], empty["first_pass_rate"], empty["estimated_saved_minutes"]),
                         (None, None, None))

    def test_duplicate_drafts_counted_by_business_id(self):
        shared = json.dumps({"id": 7})
        row = m.summarize("crm", [work(business_result_json=shared), work(business_result_json=shared),
                                  work(business_result_json=json.dumps({"id": 8}))], 12)
        self.assertEqual(row["duplicate_drafts"], 1)

    def test_cost_requires_configured_price(self):
        import os
        from unittest.mock import patch
        with patch.dict(os.environ, {"AUTOMATION_TOKEN_PRICE_PER_1K": "0.05"}):
            row = m.summarize("crm", [work(total_tokens=2000)], 12)
        self.assertEqual(row["cost_per_applied"], 0.1)


@unittest.skipUnless(_AVAILABLE, _WHY)
class MetricsIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = SessionLocal()
        cls.admin = rc.create_user("mt-adm", admin=True)
        cls.user = rc.create_user("mt-usr")
        cls.org = _create_org(cls.db, "mt-org-" + uuid.uuid4().hex[:6], cls.admin["id"])
        cls.team = _create_team(cls.db, cls.org, "mt-team", cls.admin["id"])
        _add_org_member(cls.db, cls.org, cls.user["id"], "member")
        _add_team_member(cls.db, cls.team, cls.user["id"], "member")
        now = utcnow()
        proposal = {"leave_type_code": "annual", "start_date": "2026-10-12", "end_date": "2026-10-14",
                    "reason": "家里有事", "evidence": "原文", "warnings": []}
        edited = {**proposal, "reason": "家庭事务"}
        for i, (accepted, edit) in enumerate(((proposal, 0), (edited, 1))):
            cls.db.add(AutomationWork(
                id=str(uuid.uuid4()), user_id=cls.user["id"], team_id=cls.team, request_key=str(uuid.uuid4()),
                kind="leave", model_name="glm-4", sensitivity="internal", source_text="x" * 20, status="applied",
                proposal_json=json.dumps(proposal), accepted_json=json.dumps(accepted),
                business_result_json=json.dumps({"id": 900 + i}), elapsed_ms=3000, total_tokens=500, edited=edit,
                apply_attempts=1, created_at=now - timedelta(minutes=2), applied_at=now))
        cls.db.commit()

    @classmethod
    def tearDownClass(cls):
        cls.db.execute(text("DELETE FROM automation_work WHERE team_id=:t"), {"t": cls.team})
        cls.db.execute(text("DELETE FROM workflow_baseline WHERE kind='leave'"))
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

    def test_team_scoped_metrics_and_baseline_override(self):
        data = self.run_db(lambda db: m.metrics(db, 30, self.team))
        leave = next(r for r in data["workflows"] if r["kind"] == "leave")
        self.assertEqual((leave["total"], leave["applied"], leave["first_pass_rate"]), (2, 2, 0.5))
        self.assertEqual(leave["avg_edit_ratio"], round(0.5 * (1 / 4), 3))   # 第二份改了 4 个内容字段中的 1 个
        self.assertEqual(leave["baseline_minutes"], 6)
        self.assertEqual(data["team_name"], "mt-team")
        self.run_db(lambda db: m.set_baseline(db, self.admin["id"], "leave", 20))
        again = self.run_db(lambda db: m.metrics(db, 30, self.team))
        self.assertEqual(next(r for r in again["workflows"] if r["kind"] == "leave")["baseline_minutes"], 20)

    def test_baseline_validation(self):
        for kind, minutes in (("leave", 0), ("leave", 601), ("nope", 5)):
            with self.subTest(kind=kind, minutes=minutes), self.assertRaises(InvalidInput):
                self.run_db(lambda db: m.set_baseline(db, self.admin["id"], kind, minutes))

    def test_routes_are_admin_only(self):
        client = rc.make_client()
        self.assertEqual(client.get("/admin/automation-metrics", headers=self.user["headers"]).status_code, 403)
        with rc.admin_env(self.admin["name"]):
            ok = client.get(f"/admin/automation-metrics?days=7&team_id={self.team}", headers=self.admin["headers"])
            self.assertEqual(ok.status_code, 200, ok.text)
            self.assertEqual(client.get("/admin/automation-metrics?days=0", headers=self.admin["headers"]).status_code, 422)


if __name__ == "__main__":
    unittest.main()
