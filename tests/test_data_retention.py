"""数据保留策略（service/data_retention.py）：到期的过程记录分批清理，进行中的、未读的、待处理的不动，永远保留的表不在任何策略里。

测试数据的时间放在 2001 年、清理时把“现在”设成 2002-01-01：截止时间也落在 2001 年，开发库里真实的数据不会被波及。
"""
import os
import unittest
from datetime import datetime
from unittest import mock

from sqlalchemy import text

from tests import _route_client as rc

_AVAILABLE, _WHY = rc.route_tests_available()

NOW = datetime(2002, 1, 1)
OLD = datetime(2001, 1, 1)       # 早于任何一类的截止时间（最长 180 天 → 2001-07-05）
RECENT = datetime(2001, 12, 20)  # 晚于默认天数下任何一类的截止时间（最短的操作日志 90 天 → 2001-10-03）
MARK = "rt-retention"


class RetentionConfigTest(unittest.TestCase):
    def policy(self):
        from service.data_retention import POLICIES
        return next(p for p in POLICIES if p.name == "operation_logs")

    def test_zero_disables_and_small_values_are_raised_to_the_minimum(self):
        p = self.policy()
        with mock.patch.dict(os.environ, {p.env: "0"}):
            self.assertEqual(p.days(), 0)
        with mock.patch.dict(os.environ, {p.env: "1"}):
            self.assertEqual(p.days(), 7, "配成 1 天多半是手误，按最少 7 天算，不能把近期数据删光")
        with mock.patch.dict(os.environ, {p.env: "abc"}):
            self.assertEqual(p.days(), p.default_days)
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(p.env, None)
            self.assertEqual(p.days(), 90)

    def test_audit_issue_and_conversation_tables_are_never_purged(self):
        from service.data_retention import NEVER_PURGED, POLICIES
        statements = " ".join([p.select_sql + " " + " ".join(p.delete_sqls) for p in POLICIES]).lower()
        for table in ("audit_event", "kb_audit_log", "system_issue", "issue_event", "conversation", "message ", "approval_request"):
            self.assertNotIn(f"from {table.strip()} ", statements + " ", f"{table} 不能出现在任何清理语句里")
        self.assertIn("audit_event", NEVER_PURGED)

    def test_scheduled_run_is_off_by_default(self):
        from service import data_retention
        with mock.patch.dict(os.environ, {}, clear=False), mock.patch.object(data_retention, "apply") as apply:
            os.environ.pop("DATA_RETENTION_AUTO", None)
            self.assertEqual(data_retention.run_scheduled(), {})
            apply.assert_not_called()


