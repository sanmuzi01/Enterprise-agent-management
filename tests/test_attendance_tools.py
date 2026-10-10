"""考勤异常 Agent 工具：只读；员工只能查自己的；汇总只有人事/部门负责人能看且不含人名；没有任何写入、说明、认定类工具。"""
import json
import unittest
import uuid
from datetime import datetime, timedelta
from unittest.mock import patch

from tests import _route_client as rc  # 先导入：测试用的异步连接不复用（必须在导入 models 之前）

from sqlalchemy import text

from models.init_db import AttendanceAnomaly, SessionLocal
from service import enterprise_agent_templates as templates
from service.tools import attendance as tools
from service.tools.base import ToolContext, ToolRegistry
from tests.test_enterprise_access import _add_org_member, _add_team_member, _create_org, _create_team
from utils.timeutil import utcnow

_AVAILABLE, _WHY = rc.route_tests_available()


def make(cls, user):
    instance = cls()
    instance.set_context(ToolContext(user_id=user["id"]))
    return instance


class SurfaceTest(unittest.TestCase):
    def test_tools_are_read_only_and_have_no_decision_tools(self):
        for name in ("get_my_attendance_anomalies", "get_attendance_summary"):
            self.assertEqual(ToolRegistry.get(name)().risk_level, "read")
        names = [n for n in ToolRegistry.list_all() if "attendance" in n]
        self.assertEqual(sorted(names), ["get_attendance_summary", "get_my_attendance_anomalies"])

    def test_every_department_template_can_use_them(self):
        for key, template in templates.TEMPLATES.items():
            if key == "central":
                continue
            with self.subTest(template=key):
                self.assertIn("get_my_attendance_anomalies", template["tools"])
                self.assertIn("get_attendance_summary", template["tools"])


@unittest.skipUnless(_AVAILABLE, _WHY)
class AttendanceToolsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = SessionLocal()
        cls.head = rc.create_user("atl-head")
        cls.emp = rc.create_user("atl-emp")
        cls.other = rc.create_user("atl-oth")
        cls.org = _create_org(cls.db, "atl-org-" + uuid.uuid4().hex[:6], cls.head["id"])
        cls.team = _create_team(cls.db, cls.org, "atl-team", cls.head["id"])
        cls.db.commit()
        for user in (cls.head, cls.emp, cls.other):
            _add_org_member(cls.db, cls.org, user["id"], "member")
        _add_team_member(cls.db, cls.team, cls.head["id"], "admin")
        _add_team_member(cls.db, cls.team, cls.emp["id"], "member")
        _add_team_member(cls.db, cls.team, cls.other["id"], "member")

    @classmethod
    def tearDownClass(cls):
        rc.cleanup()
        cls.db.commit()
        cls.db.execute(text("DELETE FROM attendance_anomaly WHERE organization_id=:o"), {"o": cls.org})
        cls.db.execute(text("DELETE FROM team_members WHERE team_id=:t"), {"t": cls.team})
        cls.db.execute(text("DELETE FROM teams WHERE id=:t"), {"t": cls.team})
        cls.db.execute(text("DELETE FROM organization_members WHERE organization_id=:o"), {"o": cls.org})
        cls.db.execute(text("DELETE FROM organizations WHERE id=:o"), {"o": cls.org})
        cls.db.commit()
        cls.db.close()

    def setUp(self):
        self.db.commit()
        self.db.execute(text("DELETE FROM attendance_anomaly WHERE organization_id=:o"), {"o": self.org})
        self.db.commit()
        patcher = patch("service.tools.attendance.hub.resolve_caller_context", return_value={"team_id": self.team})
        patcher.start()
        self.addCleanup(patcher.stop)

    def add(self, user, kind="late", status="open", days_ago=1, explanation=None):
        day = datetime.combine((utcnow() - timedelta(days=days_ago)).date(), datetime.min.time())
        self.db.add(AttendanceAnomaly(organization_id=self.org, team_id=self.team, user_id=user["id"], work_date=day, type=kind, severity="medium",
                                      detail_json=json.dumps({"minutes": 30, "punches": ["09:30", "18:00"]}), status=status, explanation=explanation))
        self.db.commit()

    def run_tool(self, cls, user, **kwargs):
        return json.loads(make(cls, user).execute(**kwargs))

    def test_employee_sees_only_own_pending_anomalies(self):
        self.add(self.emp, explanation=None)
        self.add(self.emp, kind="absent", days_ago=2, status="confirmed")
        self.add(self.other, kind="absent")
        result = self.run_tool(tools.GetMyAttendanceAnomaliesTool, self.emp)
        self.assertEqual([(a["type"], a["status"]) for a in result["anomalies"]], [("迟到", "待说明")])
        everything = self.run_tool(tools.GetMyAttendanceAnomaliesTool, self.emp, only_pending=False)
        self.assertEqual(len(everything["anomalies"]), 2)

    def test_nothing_pending_says_so(self):
        result = self.run_tool(tools.GetMyAttendanceAnomaliesTool, self.emp)
        self.assertEqual(result["anomalies"], [])

    def test_summary_is_for_heads_only_and_has_no_names(self):
        self.add(self.emp)
        self.add(self.other, kind="absent", status="explained", explanation="外出")
        denied = self.run_tool(tools.GetAttendanceSummaryTool, self.emp)
        self.assertIn("只有人事或部门负责人", denied["error"])
        result = self.run_tool(tools.GetAttendanceSummaryTool, self.head)
        self.assertEqual(result["by_type"], {"late": 1, "absent": 1})
        self.assertEqual((result["people_affected"], result["waiting_decision"]), (2, 1))
        self.assertNotIn(self.emp["name"], json.dumps(result, ensure_ascii=False))
        self.assertIn("共 2 条考勤异常", result["narrative"])

    def test_summary_rejects_a_bad_range(self):
        self.assertIn("日期", self.run_tool(tools.GetAttendanceSummaryTool, self.head, start="2026-10-10", end="2026-10-01")["error"])
        self.assertIn("格式", self.run_tool(tools.GetAttendanceSummaryTool, self.head, start="昨天")["error"])

    def test_user_without_department_gets_a_clear_message(self):
        with patch("service.tools.attendance.hub.resolve_caller_context", return_value={"team_id": None}):
            self.assertIn("不属于任何部门", self.run_tool(tools.GetMyAttendanceAnomaliesTool, self.emp)["error"])


if __name__ == "__main__":
    unittest.main()
