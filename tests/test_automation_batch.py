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

    def body(self, texts=None, **over):
        texts = [f"第{i}份材料：我要请年假，家里有事" for i in range(1, 4)] if texts is None else texts
        data = {"batch_id": str(uuid.uuid4()), "team_id": self.team, "kind": "leave", "model_name": "test-model",
                "items": [{"name": f"文件{i}.docx", "text": t} for i, t in enumerate(texts, start=1)]}
        data.update(over)
        return data

    def create(self, user=None, **over):
        return self.client.post("/enterprise/automation/batches", json=self.body(**over), headers=(user or self.emp)["headers"])

    def get(self, batch_id, user=None):
        return self.client.get(f"/enterprise/automation/batches/{batch_id}", headers=(user or self.emp)["headers"])

    # ---- 正常流程 ----

    def test_items_are_processed_in_the_background_with_progress(self):
        response = self.create()
        self.assertEqual(response.status_code, 200, response.text)
        first = response.json()
        self.assertEqual((first["total"], first["kind"]), (3, "leave"))
        self.assertEqual([i["status"] for i in first["items"]], ["queued"] * 3)      # 创建时立即返回，全部排队中
        done = self.get(first["batch_id"]).json()                                       # TestClient 在响应后跑完后台任务
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
        self.assertEqual(len(self.calls), 6)
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
        batch = self.client.post("/enterprise/automation/batches", json=self.body(), headers=self.emp["headers"]).json()
        # 创建时已经整理完；这里验证排队期间失效：把一份改回排队，再让身份校验失败后重新运行
        self.db.execute(text("UPDATE automation_work SET status='queued', proposal_json=NULL WHERE batch_id=:b LIMIT 1"), {"b": batch["batch_id"]})
        self.db.commit()
        ids = [i["id"] for i in self.get(batch["batch_id"]).json()["items"] if i["status"] == "queued"]
        with patch.object(batches, "authorize", side_effect=revoked):
            asyncio.run(batches.run_items(ids, self.emp["id"]))
        done = self.get(batch["batch_id"]).json()
        self.assertEqual(sum(1 for i in done["items"] if i["status"] == "failed"), 1)
        self.assertIn("身份已失效", next(i for i in done["items"] if i["status"] == "failed")["error_message"])

    def test_stuck_queued_items_become_failed_after_the_process_was_restarted(self):
        batch = self.create().json()
        self.db.execute(text("UPDATE automation_work SET status='queued', updated_at=:t WHERE batch_id=:b"),
                        {"b": batch["batch_id"], "t": utcnow() - timedelta(minutes=20)})
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


if __name__ == "__main__":
    unittest.main()
