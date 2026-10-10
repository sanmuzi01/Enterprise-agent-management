"""批量整理：后台依次整理、进度、单份失败与重试、幂等、限制、权限、中断恢复、并发上限（模型与业务系统用替身，数据库为真实测试库）。"""
import asyncio
import json
import unittest
import uuid
from datetime import timedelta
from unittest.mock import AsyncMock, patch

from tests import _route_client as rc  # 先导入：测试用的异步连接不复用（必须在导入 models 之前）

from sqlalchemy import text

from models.init_db import SessionLocal
from service import automation_batch_service as batches
from service import automation_work_service as works
from service.events import handlers as _handlers  # noqa: F401  —— 注册事件消费者
from service.events import outbox, runner
from tests._async_helpers import run_async
from service.exceptions import QuotaExceeded
from tests.test_enterprise_access import _add_org_member, _add_team_member, _create_org, _create_team
from utils.timeutil import utcnow

_AVAILABLE, _WHY = rc.route_tests_available()


def answer_for(source: str) -> str:
    marker = "我要请年假"
    return json.dumps({"leave_type_code": "annual", "start_date": None, "end_date": None, "reason": "",
                       "evidence": marker if marker in source else source[:10], "warnings": ["日期不明确"]}, ensure_ascii=False)


@unittest.skipUnless(_AVAILABLE, _WHY)
class BatchTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = SessionLocal()
        cls.client = rc.make_client()
        cls.owner = rc.create_user("bat-owner")
        cls.emp = rc.create_user("bat-emp")
        cls.other = rc.create_user("bat-oth")
        cls.out = rc.create_user("bat-out")
        cls.org = _create_org(cls.db, "bat-org-" + uuid.uuid4().hex[:6], cls.owner["id"])
        cls.team = _create_team(cls.db, cls.org, "bat-team", cls.owner["id"])
        cls.db.commit()
        for user in (cls.emp, cls.other):
            _add_org_member(cls.db, cls.org, user["id"], "member")
            _add_team_member(cls.db, cls.team, user["id"], "member")

    @classmethod
    def tearDownClass(cls):
        rc.cleanup()
        cls.db.execute(text("DELETE FROM automation_work WHERE team_id=:t"), {"t": cls.team})
        cls.db.execute(text("DELETE FROM team_members WHERE team_id=:t"), {"t": cls.team})
        cls.db.execute(text("DELETE FROM teams WHERE id=:t"), {"t": cls.team})
        cls.db.execute(text("DELETE FROM organization_members WHERE organization_id=:o"), {"o": cls.org})
        cls.db.execute(text("DELETE FROM organizations WHERE id=:o"), {"o": cls.org})
        cls.db.commit()
        cls.db.close()

    def setUp(self):
        self.db.commit()
        self.db.execute(text("DELETE FROM automation_work WHERE team_id=:t"), {"t": self.team})
        self.db.execute(text("DELETE FROM outbox_event WHERE topic='automation.job.v1'"))
        for table in ("consumer_inbox", "consumer_retry", "dead_letter"):
            self.db.execute(text(f"DELETE FROM {table} WHERE consumer='batch_item_worker'"))
        self.db.execute(text("DELETE FROM system_issue WHERE error_code='KAFKA_CONSUME_FAILED' AND operation LIKE '消费者 batch_item_worker%'"))
        self.db.commit()
        env = patch.dict("os.environ", {"AUTOMATION_BATCH_RATE_LIMIT": "1000"})
        env.start()
        self.addCleanup(env.stop)
        self.calls = []
        self.fail_on = set()
        self.running = self.peak = 0

        async def chat(db, user_id, model, system, history, source, temperature=0):
            self.calls.append(source)
            self.running += 1
            self.peak = max(self.peak, self.running)
            await asyncio.sleep(0.05)
            self.running -= 1
            if any(marker in source for marker in self.fail_on):
                return "这不是 JSON", {"total_tokens": 1}
            return answer_for(source), {"total_tokens": 10}

        for p in (patch.object(works, "async_chat_with_usage", side_effect=chat),
                  patch.object(works, "run_checks", AsyncMock(return_value=[])),
                  patch.object(batches, "async_get_api_config", AsyncMock(return_value={"api_key": "t"})),
                  patch.object(batches, "enforce_quota_async", AsyncMock())):
            p.start()
            self.addCleanup(p.stop)

    def drain(self):
        """跑事件运行器直到没有事情可做（线上由 lifespan 里的后台循环做同样的事）。"""
        for _ in range(8):
            result = run_async(runner.run_cycle())
            self.cycles = getattr(self, "cycles", []) + [result]
            if not (result["published"]["sent"] or result["consumed"]["done"] or result["consumed"]["retry"] or result["consumed"]["dead"]):
                break

    def body(self, texts=None, **over):
        texts = [f"第{i}份材料：我要请年假，家里有事" for i in range(1, 4)] if texts is None else texts
        data = {"batch_id": str(uuid.uuid4()), "team_id": self.team, "kind": "leave", "model_name": "test-model",
                "items": [{"name": f"文件{i}.docx", "text": t} for i, t in enumerate(texts, start=1)]}
        data.update(over)
        return data

    def create(self, user=None, drain=True, **over):
        response = self.client.post("/enterprise/automation/batches", json=self.body(**over), headers=(user or self.emp)["headers"])
        if drain and response.status_code == 200:
            self.drain()
        return response

    def get(self, batch_id, user=None):
        return self.client.get(f"/enterprise/automation/batches/{batch_id}", headers=(user or self.emp)["headers"])

    # ---- 正常流程 ----

    def test_items_are_processed_in_the_background_with_progress(self):
        response = self.create(drain=False)
        self.assertEqual(response.status_code, 200, response.text)
        first = response.json()
        self.assertEqual((first["total"], first["kind"]), (3, "leave"))
        self.assertEqual([i["status"] for i in first["items"]], ["queued"] * 3)      # 创建时立即返回，全部排队中
        self.assertFalse(self.calls)
        self.drain()
        done = self.get(first["batch_id"]).json()
        self.assertTrue(done["finished"])
        self.assertEqual((done["done"], done["counts"]["ready"]), (3, 3))
        self.assertEqual([i["batch_name"] for i in done["items"]], ["文件1.docx", "文件2.docx", "文件3.docx"])
        for item in done["items"]:
            self.assertIn("我要请年假", item["proposal"]["evidence"])
        self.assertEqual(len(self.calls), 3)

    def test_each_item_is_an_ordinary_work_that_can_be_opened_and_saved(self):
        batch = self.create().json()
        item = self.get(batch["batch_id"]).json()["items"][0]
        detail = self.client.get(f"/enterprise/automation/{item['id']}", headers=self.emp["headers"]).json()
        self.assertEqual((detail["status"], detail["batch_id"]), ("ready", batch["batch_id"]))
        history = self.client.get(f"/enterprise/automation?team_id={self.team}", headers=self.emp["headers"]).json()
        self.assertEqual(history["stats"]["ready"], 3)

    def test_at_most_two_items_call_the_model_at_once(self):
        self.create(texts=[f"第{i}份：我要请年假，原因{i}" for i in range(6)])
        self.assertEqual(len(self.calls), 6, self.cycles)
        self.assertLessEqual(self.peak, batches.CONCURRENCY)
        self.assertGreaterEqual(self.peak, 2)

    # ---- 单份失败、重试 ----

    def test_one_failure_does_not_stop_the_others_and_can_be_retried(self):
        self.fail_on = {"坏材料"}
        batch = self.create(texts=["第一份：我要请年假，家里有事", "坏材料：我要请年假，但模型答不出来", "第三份：我要请年假，出去旅游"]).json()
        done = self.get(batch["batch_id"]).json()
        self.assertEqual([i["status"] for i in done["items"]], ["ready", "failed", "ready"])
        self.assertTrue(done["finished"])
        self.assertIn("结构化结果", done["items"][1]["error_message"])
        bad = done["items"][1]
        self.fail_on = set()
        retried = self.client.post(f"/enterprise/automation/batches/{batch['batch_id']}/items/{bad['id']}/retry", headers=self.emp["headers"])
        self.assertEqual(retried.status_code, 200, retried.text)
        self.assertEqual(retried.json()["items"][1]["status"], "queued")
        self.drain()
        final = self.get(batch["batch_id"]).json()
        self.assertEqual(final["counts"]["ready"], 3)
        # 成功的不能被重试覆盖
        ok = final["items"][0]
        again = self.client.post(f"/enterprise/automation/batches/{batch['batch_id']}/items/{ok['id']}/retry", headers=self.emp["headers"])
        self.assertEqual(again.status_code, 409)

    def test_quota_exhaustion_fails_only_the_items_it_hits(self):
        calls = {"n": 0}

        async def quota(db, user_id):
            calls["n"] += 1
            if calls["n"] == 3:   # 第 1 次是创建批次时的预检，第 2、3 份材料里第 2 份用尽
                raise QuotaExceeded("今日额度已用完")
        with patch.object(batches, "enforce_quota_async", side_effect=quota):
            batch = self.create().json()
        done = self.get(batch["batch_id"]).json()
        statuses = sorted(i["status"] for i in done["items"])
        self.assertEqual(statuses, ["failed", "ready", "ready"])
        self.assertIn("额度", next(i for i in done["items"] if i["status"] == "failed")["error_message"])

    # ---- 幂等与限制 ----

    def test_resubmitting_the_same_batch_does_not_process_again(self):
        data = self.body()
        first = self.client.post("/enterprise/automation/batches", json=data, headers=self.emp["headers"])
        again = self.client.post("/enterprise/automation/batches", json=data, headers=self.emp["headers"])
        self.drain()
        self.drain()
        self.assertEqual(again.status_code, 200)
        self.assertEqual([i["id"] for i in again.json()["items"]], [i["id"] for i in first.json()["items"]])
        self.assertEqual(len(self.calls), 3)

    def test_validation(self):
        self.assertEqual(self.create(texts=[]).status_code, 422)
        self.assertEqual(self.create(texts=["x" * 11] * 11).status_code, 422)
        self.assertEqual(self.create(texts=["太短"]).status_code, 400)
        self.assertEqual(self.create(texts=["字" * 15000] * 5).status_code, 400)          # 合计超 6 万字
        self.assertEqual(self.create(kind="crm").status_code, 403)                          # 销售专属；本部门不是销售
        self.assertEqual(self.create(sensitivity="restricted").status_code, 403)
        with patch.object(batches, "async_get_api_config", AsyncMock(return_value=None)):   # 没有连接所选模型
            self.assertEqual(self.create().status_code, 400)
        self.assertEqual(self.client.post("/enterprise/automation/batches", json={**self.body(), "extra": 1}, headers=self.emp["headers"]).status_code, 422)
        self.assertFalse(self.calls)

    def test_customer_bound_workflows_are_not_batchable(self):
        self.db.execute(text("UPDATE teams SET department_code='sales' WHERE id=:t"), {"t": self.team})
        self.db.commit()
        try:
            response = self.create(kind="crm")
        finally:
            self.db.execute(text("UPDATE teams SET department_code=NULL WHERE id=:t"), {"t": self.team})
            self.db.commit()
        self.assertEqual(response.status_code, 400)
        self.assertIn("暂不支持批量整理", response.json()["detail"])

    def test_only_one_batch_per_user_at_a_time(self):
        self.create()
        self.db.execute(text("UPDATE automation_work SET status='queued' WHERE team_id=:t AND user_id=:u LIMIT 1"),
                        {"t": self.team, "u": self.emp["id"]})
        self.db.commit()
        busy = self.create()
        self.assertEqual(busy.status_code, 409)
        self.assertIn("还在整理中", busy.json()["detail"])
        self.assertEqual(self.create(user=self.other).status_code, 200)                    # 别人不受影响

    # ---- 权限与恢复 ----

    def test_only_the_owner_sees_a_batch_and_members_only_can_create(self):
        batch = self.create().json()
        self.assertEqual(self.get(batch["batch_id"], user=self.other).status_code, 404)
        self.assertEqual(self.client.post("/enterprise/automation/batches", json=self.body(), headers=self.out["headers"]).status_code, 403)
        self.assertEqual(self.client.get("/enterprise/automation/batches", params={"team_id": self.team}, headers=self.out["headers"]).status_code, 403)
        retry = self.client.post(f"/enterprise/automation/batches/{batch['batch_id']}/items/{uuid.uuid4()}/retry", headers=self.other["headers"])
        self.assertEqual(retry.status_code, 404)

    def test_revoked_membership_fails_the_items_that_were_still_queued(self):
        async def revoked(db, user_id, team_id, kind=None):
            from service.exceptions import PermissionDenied
            raise PermissionDenied("身份失效")
        batch = self.create(drain=False).json()
        with patch.object(batches, "authorize", side_effect=revoked):     # 材料排队期间，成员身份被撤销
            self.drain()
        done = self.get(batch["batch_id"]).json()
        self.assertEqual(done["counts"]["failed"], 3)
        self.assertIn("身份已失效", done["items"][0]["error_message"])

    def test_stuck_queued_items_become_failed_after_the_process_was_restarted(self):
        batch = self.create().json()
        self.db.execute(text("UPDATE automation_work SET status='queued', updated_at=:t WHERE batch_id=:b"),
                        {"b": batch["batch_id"], "t": utcnow() - timedelta(minutes=90)})
        self.db.commit()
        done = self.get(batch["batch_id"]).json()
        self.assertTrue(done["finished"])
        self.assertEqual(done["counts"]["failed"], 3)
        self.assertIn("中断", done["items"][0]["error_message"])

    def test_listing_recent_batches_for_resuming_after_leaving_the_page(self):
        first = self.create().json()
        listed = self.client.get("/enterprise/automation/batches", params={"team_id": self.team}, headers=self.emp["headers"]).json()
        self.assertEqual(listed[0]["batch_id"], first["batch_id"])
        self.assertEqual((listed[0]["total"], listed[0]["active"], listed[0]["finished"]), (3, 0, True))
        self.assertEqual(self.client.get("/enterprise/automation/batches", params={"team_id": self.team}, headers=self.other["headers"]).json(), [])

    # ---- 事件驱动：重启后继续、重试用尽、死信 ----

    def _expire_retries(self):
        self.db.execute(text("UPDATE consumer_retry SET next_attempt_at=:t WHERE consumer='batch_item_worker'"), {"t": utcnow() - timedelta(seconds=1)})
        self.db.commit()

    def test_a_restart_in_the_middle_resumes_the_batch_instead_of_failing_it(self):
        batch = self.create(drain=False).json()
        outbox.publish_pending(outbox.db_transport)
        first = batch["items"][0]
        # 进程在整理第一份材料的中途崩溃：材料停在“整理中”、事件租约已被领走但没有完成
        outbox.claim("batch_item_worker", self._event_id(first["id"]))
        self.db.execute(text("UPDATE automation_work SET status='processing', updated_at=:t WHERE id=:i"),
                        {"t": utcnow() - timedelta(minutes=5), "i": first["id"]})
        self.db.commit()
        self.drain()
        mid = self.get(batch["batch_id"]).json()
        self.assertEqual([i["status"] for i in mid["items"]], ["processing", "ready", "ready"])     # 另外两份不受影响
        self._expire_retries()                                                                      # 重启后租约到期
        self.drain()
        done = self.get(batch["batch_id"]).json()
        self.assertEqual([i["status"] for i in done["items"]], ["ready"] * 3)                       # 接着整理，不是标成失败
        self.assertEqual(len(self.calls), 3)

    def test_item_still_being_processed_elsewhere_is_retried_not_duplicated(self):
        batch = self.create(drain=False).json()
        outbox.publish_pending(outbox.db_transport)
        first = batch["items"][0]
        self.db.execute(text("UPDATE automation_work SET status='processing', updated_at=:t WHERE id=:i"), {"t": utcnow(), "i": first["id"]})
        self.db.commit()
        self.drain()
        item = self.get(batch["batch_id"]).json()["items"][0]
        self.assertEqual(item["status"], "processing")                                              # 没有被抢走重复整理
        retry = self.db.execute(text("SELECT attempts FROM consumer_retry WHERE consumer='batch_item_worker' AND event_id=:e"),
                                {"e": self._event_id(first["id"])}).scalar()
        self.db.commit()
        self.assertEqual(retry, 1)
        self.assertEqual(len(self.calls), 2)

    def test_exhausted_retries_fail_the_item_open_a_problem_and_land_in_the_dead_letter_queue(self):
        batch = self.create(drain=False).json()
        with patch.object(batches, "process_work", AsyncMock(side_effect=RuntimeError("数据库连接断开 password=hunter2"))):
            for _ in range(6):
                self.drain()
                self._expire_retries()
        done = self.get(batch["batch_id"]).json()
        self.assertEqual(done["counts"]["failed"], 3)
        self.assertIn("异常", done["items"][0]["error_message"])
        dead = self.db.execute(text("SELECT status, attempts, error FROM dead_letter WHERE consumer='batch_item_worker'")).all()
        self.db.commit()
        self.assertEqual(len(dead), 3)
        self.assertEqual({d[0] for d in dead}, {"pending"})
        self.assertTrue(all("hunter2" not in d[2] for d in dead))
        problem = self.db.execute(text("SELECT occurrence_count FROM system_issue WHERE error_code='KAFKA_CONSUME_FAILED' AND operation LIKE '消费者 batch_item_worker%'")).scalar()
        self.db.commit()
        self.assertEqual(problem, 3)
        # 失败的材料还能照常重试（新事件），并成功
        self.client.post(f"/enterprise/automation/batches/{batch['batch_id']}/items/{done['items'][0]['id']}/retry", headers=self.emp["headers"])
        self.drain()
        self.assertEqual(self.get(batch["batch_id"]).json()["items"][0]["status"], "ready")

    def test_creating_a_batch_registers_all_events_together_with_the_materials(self):
        self.create(drain=False)
        count = self.db.execute(text("SELECT COUNT(*) FROM outbox_event WHERE topic='automation.job.v1' AND payload_json LIKE :p"), {"p": '%"user_id": ' + str(self.emp["id"]) + '%'}).scalar()
        self.db.commit()
        self.assertEqual(count, 3)

    def _event_id(self, work_id):
        value = self.db.execute(text("SELECT event_id FROM outbox_event WHERE topic='automation.job.v1' AND payload_json LIKE :p ORDER BY id DESC LIMIT 1"),
                                {"p": f'%{work_id}%'}).scalar()
        self.db.commit()
        return value

    def test_dead_letter_admin_api(self):
        self.client_admin = self.client
        batch = self.create(drain=False).json()
        with patch.object(batches, "process_work", AsyncMock(side_effect=RuntimeError("boom"))):
            for _ in range(6):
                self.drain()
                self._expire_retries()
        admin = rc.create_user("bat-dlq-adm", admin=True)
        self.assertEqual(self.client.get("/admin/events/dead-letters", headers=self.emp["headers"]).status_code, 403)
        with rc.admin_env(admin["name"]):
            listed = self.client.get("/admin/events/dead-letters", headers=admin["headers"]).json()
            mine = [d for d in listed if d["consumer"] == "batch_item_worker"]
            self.assertEqual(len(mine), 3)
            detail = self.client.get(f"/admin/events/dead-letters/{mine[0]['id']}", headers=admin["headers"]).json()
            self.assertEqual(detail["event"]["event_type"], "batch.item.queued")
            self.assertEqual(self.client.post(f"/admin/events/dead-letters/{mine[0]['id']}/discard", json={"reason": "x"}, headers=admin["headers"]).status_code, 422)
            discarded = self.client.post(f"/admin/events/dead-letters/{mine[0]['id']}/discard", json={"reason": "材料已由用户重新提交"}, headers=admin["headers"])
            self.assertEqual(discarded.json()["status"], "discarded")
            redelivered = self.client.post(f"/admin/events/dead-letters/{mine[1]['id']}/redeliver", headers=admin["headers"])
            self.assertEqual(redelivered.json()["status"], "redelivered")
            self.assertEqual(self.client.post(f"/admin/events/dead-letters/{mine[1]['id']}/redeliver", headers=admin["headers"]).status_code, 409)
            self.assertEqual(self.client.get("/admin/events/stats", headers=admin["headers"]).status_code, 200)
        self.drain()    # 重新投递的那份这次处理成功（process_work 不再抛异常）
        statuses = sorted(i["status"] for i in self.get(batch["batch_id"]).json()["items"])
        self.assertEqual(statuses, ["failed", "failed", "ready"])


if __name__ == "__main__":
    unittest.main()
