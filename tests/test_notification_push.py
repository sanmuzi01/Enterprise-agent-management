"""站内通知推送到飞书 / 钉钉：通知和推送事件同一事务写入、发送失败进入重试、平台恢复后补发、重试用尽进死信、
死信重新投递后送达；已读 / 解绑 / 免打扰时段不再推送。不连真实飞书（发送用 mock），发件箱用真实 MySQL。"""
import json
import unittest
import uuid
from datetime import timedelta
from unittest.mock import patch

from tests import _route_client as rc  # 先导入：测试用的异步连接不复用（必须在导入 models 之前）

from sqlalchemy import text

from models.init_db import SessionLocal
from service.events import handlers as _handlers  # noqa: F401  —— 注册 external_notifier
from service.events import outbox, runner
from service.integrations.base import IntegrationError
from tests._async_helpers import run_async
from tests.test_integrations import fake_app
from utils.timeutil import utcnow

_AVAILABLE, _WHY = rc.route_tests_available()
TOPIC = "notification.command.v1"


@unittest.skipUnless(_AVAILABLE, _WHY)
class NotificationPushTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from service.integrations import apps, identity
        from tests.test_enterprise_access import _add_org_member
        cls.db = SessionLocal()
        if cls.db.execute(text("SELECT COUNT(*) FROM collaboration_app")).scalar():
            cls.db.close()
            raise unittest.SkipTest("库里已经有真实的飞书 / 钉钉配置，不在这个库上跑（不覆盖真实配置）")
        cls.admin = rc.create_user("push-admin")
        cls.bound = rc.create_user("push-bound")
        cls.unbound = rc.create_user("push-unbound")
        cls.feishu = fake_app("feishu")
        apps.save_sync(cls.db, "feishu", cls.admin["id"], app_id=cls.feishu.app_id, app_secret="s", verification_token="vtoken",
                       encrypt_key=cls.feishu.encrypt_key, robot_code=None, card_template_id=None, enabled=True)
        org = apps.enterprise_id_sync(cls.db)
        for user in (cls.bound, cls.unbound):
            _add_org_member(cls.db, org, user["id"], "member")
        identity.bind_sync(cls.db, cls.admin["id"], org, "feishu", cls.feishu.app_id, "ou_push", cls.bound["id"])

    @classmethod
    def tearDownClass(cls):
        cls.cleanup_rows()
        cls.db.execute(text("DELETE FROM external_user_binding WHERE external_tenant_id=:t"), {"t": cls.feishu.app_id})
        cls.db.execute(text("DELETE FROM collaboration_app WHERE app_id=:t"), {"t": cls.feishu.app_id})
        cls.db.commit()
        rc.cleanup()
        cls.db.close()

    @classmethod
    def cleanup_rows(cls):
        cls.db.rollback()
        users = {"a": cls.bound["id"], "b": cls.unbound["id"]}
        ids = [r[0] for r in cls.db.execute(text("SELECT event_id FROM outbox_event WHERE topic=:t AND aggregate_id IN "
                                                 "(SELECT CAST(id AS CHAR) FROM notification WHERE user_id IN (:a, :b))"),
                                            {"t": TOPIC, **users}).all()]
        for table in ("consumer_inbox", "consumer_retry", "dead_letter", "outbox_event"):
            for event_id in ids:
                cls.db.execute(text(f"DELETE FROM {table} WHERE event_id=:e"), {"e": event_id})
        cls.db.execute(text("DELETE FROM notification WHERE user_id IN (:a, :b)"), users)
        cls.db.execute(text("DELETE FROM notification_preference WHERE user_id IN (:a, :b)"), users)
        cls.db.commit()

    def setUp(self):
        self.cleanup_rows()
        self.sent = []
        self.failing = False

        def send(app, context, words):
            if self.failing:
                raise IntegrationError("飞书返回错误 99991400：请求过于频繁")
            self.sent.append((context, words))
        for p in (patch("service.integrations.feishu.messages.send_text", side_effect=send),
                  patch("service.observability.issues.record_occurrence"),            # 死信会记进问题中心：这里不写
                  patch.dict("os.environ", {"INTEGRATION_WEB_BASE_URL": "https://agent.example.test"})):
            p.start()
            self.addCleanup(p.stop)

    # -------- 工具

    def notify(self, user, title="报销单待你审批", key=None):
        from models.async_db import AsyncSessionLocal
        from service import notification_center

        async def go():
            async with AsyncSessionLocal() as db:
                return await notification_center.notify(db, user["id"], "approval", title, body="张三的差旅报销 1,280 元",
                                                        link="/department", dedupe_key=key or uuid.uuid4().hex)
        return run_async(go())

    def events(self, user):
        self.db.commit()
        return self.db.execute(text("SELECT e.event_id, e.payload_json FROM outbox_event e JOIN notification n "
                                    "ON e.aggregate_id = CAST(n.id AS CHAR) WHERE e.topic=:t AND n.user_id=:u ORDER BY e.id"),
                               {"t": TOPIC, "u": user["id"]}).all()

    def deliver(self, event_id):
        """让 external_notifier 处理一次（和后台运行器同一个入口：领取租约 → 处理 → 成功 / 记失败 / 进死信）。"""
        from models.init_db import OutboxEvent
        self.db.commit()
        row = self.db.query(OutboxEvent).filter_by(event_id=event_id).one()
        stats = {"done": 0, "retry": 0, "dead": 0, "skipped": 0}
        envelope = outbox.envelope(row)
        self.db.commit()                                            # 结束读快照，后面才看得到处理结果
        return run_async(runner._deliver(runner.REGISTRY["external_notifier"], envelope, stats))

    def due_now(self, event_id):
        """跳过退避等待（测试里不真的等几分钟）。"""
        self.db.execute(text("UPDATE consumer_retry SET next_attempt_at=:t WHERE event_id=:e"),
                        {"t": utcnow() - timedelta(seconds=5), "e": event_id})
        self.db.commit()

    # -------- 生产

    def test_push_event_written_with_notification_only_for_bound_users(self):
        self.assertTrue(self.notify(self.bound))
        self.assertTrue(self.notify(self.unbound))
        [(event_id, body)] = self.events(self.bound)
        payload = json.loads(body)
        self.assertEqual((payload["provider"], payload["user_id"]), ("feishu", self.bound["id"]))
        self.assertNotIn("报销", body)                              # 事件里只放编号，不放通知正文
        self.assertEqual(self.events(self.unbound), [])
        self.assertFalse(self.notify(self.bound, key="same") and self.notify(self.bound, key="same"))
        self.assertEqual(len(self.events(self.bound)), 2)           # 去重掉的通知不会多出推送

    def test_quiet_hours_do_not_push(self):
        self.db.execute(text("INSERT INTO notification_preference (user_id, muted_categories, quiet_start, quiet_end, push_external, updated_at) "
                             "VALUES (:u, '[]', '00:00', '23:59', 0, NOW())"), {"u": self.bound["id"]})
        self.db.commit()
        self.notify(self.bound)
        self.assertEqual(self.events(self.bound), [])

    # -------- 消费

    def test_delivered_to_own_feishu(self):
        self.notify(self.bound)
        [(event_id, _)] = self.events(self.bound)
        self.assertEqual(self.deliver(event_id), "done")
        [(context, words)] = self.sent
        self.assertEqual(context, {"open_id": "ou_push"})
        self.assertIn("【报销单待你审批】", words)
        self.assertIn("https://agent.example.test/department", words)
        self.assertNotIn("补发", words)
        self.assertEqual(self.deliver(event_id), "skipped")          # 重复投递：收件箱挡住，不会再发

    def test_send_failure_goes_to_retry_and_resends_after_recovery(self):
        self.notify(self.bound)
        [(event_id, _)] = self.events(self.bound)
        self.failing = True
        self.assertEqual(self.deliver(event_id), "retry")
        self.assertEqual(self.sent, [])
        attempts, wait_until, error = self.db.execute(text(
            "SELECT attempts, next_attempt_at, last_error FROM consumer_retry WHERE event_id=:e AND consumer='external_notifier'"),
            {"e": event_id}).one()
        self.db.commit()
        self.assertEqual(attempts, 1)
        self.assertGreater(wait_until, utcnow())                    # 退避：不会立刻再打平台
        self.assertIn("请求过于频繁", error)
        self.assertEqual(self.deliver(event_id), "skipped")          # 还没到重试时间
        count = self.db.execute(text("SELECT COUNT(*) FROM notification WHERE user_id=:u"), {"u": self.bound["id"]}).scalar()
        self.db.commit()
        self.assertEqual(count, 1)                                  # 平台不可用不影响站内通知
        self.failing = False                                        # 平台恢复
        self.db.execute(text("UPDATE notification SET created_at=:t WHERE user_id=:u"),
                        {"t": utcnow() - timedelta(hours=1), "u": self.bound["id"]})
        self.due_now(event_id)
        self.assertEqual(self.deliver(event_id), "done")
        self.assertIn("平台恢复后补发", self.sent[-1][1])              # 晚到的消息注明原通知时间

    def test_dead_letter_can_be_redelivered(self):
        from service.integrations import notify_push
        self.notify(self.bound)
        [(event_id, _)] = self.events(self.bound)
        self.failing = True
        results = []
        for _ in range(notify_push.MAX_ATTEMPTS):
            results.append(self.deliver(event_id))
            self.due_now(event_id)
        self.assertEqual(results[-1], "dead")
        self.assertEqual(results[:-1], ["retry"] * (notify_push.MAX_ATTEMPTS - 1))
        dead_id, status = self.db.execute(text("SELECT id, status FROM dead_letter WHERE event_id=:e AND consumer='external_notifier'"),
                                          {"e": event_id}).one()
        self.db.commit()
        self.assertEqual(status, "pending")
        from service.integrations.dispatcher import health_sync
        self.assertEqual(health_sync(self.db, "feishu")["push"]["dead"], 1)      # 管理员在接入页能看到
        self.db.commit()
        self.assertNotIn(event_id, [e["event_id"] for e in outbox.fetch_deliverable("external_notifier", [TOPIC], limit=500)])

        self.failing = False                                        # 管理员修好了（比如重新配置了凭证）
        outbox.redeliver(self.db, dead_id, self.admin["id"])
        self.assertEqual(self.sent, [])
        published = outbox.publish_pending(outbox.db_transport, limit=500)
        self.assertGreaterEqual(published.get("sent", 0), 1)
        self.assertIn(event_id, [e["event_id"] for e in outbox.fetch_deliverable("external_notifier", [TOPIC], limit=500)])
        self.assertEqual(self.deliver(event_id), "done")
        self.assertEqual(len(self.sent), 1)

    def test_not_sent_when_already_read_or_unbound(self):
        self.notify(self.bound)
        self.notify(self.bound)
        [(first, _), (second, _)] = self.events(self.bound)
        self.db.execute(text("UPDATE notification SET read_at=:t WHERE CAST(id AS CHAR)=(SELECT aggregate_id FROM outbox_event WHERE event_id=:e)"),
                        {"t": utcnow(), "e": first})
        self.db.execute(text("UPDATE external_user_binding SET status='disabled' WHERE external_user_id='ou_push' AND external_tenant_id=:t"),
                        {"t": self.feishu.app_id})
        self.db.commit()
        try:
            self.assertEqual(self.deliver(first), "done")           # 已在网页上读过
            self.assertEqual(self.deliver(second), "done")          # 绑定停用了
        finally:
            self.db.execute(text("UPDATE external_user_binding SET status='active' WHERE external_user_id='ou_push' AND external_tenant_id=:t"),
                            {"t": self.feishu.app_id})
            self.db.commit()
        self.assertEqual(self.sent, [])


if __name__ == "__main__":
    unittest.main()
