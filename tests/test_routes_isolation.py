"""真实路由级测试：登录鉴权 + 跨用户 / 管理员权限隔离。

走真实 FastAPI 应用（TestClient）、真实 JWT、真实 DB。无法连库或缺 JWT_SECRET_KEY 时整体 skip。
"""

import os
import unittest
import uuid
from unittest.mock import AsyncMock, patch

from tests import _route_client as rc


_AVAILABLE, _WHY = rc.route_tests_available()


@unittest.skipUnless(_AVAILABLE, f"路由级测试环境不可用：{_WHY}")
class RouteIsolationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = rc.make_client()
        cls.client.__enter__()
        cls.alice = rc.create_user("alice")
        cls.bob = rc.create_user("bob")
        cls.admin = rc.create_user("admin")
        cls._admin_env = rc.admin_env(cls.admin["name"])
        cls._admin_env.start()

    @classmethod
    def tearDownClass(cls):
        cls._admin_env.stop()
        try:
            cls.client.__exit__(None, None, None)
        finally:
            rc.cleanup()

    # ---- 登录 / 鉴权门槛 ----

    def test_login_success_and_wrong_password(self):
        ok = self.client.post("/user/login", json={"name": self.alice["name"], "password": self.alice["password"]})
        self.assertEqual(ok.status_code, 200, ok.text)
        self.assertIn("access_token", ok.json())

        bad = self.client.post("/user/login", json={"name": self.alice["name"], "password": "wrong-password"})
        self.assertEqual(bad.status_code, 401)

    def test_protected_route_requires_token(self):
        self.assertIn(self.client.get("/user/widgets").status_code, (401, 403))
        self.assertEqual(self.client.get("/user/widgets", headers=self.alice["headers"]).status_code, 200)

    # ---- 组件跨用户隔离 ----

    def _create_widget(self, user) -> int:
        draft = {"type": "metric", "data_source": {"kind": "system_stats"}, "trigger": {"kind": "manual"}}
        r = self.client.post("/user/widgets", json={"draft": draft}, headers=user["headers"])
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["id"]

    def test_widget_is_private_to_owner(self):
        wid = self._create_widget(self.alice)

        # 本人可见
        mine = self.client.get("/user/widgets", headers=self.alice["headers"]).json()["items"]
        self.assertIn(wid, [w["id"] for w in mine])

        # 别人拿不到 —— 读 / 运行 / 导出 / 改 / 删 全部 404
        h = self.bob["headers"]
        self.assertEqual(self.client.get(f"/user/widgets/{wid}/data", headers=h).status_code, 404)
        self.assertEqual(self.client.post(f"/user/widgets/{wid}/run", headers=h).status_code, 404)
        self.assertEqual(self.client.get(f"/user/widgets/{wid}/export", headers=h).status_code, 404)
        self.assertEqual(self.client.patch(f"/user/widgets/{wid}", json={"name": "x"}, headers=h).status_code, 404)
        self.assertEqual(self.client.delete(f"/user/widgets/{wid}", headers=h).status_code, 404)

        # bob 的列表里也不该出现
        theirs = self.client.get("/user/widgets", headers=h).json()["items"]
        self.assertNotIn(wid, [w["id"] for w in theirs])

    # ---- 知识库跨用户隔离 ----

    def test_knowledge_agent_is_private_to_owner(self):
        r = self.client.post("/agent", json={"name": f"rt-agent-{self.alice['id']}"}, headers=self.alice["headers"])
        self.assertEqual(r.status_code, 200, r.text)
        agent_id = r.json().get("agent_id")
        self.assertIsNotNone(agent_id, r.text)

        # 同步 def 端点 + 领域异常：别人删 / 删不存在 都要 404（且带 code 字段）
        gone = self.client.delete(f"/agent/{agent_id}", headers=self.bob["headers"])
        self.assertEqual(gone.status_code, 404)
        self.assertEqual(gone.json().get("code"), "not_found")
        self.assertEqual(self.client.delete("/agent/999999", headers=self.alice["headers"]).status_code, 404)

        # bob 查 alice 的知识库诊断 —— 不放行
        got = self.client.get(f"/knowledge/{agent_id}/diagnostics", headers=self.bob["headers"])
        self.assertIn(got.status_code, (403, 404))

        # alice 自己可以
        self.assertEqual(
            self.client.get(f"/knowledge/{agent_id}/diagnostics", headers=self.alice["headers"]).status_code, 200
        )

    def test_create_agent_ignores_client_supplied_agent_type_and_department_code(self):
        # 中央/部门 Agent 只能由企业管理员通过组织管理后台创建（同时设置
        # organization_id/team_id/scope_type）——普通用户这个创建接口不再接受
        # agent_type/department_code，就算传了也该被忽略，不能建出一个自称
        # "central"/"department" 的 Agent。
        r = self.client.post(
            "/agent",
            json={
                "name": f"rt-fake-dept-agent-{self.alice['id']}",
                "agent_type": "department",
                "department_code": "procurement",
            },
            headers=self.alice["headers"],
        )
        self.assertEqual(r.status_code, 200, r.text)
        agent_id = r.json()["agent_id"]

        from models.init_db import Agent, SessionLocal
        db = SessionLocal()
        try:
            agent = db.get(Agent, agent_id)
            self.assertEqual(agent.agent_type, "personal")
            self.assertIsNone(agent.department_code)
        finally:
            db.close()

        # 纯读接口（已全量 async）：本人 200、别人 404
        self.assertEqual(self.client.get(f"/knowledge/{agent_id}/list", headers=self.alice["headers"]).status_code, 200)
        self.assertEqual(self.client.get(f"/knowledge/{agent_id}/list", headers=self.bob["headers"]).status_code, 404)
        self.assertEqual(self.client.get("/knowledge/my/list", headers=self.alice["headers"]).status_code, 200)
        self.assertEqual(
            self.client.get(f"/knowledge/{agent_id}/999999", headers=self.alice["headers"]).status_code, 404
        )

        # 检索：本人过了归属校验（测试用户没配 embedding Key，会在向量化那步 400），别人在归属校验就 404
        mine = self.client.post(f"/knowledge/{agent_id}/search", json={"query": "x", "top_k": 3},
                                headers=self.alice["headers"])
        self.assertIn(mine.status_code, (200, 400), mine.text)
        if mine.status_code == 400:
            self.assertIn("API Key", mine.text)          # 说明已越过归属校验，卡在向量化
        self.assertEqual(
            self.client.post(f"/knowledge/{agent_id}/search", json={"query": "x"},
                             headers=self.bob["headers"]).status_code, 404
        )

    # ---- 知识库空间：CRUD + 跨用户隔离 ----

    def test_knowledge_space_is_private_to_owner(self):
        c = self.client.post(
            "/knowledge-spaces",
            json={"name": f"rt-space-{self.alice['id']}", "purpose": "policy", "tags": ["制度"]},
            headers=self.alice["headers"],
        )
        self.assertEqual(c.status_code, 200, c.text)
        sid = c.json()["id"]

        mine = self.client.get("/knowledge-spaces", headers=self.alice["headers"]).json()
        self.assertIn(sid, [s["id"] for s in mine["items"]])
        self.assertTrue(any(p["key"] == "policy" for p in mine["purposes"]))

        h = self.bob["headers"]
        self.assertEqual(self.client.get(f"/knowledge-spaces/{sid}", headers=h).status_code, 404)
        self.assertEqual(self.client.patch(f"/knowledge-spaces/{sid}", json={"name": "x"}, headers=h).status_code, 404)
        d = self.client.delete(f"/knowledge-spaces/{sid}", headers=h)
        self.assertEqual(d.status_code, 404)
        self.assertEqual(d.json().get("code"), "not_found")
        self.assertNotIn(sid, [s["id"] for s in self.client.get("/knowledge-spaces", headers=h).json()["items"]])

        # owner 改名
        self.assertEqual(
            self.client.patch(f"/knowledge-spaces/{sid}", json={"name": "改过的名字"},
                              headers=self.alice["headers"]).status_code, 200
        )

        # 空间内文档：上传 -> 列表 -> 跨用户隔离 -> 删除
        up = self.client.post(
            f"/knowledge-spaces/{sid}/documents",
            files={"file": ("rt-note.txt", b"hello knowledge space", "text/plain")},
            headers=self.alice["headers"],
        )
        self.assertEqual(up.status_code, 200, up.text)
        kid = up.json()["knowledge_id"]

        lst = self.client.get(f"/knowledge-spaces/{sid}/documents", headers=self.alice["headers"])
        self.assertEqual(lst.status_code, 200)
        self.assertIn(kid, [d["id"] for d in lst.json()["items"]])

        self.assertEqual(self.client.get(f"/knowledge-spaces/{sid}/documents", headers=h).status_code, 404)
        self.assertEqual(
            self.client.delete(f"/knowledge-spaces/{sid}/documents/{kid}", headers=h).status_code, 404
        )
        self.assertEqual(
            self.client.delete(f"/knowledge-spaces/{sid}/documents/{kid}", headers=self.alice["headers"]).status_code, 200
        )

        # 清空文档后 owner 可删空间
        self.assertEqual(
            self.client.delete(f"/knowledge-spaces/{sid}", headers=self.alice["headers"]).status_code, 200
        )

    # ---- Agent 绑定知识库空间：绑定校验 + 越权 ----

    def _make_space(self, user, name: str) -> int:
        r = self.client.post(
            "/knowledge-spaces",
            json={"name": name, "purpose": "policy"},
            headers=user["headers"],
        )
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["id"]

    def test_agent_space_binding_and_cross_user_reject(self):
        s1 = self._make_space(self.alice, f"rt-bind-a-{self.alice['id']}")
        s2 = self._make_space(self.alice, f"rt-bind-b-{self.alice['id']}")
        bob_space = self._make_space(self.bob, f"rt-bind-bob-{self.bob['id']}")

        # 创建时绑定自己的两个空间 + kb_* 配置
        c = self.client.post(
            "/agent",
            json={"name": f"rt-kb-agent-{self.alice['id']}", "rag_enabled": 1,
                  "space_ids": [s1, s2], "kb_top_k": 8, "kb_force_citation": 1},
            headers=self.alice["headers"],
        )
        self.assertEqual(c.status_code, 200, c.text)
        agent_id = c.json()["agent_id"]

        got = self.client.get(f"/agent/{agent_id}", headers=self.alice["headers"]).json()
        self.assertCountEqual(got.get("space_ids", []), [s1, s2])
        self.assertEqual(got.get("kb_top_k"), 8)

        # 改绑：只留 s1
        u = self.client.put(f"/agent/{agent_id}", json={"space_ids": [s1]}, headers=self.alice["headers"])
        self.assertEqual(u.status_code, 200, u.text)
        got = self.client.get(f"/agent/{agent_id}", headers=self.alice["headers"]).json()
        self.assertEqual(got.get("space_ids"), [s1])

        # 绑定别人的空间 -> 400（校验挡在 access_control.user_space_ids）
        bad = self.client.put(
            f"/agent/{agent_id}", json={"space_ids": [s1, bob_space]}, headers=self.alice["headers"]
        )
        self.assertEqual(bad.status_code, 400, bad.text)

        # 创建时就绑别人的空间 -> 400
        bad2 = self.client.post(
            "/agent",
            json={"name": f"rt-kb-bad-{self.alice['id']}", "space_ids": [bob_space]},
            headers=self.alice["headers"],
        )
        self.assertEqual(bad2.status_code, 400, bad2.text)

        # 清理绑定后删 Agent + 空间
        self.client.put(f"/agent/{agent_id}", json={"space_ids": []}, headers=self.alice["headers"])
        self.client.delete(f"/agent/{agent_id}", headers=self.alice["headers"])
        for sid in (s1, s2):
            self.client.delete(f"/knowledge-spaces/{sid}", headers=self.alice["headers"])
        self.client.delete(f"/knowledge-spaces/{bob_space}", headers=self.bob["headers"])

    def test_agent_space_binding_allows_department_team_admin(self):
        """Phase 3B（docs/enterprise-rbac-plan.md）：alice 是某个部门的 team admin，
        bob 的知识库空间挂在这个部门下——alice 虽然不是这个空间的 SpaceMember，也应该
        能把它绑到自己的 Agent 上（校验路径跟上面一样，走 access_control.user_space_ids，
        这条走的是它新加的第三个来源）。没有现成的路由能设置 team_id，直接建库行。
        """
        from models.init_db import EnterpriseRole, KnowledgeSpace, SessionLocal, Team, TeamMember

        db = SessionLocal()
        try:
            team = Team(name=f"rt-bind-team-{self.alice['id']}", owner_user_id=self.bob["id"])
            db.add(team)
            db.commit()
            team_id = team.id
            admin_role_id = db.query(EnterpriseRole.id).filter_by(scope="team", code="admin").scalar()
            db.add(TeamMember(team_id=team_id, user_id=self.alice["id"], role_id=admin_role_id, status="active"))
            db.commit()

            dept_space = KnowledgeSpace(user_id=self.bob["id"], name=f"rt-dept-space-{self.bob['id']}", team_id=team_id)
            db.add(dept_space)
            db.commit()
            dept_space_id = dept_space.id
        finally:
            db.close()

        try:
            c = self.client.post(
                "/agent",
                json={"name": f"rt-dept-agent-{self.alice['id']}", "rag_enabled": 1,
                      "space_ids": [dept_space_id]},
                headers=self.alice["headers"],
            )
            self.assertEqual(c.status_code, 200, c.text)
            agent_id = c.json()["agent_id"]
            got = self.client.get(f"/agent/{agent_id}", headers=self.alice["headers"]).json()
            self.assertEqual(got.get("space_ids"), [dept_space_id])
            self.client.put(f"/agent/{agent_id}", json={"space_ids": []}, headers=self.alice["headers"])
            self.client.delete(f"/agent/{agent_id}", headers=self.alice["headers"])
        finally:
            from sqlalchemy import text
            db = SessionLocal()
            try:
                db.execute(text("DELETE FROM knowledge_spaces WHERE id=:i"), {"i": dept_space_id})
                db.execute(text("DELETE FROM team_members WHERE team_id=:t"), {"t": team_id})
                db.execute(text("DELETE FROM teams WHERE id=:t"), {"t": team_id})
                db.commit()
            except Exception:
                db.rollback()
            finally:
                db.close()

    # ---- 知识库调试台：越权检索 / 样例隔离 ----

    def test_rag_debug_console_isolation(self):
        sid = self._make_space(self.alice, f"rt-dbg-{self.alice['id']}")
        h_a, h_b = self.alice["headers"], self.bob["headers"]

        # alice 对自己的空间跑检索：过了归属校验，卡在向量化（测试用户没配 embedding Key）
        mine = self.client.post(
            "/rag-debug/run", json={"query": "年假几天", "space_ids": [sid], "top_k": 3}, headers=h_a
        )
        self.assertIn(mine.status_code, (200, 400), mine.text)

        # bob 用 alice 的 space_id 跑检索 -> 403（PermissionError 在向量化之前）
        theirs = self.client.post(
            "/rag-debug/run", json={"query": "年假几天", "space_ids": [sid], "top_k": 3}, headers=h_b
        )
        self.assertEqual(theirs.status_code, 403, theirs.text)

        # 存一条样例（不依赖检索是否成功，result 可为空快照）
        saved = self.client.post(
            "/rag-debug/samples",
            json={"query": "年假几天", "space_ids": [sid],
                  "result": {"hits": [], "context": "", "citations": []},
                  "verdict": "useless", "in_eval_set": True},
            headers=h_a,
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        sample_id = saved.json()["id"]

        # 列表 / 导出：本人可见，别人 404 / 空
        mine_list = self.client.get(f"/rag-debug/samples?space_id={sid}", headers=h_a).json()
        self.assertIn(sample_id, [s["id"] for s in mine_list["items"]])
        self.assertEqual(self.client.get(f"/rag-debug/samples?space_id={sid}", headers=h_b).status_code, 404)

        exported = self.client.get(f"/rag-debug/samples/export?space_id={sid}", headers=h_a).json()
        self.assertEqual(exported["total"], 1)
        self.assertEqual(exported["cases"][0]["expected_knowledge_ids"], [])  # useless -> 期望查不到

        # 别人改 / 删这条样例 -> 404
        self.assertEqual(
            self.client.patch(f"/rag-debug/samples/{sample_id}", json={"verdict": "useful"}, headers=h_b).status_code, 404
        )
        self.assertEqual(self.client.delete(f"/rag-debug/samples/{sample_id}", headers=h_b).status_code, 404)

        # 本人删除后清理空间
        self.assertEqual(self.client.delete(f"/rag-debug/samples/{sample_id}", headers=h_a).status_code, 200)
        self.client.delete(f"/knowledge-spaces/{sid}", headers=h_a)

    # ---- 知识库健康分 + 按空间评估：越权隔离 ----

    def test_space_health_and_eval_isolation(self):
        sid = self._make_space(self.alice, f"rt-health-{self.alice['id']}")
        h_a, h_b = self.alice["headers"], self.bob["headers"]

        # 本人：健康分实时算，返回明细 + 分数
        ok = self.client.get(f"/knowledge-spaces/{sid}/health", headers=h_a)
        self.assertEqual(ok.status_code, 200, ok.text)
        body = ok.json()
        self.assertIsInstance(body["health_score"], int)
        self.assertIn("documents", body)
        self.assertIn("retrieval", body)

        # 别人：404（不泄露存在性）
        self.assertEqual(self.client.get(f"/knowledge-spaces/{sid}/health", headers=h_b).status_code, 404)

        # 按空间评估：本人过了归属校验（无 embedding Key 时卡在向量化 -> 400）；别人 404
        mine = self.client.post(
            f"/evaluation/space/{sid}/rag", json={"cases": [{"question": "年假几天"}], "top_k": 3}, headers=h_a
        )
        self.assertIn(mine.status_code, (200, 400), mine.text)
        self.assertEqual(
            self.client.post(
                f"/evaluation/space/{sid}/rag", json={"cases": [{"question": "x"}]}, headers=h_b
            ).status_code, 404
        )

        self.client.delete(f"/knowledge-spaces/{sid}", headers=h_a)

    # ---- 评估：Agent 维度 RAG 评估 + 固定评估集 CRUD（Phase 3 收尾，全量 AsyncSession）----

    def test_agent_rag_eval_and_eval_sets_isolation(self):
        h_a, h_b = self.alice["headers"], self.bob["headers"]

        c = self.client.post("/agent", json={"name": f"rt-eval-agent-{self.alice['id']}"}, headers=h_a)
        self.assertEqual(c.status_code, 200, c.text)
        agent_id = c.json()["agent_id"]

        # 本人：过了归属校验，卡在向量化（没配 embedding Key）-> 200 或 400 都算过了这一关
        mine = self.client.post(
            f"/evaluation/{agent_id}/rag", json={"cases": [{"question": "年假几天"}], "top_k": 3}, headers=h_a
        )
        self.assertIn(mine.status_code, (200, 400), mine.text)

        # 别人用 alice 的 agent_id -> 404（不泄露存在性）
        theirs = self.client.post(
            f"/evaluation/{agent_id}/rag", json={"cases": [{"question": "x"}]}, headers=h_b
        )
        self.assertEqual(theirs.status_code, 404, theirs.text)

        # 固定评估集：创建时把 evaluate_rag_dataset 打桩掉，不依赖真实向量检索
        with patch(
            "service.evaluation.eval_set_service.evaluate_rag_dataset",
            new_callable=AsyncMock,
            return_value={"case_count": 1, "evaluated_retrieval_count": 1,
                          "evaluated_faithfulness_count": 0,
                          "metrics": {"hit_rate": 1.0, "recall": None,
                                      "precision_at_k": None, "mrr": None, "faithfulness": None},
                          "cases": [{"question": "年假几天", "hit": True}], "settings": {}},
        ):
            created = self.client.post(
                "/evaluation/sets",
                json={"name": f"rt-evalset-{self.alice['id']}", "agent_id": agent_id,
                      "cases": [{"question": "年假几天"}]},
                headers=h_a,
            )
            self.assertEqual(created.status_code, 200, created.text)
            set_id = created.json()["id"]

            # 别人看不到、跑不了、删不掉 -> 全部 404
            self.assertEqual(self.client.get(f"/evaluation/sets/{set_id}", headers=h_b).status_code, 404)
            self.assertEqual(self.client.post(f"/evaluation/sets/{set_id}/run", headers=h_b).status_code, 404)
            self.assertEqual(self.client.delete(f"/evaluation/sets/{set_id}", headers=h_b).status_code, 404)

            # 本人：列表能看到、跑一次、看历史
            listed = self.client.get(f"/evaluation/sets?agent_id={agent_id}", headers=h_a).json()
            self.assertIn(set_id, [s["id"] for s in listed])

            run = self.client.post(f"/evaluation/sets/{set_id}/run", headers=h_a)
            self.assertEqual(run.status_code, 200, run.text)
            self.assertEqual(run.json()["report"]["metrics"]["hit_rate"], 1.0)

            runs = self.client.get(f"/evaluation/sets/{set_id}/runs", headers=h_a).json()
            self.assertEqual(len(runs), 1)

            self.assertEqual(self.client.delete(f"/evaluation/sets/{set_id}", headers=h_a).status_code, 200)

        self.client.delete(f"/agent/{agent_id}", headers=h_a)

    # ---- 知识库空间企业权限：成员分级 + 审计 + 管理员视角 ----

    def test_space_membership_roles_and_audit(self):
        h_a, h_b = self.alice["headers"], self.bob["headers"]
        sid = self._make_space(self.alice, f"rt-team-{self.alice['id']}")
        up = self.client.post(
            f"/knowledge-spaces/{sid}/documents",
            files={"file": ("rt-team.txt", b"hello team space", "text/plain")},
            headers=h_a,
        )
        self.assertEqual(up.status_code, 200, up.text)
        kid = up.json()["knowledge_id"]

        # 非成员：读写都 404（不泄露存在性）
        self.assertEqual(self.client.get(f"/knowledge-spaces/{sid}", headers=h_b).status_code, 404)
        self.assertEqual(self.client.get(f"/knowledge-spaces/{sid}/documents", headers=h_b).status_code, 404)
        self.assertEqual(self.client.get(f"/knowledge-spaces/{sid}/members", headers=h_b).status_code, 404)

        # 加 bob 为 viewer
        r = self.client.put(
            f"/knowledge-spaces/{sid}/members",
            json={"user_name": self.bob["name"], "role": "viewer"}, headers=h_a,
        )
        self.assertEqual(r.status_code, 200, r.text)

        # viewer：能读，不能写
        got = self.client.get(f"/knowledge-spaces/{sid}", headers=h_b)
        self.assertEqual(got.status_code, 200)
        self.assertEqual(got.json()["my_role"], "viewer")
        self.assertFalse(got.json()["can_write_doc"])
        self.assertEqual(self.client.get(f"/knowledge-spaces/{sid}/documents", headers=h_b).status_code, 200)
        self.assertEqual(
            self.client.post(f"/knowledge-spaces/{sid}/documents",
                             files={"file": ("x.txt", b"x", "text/plain")}, headers=h_b).status_code, 403
        )
        self.assertEqual(
            self.client.patch(f"/knowledge-spaces/{sid}", json={"name": "viewer改的"}, headers=h_b).status_code, 403
        )
        self.assertEqual(
            self.client.delete(f"/knowledge-spaces/{sid}/documents/{kid}", headers=h_b).status_code, 403
        )
        # viewer 不能管成员
        self.assertEqual(
            self.client.put(f"/knowledge-spaces/{sid}/members",
                            json={"user_name": self.admin["name"], "role": "viewer"}, headers=h_b).status_code, 403
        )

        # 升 editor：能传文档，仍不能改空间
        self.client.put(f"/knowledge-spaces/{sid}/members",
                        json={"user_name": self.bob["name"], "role": "editor"}, headers=h_a)
        up2 = self.client.post(f"/knowledge-spaces/{sid}/documents",
                               files={"file": ("rt-editor.txt", b"editor upload", "text/plain")}, headers=h_b)
        self.assertEqual(up2.status_code, 200, up2.text)
        kid2 = up2.json()["knowledge_id"]
        self.assertEqual(
            self.client.patch(f"/knowledge-spaces/{sid}", json={"name": "editor改的"}, headers=h_b).status_code, 403
        )

        # 升 admin：能改空间、能管成员，不能删空间
        self.client.put(f"/knowledge-spaces/{sid}/members",
                        json={"user_name": self.bob["name"], "role": "admin"}, headers=h_a)
        self.assertEqual(
            self.client.patch(f"/knowledge-spaces/{sid}", json={"name": "admin改的"}, headers=h_b).status_code, 200
        )
        self.assertEqual(self.client.delete(f"/knowledge-spaces/{sid}", headers=h_b).status_code, 403)

        # 审计：owner 能看，条目里有成员设置 / 上传 / 修改
        audit = self.client.get(f"/knowledge-spaces/{sid}/audit", headers=h_a)
        self.assertEqual(audit.status_code, 200, audit.text)
        actions = {e["action"] for e in audit.json()["items"]}
        self.assertIn("member.set", actions)
        self.assertIn("doc.upload", actions)
        self.assertIn("space.update", actions)

        # 管理员企业视角：管理员能看到，普通用户 403
        self.assertEqual(self.client.get("/admin/knowledge-spaces", headers=self.alice["headers"]).status_code, 403)
        adm = self.client.get("/admin/knowledge-spaces", headers=self.admin["headers"])
        self.assertEqual(adm.status_code, 200, adm.text)
        self.assertIn(sid, [s["id"] for s in adm.json()["items"]])

        # 清理：删文档、移除成员、删空间
        for k in (kid, kid2):
            self.client.delete(f"/knowledge-spaces/{sid}/documents/{k}", headers=h_a)
        self.client.delete(f"/knowledge-spaces/{sid}/members/{self.bob['id']}", headers=h_a)
        self.assertEqual(self.client.delete(f"/knowledge-spaces/{sid}", headers=h_a).status_code, 200)

    # ---- 会话 / 后台任务：领域异常 + 隔离 ----

    def test_conversation_cross_user_404_with_code(self):
        r = self.client.post("/agent", json={"name": f"rt-conv-{self.alice['id']}"}, headers=self.alice["headers"])
        agent_id = r.json()["agent_id"]
        c = self.client.post("/conversation", json={"agent_id": agent_id}, headers=self.alice["headers"])
        self.assertEqual(c.status_code, 200, c.text)
        conv_id = c.json().get("id") or c.json().get("conversation_id")

        got = self.client.get(f"/conversation/{conv_id}", headers=self.bob["headers"])
        self.assertEqual(got.status_code, 404)
        self.assertEqual(got.json().get("code"), "not_found")
        self.assertEqual(self.client.get("/conversation/999999", headers=self.alice["headers"]).status_code, 404)

    def test_background_task_admin_only_endpoint_403(self):
        r = self.client.get("/task/all", headers=self.alice["headers"])
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json().get("code"), "permission_denied")
        self.assertEqual(self.client.get("/task/all", headers=self.admin["headers"]).status_code, 200)

    # ---- Skill：管理员维护，普通用户只读公开技能并绑定使用 ----

    def test_skill_read_routes_work_and_404(self):
        h = self.alice["headers"]
        self.assertEqual(self.client.get("/skill/public", headers=h).status_code, 200)
        self.assertEqual(self.client.get("/skill/999999", headers=h).status_code, 404)
        self.assertEqual(self.client.get("/skill/999999/validate", headers=h).status_code, 404)

    def test_skill_management_routes_are_admin_only(self):
        regular = self.alice["headers"]
        admin = self.admin["headers"]
        management_requests = [
            self.client.get("/skill/", headers=regular),
            self.client.get("/skill/templates", headers=regular),
            self.client.get("/skill/tools", headers=regular),
            self.client.post("/skill/", json={"name": "x", "system_prompt": "x"}, headers=regular),
            self.client.put("/skill/999999", json={"name": "x"}, headers=regular),
            self.client.delete("/skill/999999", headers=regular),
            self.client.post("/skill/999999/translate", headers=regular),
            self.client.get("/skill/999999/export", headers=regular),
            self.client.post("/skill/999999/install", headers=regular),
            # 导入和批量刷新是最危险的入口（能让服务器保存并执行第三方脚本），必须单独确认
            self.client.post("/skill/import", files={"file": ("x.zip", b"PK\x05\x06" + b"\x00" * 18)}, headers=regular),
            self.client.post("/skill/import/github", json={"url": "https://github.com/anthropics/skills"}, headers=regular),
            self.client.post("/skill/admin/reanalyze", headers=regular),
            self.client.get("/skill/999999/versions", headers=regular),
            self.client.post("/skill/999999/versions/1/restore", headers=regular),
        ]
        for response in management_requests:
            self.assertEqual(response.status_code, 403, response.text)

        self.assertEqual(self.client.get("/skill/", headers=admin).status_code, 200)
        self.assertEqual(self.client.get("/skill/templates", headers=admin).status_code, 200)
        self.assertEqual(self.client.get("/skill/tools", headers=admin).status_code, 200)

    def test_admin_skill_list_includes_records_owned_by_other_users(self):
        from models.init_db import SessionLocal, Skill

        db = SessionLocal()
        try:
            skill = Skill(
                user_id=self.alice["id"], name="rt-legacy-owner-skill",
                description="legacy", config_file="user_created/rt-legacy.yml", is_public=0,
            )
            db.add(skill)
            db.commit()
            skill_id = skill.id
        finally:
            db.close()

        response = self.client.get("/skill/", headers=self.admin["headers"])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn(skill_id, [item["id"] for item in response.json()["data"]])

    def test_admin_can_edit_version_and_delete_a_skill_owned_by_another_user(self):
        import yaml
        from models.init_db import SessionLocal, Skill
        from service.skills import loader as skill_loader

        name = f"rt-admin-manage-{uuid.uuid4().hex[:8]}.yml"
        config_file = f"user_created/{name}"
        path = os.path.join(skill_loader.SKILLS_ROOT, config_file)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump({"name": "旧技能", "description": "d", "tools": [{"name": "word_count", "defaults": {}}],
                            "system_prompt": "旧提示词"}, f, allow_unicode=True)
        db = SessionLocal()
        try:
            skill = Skill(user_id=self.alice["id"], name="rt-owned-by-alice", description="d",
                          config_file=config_file, is_public=0)
            db.add(skill)
            db.commit()
            skill_id = skill.id
        finally:
            db.close()

        try:
            admin, alice = self.admin["headers"], self.alice["headers"]
            self.assertEqual(self.client.put(f"/skill/{skill_id}", json={"system_prompt": "别人改"}, headers=alice).status_code, 403)

            r = self.client.put(f"/skill/{skill_id}", json={"system_prompt": "管理员改过", "tool_names": ["word_count"]},
                                headers=admin)
            self.assertEqual(r.status_code, 200, r.text)
            with open(path, encoding="utf-8") as f:
                self.assertIn("管理员改过", f.read())

            r = self.client.get(f"/skill/{skill_id}/versions", headers=admin)
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(len(r.json()["data"]), 1)

            r = self.client.delete(f"/skill/{skill_id}", headers=admin)
            self.assertEqual(r.status_code, 200, r.text)
            # 管理员访问已不存在的技能：一律 404，不能 500
            for suffix in ("", "/validate", "/export"):
                r = self.client.get(f"/skill/{skill_id}{suffix}", headers=admin)
                self.assertEqual(r.status_code, 404, f"{suffix or '/'} -> {r.status_code}")
        finally:
            db = SessionLocal()
            try:
                db.query(Skill).filter(Skill.id == skill_id).delete()
                db.commit()
            finally:
                db.close()
            if os.path.exists(path):
                os.remove(path)

    # ---- 管理员接口隔离 ----

    def test_admin_routes_reject_regular_user(self):
        self.assertEqual(self.client.get("/admin/users", headers=self.alice["headers"]).status_code, 403)
        self.assertEqual(self.client.get("/admin/overview", headers=self.bob["headers"]).status_code, 403)

    def test_admin_routes_allow_admin(self):
        r = self.client.get("/admin/users", headers=self.admin["headers"])
        self.assertEqual(r.status_code, 200, r.text)


if __name__ == "__main__":
    unittest.main()
