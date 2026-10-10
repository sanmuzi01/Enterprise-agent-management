"""技能批量上架 / 下架（service/skills_core/shelf.py，POST /skill/admin/shelf）。

用户的技能中心只显示“公开 + 已发布”的技能。批量导入的技能只会被设成公开、状态是草稿，结果用户的技能中心是空的。
这里走真实路由验证：上架后用户真的能在 /skill/public 看到；部门助手的专属技能不能上架；只有管理员能操作；写审计。
"""
import unittest
from unittest import mock

from sqlalchemy import text

from tests import _route_client as rc

_AVAILABLE, _WHY = rc.route_tests_available()


@unittest.skipUnless(_AVAILABLE, f"需要本地 MySQL：{_WHY}")
class SkillShelfTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.admin = rc.create_user("shelf-admin")
        cls.member = rc.create_user("shelf-member")
        cls.env = rc.admin_env(cls.admin["name"])
        cls.env.start()
        cls.client = rc.make_client()
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        cls.env.stop()
        rc.cleanup()   # 按 user_id 删掉这里建的技能

    def _skill(self, name, is_public=1, status="draft", config_file=None):
        from models.init_db import SessionLocal, Skill
        with SessionLocal() as db:
            skill = Skill(user_id=self.admin["id"], name=name, config_file=config_file or f"imported/rt_{name}.yml",
                          is_public=is_public, lifecycle_status=status)
            db.add(skill)
            db.commit()
            return skill.id

    def _state(self, skill_id):
        from models.init_db import SessionLocal
        with SessionLocal() as db:
            return tuple(db.execute(text("SELECT is_public, lifecycle_status FROM skill WHERE id = :i"), {"i": skill_id}).one())

    def _shelf(self, ids, action, user=None):
        return self.client.post("/skill/admin/shelf", headers=(user or self.admin)["headers"],
                                json={"skill_ids": ids, "action": action})

    def _public_ids(self):
        r = self.client.get("/skill/public", headers=self.member["headers"])
        self.assertEqual(r.status_code, 200, r.text)
        return {s["id"] for s in r.json()["data"]}

    def test_publish_puts_public_drafts_and_private_skills_on_the_users_shelf(self):
        public_draft = self._skill("rt-公开草稿")
        private_draft = self._skill("rt-私有草稿", is_public=0)
        already = self._skill("rt-已上架", status="published")
        agent_private = self._skill("rt-部门助手专属", is_public=0, status="published", config_file="enterprise/agent_987654.yml")
        self.assertNotIn(public_draft, self._public_ids(), "公开的草稿用户看不到——这就是技能中心是空的原因")

        with mock.patch("service.audit_service.record") as audit:
            r = self._shelf([public_draft, private_draft, already, agent_private, 99999999], "publish")
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()["data"]
        self.assertEqual({c["id"] for c in data["changed"]}, {public_draft, private_draft})
        self.assertEqual(data["unchanged"], [already])
        self.assertEqual({s["id"] for s in data["skipped"]}, {agent_private, 99999999})
        self.assertIn("部门助手", next(s["reason"] for s in data["skipped"] if s["id"] == agent_private))
        self.assertEqual(self._state(public_draft), (1, "published"))
        self.assertEqual(self._state(private_draft), (1, "published"), "上架同时设为公开")
        self.assertEqual(self._state(agent_private), (0, "published"), "部门助手的专属技能保持私有")

        shelf = self._public_ids()
        self.assertTrue({public_draft, private_draft, already} <= shelf, "上架后普通用户的技能中心能看到")
        self.assertNotIn(agent_private, shelf)

        audit.assert_called_once()
        self.assertEqual(audit.call_args.args[:2], (self.admin["id"], "skill.batch_published"))
        self.assertEqual(set(audit.call_args.kwargs["detail"]["skill_ids"]), {public_draft, private_draft})

    def test_unpublish_takes_them_off_the_shelf_but_keeps_them_public(self):
        skill = self._skill("rt-要下架", status="published")
        self.assertIn(skill, self._public_ids())
        with mock.patch("service.audit_service.record") as audit:
            r = self._shelf([skill], "unpublish")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self._state(skill), (1, "draft"), "下架只退回草稿，公开标记保留")
        self.assertNotIn(skill, self._public_ids())
        self.assertEqual(audit.call_args.args[1], "skill.batch_unpublished")

    def test_repeating_the_same_batch_changes_nothing_and_writes_no_audit(self):
        skill = self._skill("rt-重复上架")
        with mock.patch("service.audit_service.record"):   # 测试不往真实审计里写
            self._shelf([skill], "publish")
        with mock.patch("service.audit_service.record") as audit:
            r = self._shelf([skill, skill], "publish")
        self.assertEqual(r.json()["data"]["changed"], [])
        self.assertEqual(r.json()["data"]["unchanged"], [skill], "重复的 id 只算一次")
        audit.assert_not_called()

    def test_only_admins_and_only_valid_requests(self):
        skill = self._skill("rt-普通用户不能上架")
        self.assertEqual(self._shelf([skill], "publish", user=self.member).status_code, 403)
        self.assertEqual(self._state(skill), (1, "draft"))
        self.assertEqual(self._shelf([], "publish").status_code, 422, "至少选一个")
        self.assertEqual(self._shelf([skill], "delete").status_code, 422, "只能上架或下架")


if __name__ == "__main__":
    unittest.main()