@unittest.skipUnless(_AVAILABLE, f"需要本地 MySQL：{_WHY}")
class RetentionApplyTest(unittest.TestCase):
    def setUp(self):
        from models.init_db import Agent, SessionLocal
        self.SessionLocal = SessionLocal
        self.user = rc.create_user("retention")
        env = mock.patch.dict(os.environ)   # 测试结束恢复环境变量
        env.start()
        self.addCleanup(env.stop)
        for name in ("AGENT_RUN_RETENTION_DAYS", "OPERATION_LOG_RETENTION_DAYS", "NOTIFICATION_RETENTION_DAYS",
                     "BACKGROUND_TASK_RETENTION_DAYS", "DEAD_LETTER_RETENTION_DAYS"):
            os.environ.pop(name, None)   # 用默认天数
        with SessionLocal() as db:
            agent = Agent(user_id=self.user["id"], name="rt-保留策略助手", model_name="glm-4")
            db.add(agent)
            db.commit()
            self.agent_id = agent.id

    def tearDown(self):
        with self.SessionLocal() as db:
            uid = self.user["id"]
            db.execute(text("DELETE FROM agent_step WHERE run_id IN (SELECT id FROM agent_run WHERE user_id = :u)"), {"u": uid})
            db.execute(text("DELETE FROM agent_run WHERE user_id = :u"), {"u": uid})
            db.execute(text("DELETE FROM operation_log WHERE path = :m"), {"m": MARK})
            db.execute(text("DELETE FROM notification WHERE user_id = :u"), {"u": uid})
            db.execute(text("UPDATE background_task SET parent_task_id = NULL WHERE user_id = :u"), {"u": uid})
            db.execute(text("DELETE FROM background_task WHERE user_id = :u"), {"u": uid})
            db.execute(text("DELETE FROM dead_letter WHERE consumer = :m"), {"m": MARK})
            db.commit()
        rc.cleanup()

    def _insert(self, db, sql, **params):
        db.execute(text(sql), params)
        return db.execute(text("SELECT LAST_INSERT_ID()")).scalar()

    def _seed(self):
        uid, aid = self.user["id"], self.agent_id
        with self.SessionLocal() as db:
            ids = {}
            run_sql = ("INSERT INTO agent_run (user_id, agent_id, user_message, status, started_at) "
                       "VALUES (:u, :a, 'q', :s, :t)")
            ids["old_run"] = self._insert(db, run_sql, u=uid, a=aid, s="finished", t=OLD)
            ids["old_running"] = self._insert(db, run_sql, u=uid, a=aid, s="running", t=OLD)
            ids["recent_run"] = self._insert(db, run_sql, u=uid, a=aid, s="failed", t=RECENT)
            for run in (ids["old_run"], ids["recent_run"]):
                self._insert(db, "INSERT INTO agent_step (run_id, step_no, step_type, created_at) VALUES (:r, 1, 'tool', :t)",
                             r=run, t=OLD)
            log_sql = "INSERT INTO operation_log (user_id, method, path, status_code, created_at) VALUES (:u, 'GET', :m, 200, :t)"
            ids["old_log"] = self._insert(db, log_sql, u=uid, m=MARK, t=OLD)
            ids["recent_log"] = self._insert(db, log_sql, u=uid, m=MARK, t=RECENT)
            note_sql = ("INSERT INTO notification (user_id, category, title, dedupe_key, read_at, created_at) "
                        "VALUES (:u, 'system', 't', :k, :r, :t)")
            ids["old_read"] = self._insert(db, note_sql, u=uid, k="rt-1", r=OLD, t=OLD)
            ids["old_unread"] = self._insert(db, note_sql, u=uid, k="rt-2", r=None, t=OLD)
            task_sql = ("INSERT INTO background_task (user_id, task_type, status, title, created_at, finished_at, parent_task_id, retry_count) "
                        "VALUES (:u, 'rt', :s, 't', :c, :f, :p, 0)")
            ids["old_task"] = self._insert(db, task_sql, u=uid, s="failed", c=OLD, f=OLD, p=None)
            ids["retry_task"] = self._insert(db, task_sql, u=uid, s="finished", c=RECENT, f=RECENT, p=ids["old_task"])
            ids["old_queued"] = self._insert(db, task_sql, u=uid, s="queued", c=OLD, f=None, p=None)
            dl_sql = ("INSERT INTO dead_letter (event_id, consumer, topic, event_type, payload_json, attempts, status, handled_at, created_at) "
                      "VALUES (:e, :m, 't', 't', '{}', 3, :s, :h, :c)")
            ids["old_handled_dl"] = self._insert(db, dl_sql, e="rt-e1", m=MARK, s="discarded", h=OLD, c=OLD)
            ids["old_pending_dl"] = self._insert(db, dl_sql, e="rt-e2", m=MARK, s="pending", h=None, c=OLD)
            db.commit()
        return ids

    def _exists(self, db, table, row_id):
        return db.execute(text(f"SELECT COUNT(*) FROM {table} WHERE id = :i"), {"i": row_id}).scalar() == 1

    def test_only_expired_and_finished_records_are_purged_and_audited(self):
        from service import data_retention
        ids = self._seed()
        with self.SessionLocal() as db:
            due = {r["name"]: r["due"] for r in data_retention.plan(db, now=NOW)}
        self.assertEqual(due, {"agent_runs": 1, "operation_logs": 1, "read_notifications": 1,
                               "finished_tasks": 1, "handled_dead_letters": 1}, "只看不删时要准确报出到期条数")
        with self.SessionLocal() as db, mock.patch("service.audit_service.record") as audit:
            result = data_retention.apply(db, now=NOW, operator_id=self.user["id"])
        self.assertEqual(result, due)
        audit.assert_called_once()
        self.assertEqual(audit.call_args.args[:2], (self.user["id"], "data_retention.applied"))
        with self.SessionLocal() as db:
            gone = ["agent_run:old_run", "operation_log:old_log", "notification:old_read",
                    "background_task:old_task", "dead_letter:old_handled_dl"]
            kept = ["agent_run:old_running", "agent_run:recent_run", "operation_log:recent_log",
                    "notification:old_unread", "background_task:retry_task", "background_task:old_queued",
                    "dead_letter:old_pending_dl"]
            for item in gone:
                table, key = item.split(":")
                self.assertFalse(self._exists(db, table, ids[key]), f"{item} 到期应删除")
            for item in kept:
                table, key = item.split(":")
                self.assertTrue(self._exists(db, table, ids[key]), f"{item} 不该删除")
            self.assertEqual(db.execute(text("SELECT COUNT(*) FROM agent_step WHERE run_id = :r"), {"r": ids["old_run"]}).scalar(), 0,
                             "运行轨迹的步骤要跟着删")
            self.assertEqual(db.execute(text("SELECT COUNT(*) FROM agent_step WHERE run_id = :r"), {"r": ids["recent_run"]}).scalar(), 1)
            self.assertIsNone(db.execute(text("SELECT parent_task_id FROM background_task WHERE id = :i"),
                                         {"i": ids["retry_task"]}).scalar(), "重试出来的新任务只断开指向，不跟着删")

        with self.SessionLocal() as db, mock.patch("service.audit_service.record") as audit:
            self.assertEqual(data_retention.apply(db, now=NOW), {}, "再跑一次没有可删的")
            audit.assert_not_called()

    def test_deletes_in_batches_and_a_disabled_category_is_left_alone(self):
        from service import data_retention
        with self.SessionLocal() as db:
            for _ in range(5):
                self._insert(db, "INSERT INTO operation_log (user_id, method, path, status_code, created_at) "
                                 "VALUES (:u, 'GET', :m, 200, :t)", u=self.user["id"], m=MARK, t=OLD)
            db.commit()
        count = "SELECT COUNT(*) FROM operation_log WHERE path = :m"
        with mock.patch.dict(os.environ, {"OPERATION_LOG_RETENTION_DAYS": "0"}), self.SessionLocal() as db, \
                mock.patch("service.audit_service.record"):
            self.assertNotIn("operation_logs", data_retention.apply(db, now=NOW, only=["operation_logs"]))
            self.assertEqual(db.execute(text(count), {"m": MARK}).scalar(), 5, "配成 0 天表示这一类不清理")
        with self.SessionLocal() as db, mock.patch("service.audit_service.record"):
            self.assertEqual(data_retention.apply(db, now=NOW, batch_size=2, max_batches=1, only=["operation_logs"]),
                             {"operation_logs": 2}, "每批 2 条、只跑 1 批")
            self.assertEqual(data_retention.apply(db, now=NOW, batch_size=2, only=["operation_logs"]), {"operation_logs": 3},
                             "不限批数时分批删完")
            self.assertEqual(db.execute(text(count), {"m": MARK}).scalar(), 0)


if __name__ == "__main__":
    unittest.main()
