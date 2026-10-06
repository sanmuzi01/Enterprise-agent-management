"""责任计划整理工作流：期限换算、姓名匹配、模型编造内容的拦截、完整性核对、保存为草稿计划。"""
import json
import unittest
import uuid
from datetime import date
from unittest.mock import AsyncMock, patch

from tests import _route_client as rc  # 先导入：测试用的异步连接不复用（必须在导入 models 之前）

from sqlalchemy import text

from models.init_db import SessionLocal
from service import automation_work_service as works
from service import enterprise_hub_client as hub
from service.automation_spec import parse_answer, validate_proposal
from service.exceptions import InvalidInput
from service.llm import offline_demo
from service.workflows import get_workflow
from service.workflows.date_text import resolve_due
from service.workflows.responsibility import completeness, resolve_name
from tests.test_enterprise_access import _add_org_member, _add_team_member, _create_org, _create_team

_AVAILABLE, _WHY = rc.route_tests_available()

SOURCE = ("10月12日例会纪要：会议决定新版首页本月发布。张三负责新版首页联调，下周五前提交可部署的前端构建包，"
          "验收标准是测试环境回归通过且无阻断问题，由李四验收。王五负责整理客户反馈清单，月底前完成。"
          "关于是否增加会员页的问题，大家还在讨论，暂不决定。")

TODAY = date(2026, 10, 6)   # 周二


class DueTextTest(unittest.TestCase):
    CASES = {
        "2026-10-15": date(2026, 10, 15), "2026年10月15日前": date(2026, 10, 15), "10月15日": date(2026, 10, 15),
        "10月15号之前": date(2026, 10, 15), "10/15": date(2026, 10, 15), "十月十五日": date(2026, 10, 15),
        "今天": TODAY, "明天": date(2026, 10, 7), "后天下班前": date(2026, 10, 8),
        "本周五": date(2026, 10, 9), "这周五前": date(2026, 10, 9), "周五": date(2026, 10, 9), "周一": date(2026, 10, 12),
        "下周五前": date(2026, 10, 16), "下周一": date(2026, 10, 12), "下下周三": date(2026, 10, 21), "下星期日": date(2026, 10, 18),
        "月底": date(2026, 10, 31), "本月底前": date(2026, 10, 31), "下月底": date(2026, 11, 30), "11月底": date(2026, 11, 30),
        "下月5号": date(2026, 11, 5), "本月二十号": date(2026, 10, 20), "15号前": date(2026, 10, 15), "3号": date(2026, 11, 3),
        "三天后": date(2026, 10, 9), "3天内": date(2026, 10, 9), "两周后": date(2026, 10, 20), "年底": date(2026, 12, 31),
        "尽快": None, "近期": None, "下周内": None, "": None, "2月30日": None,
    }

    def test_resolves_only_what_it_can_be_sure_of(self):
        for phrase, expected in self.CASES.items():
            with self.subTest(phrase=phrase):
                self.assertEqual(resolve_due(phrase, TODAY), expected)

    def test_old_month_day_means_next_year_and_this_friday_already_passed_means_next(self):
        self.assertEqual(resolve_due("1月5日", TODAY), date(2027, 1, 5))
        self.assertEqual(resolve_due("周一", date(2026, 10, 9)), date(2026, 10, 12))   # 周五说"周一" → 下周一
        self.assertEqual(resolve_due("周五", date(2026, 10, 10)), date(2026, 10, 16))  # 周六说"周五" → 下周五


class NameMatchTest(unittest.TestCase):
    PEOPLE = [{"user_id": 1, "name": "张三"}, {"user_id": 2, "name": "李四"}, {"user_id": 3, "name": "赵六"}, {"user_id": 4, "name": "赵六"}]

    def test_unique_match(self):
        self.assertEqual(resolve_name("张三", self.PEOPLE, "张三负责联调"), (1, None))

    def test_blank_is_just_unassigned(self):
        self.assertEqual(resolve_name(None, self.PEOPLE, "x"), (None, None))
        self.assertEqual(resolve_name("  ", self.PEOPLE, "x"), (None, None))

    def test_name_missing_from_source_is_treated_as_fabricated(self):
        uid, note = resolve_name("张三", self.PEOPLE, "有人负责联调")
        self.assertIsNone(uid)
        self.assertIn("没有出现在原文中", note)

    def test_duplicate_names_must_be_chosen_by_hand(self):
        uid, note = resolve_name("赵六", self.PEOPLE, "赵六负责联调")
        self.assertIsNone(uid)
        self.assertIn("2 位同名员工", note)

    def test_unknown_person_is_not_matched(self):
        uid, note = resolve_name("钱七", self.PEOPLE, "钱七负责联调")
        self.assertIsNone(uid)
        self.assertIn("没有匹配到", note)


class SchemaTest(unittest.TestCase):
    def good(self, **over):
        task = {"title": "新版首页联调", "responsible_name": "张三", "due_text": "下周五前", "evidence": "张三负责新版首页联调，下周五前提交"}
        task.update(over)
        return {"title": "例会责任计划", "source_type": "MEETING", "tasks": [task]}

    def test_valid_proposal_passes(self):
        data = validate_proposal("responsibility", self.good(), SOURCE)
        self.assertEqual(data["tasks"][0]["priority"], "NORMAL")

    def test_evidence_and_due_text_must_be_verbatim(self):
        with self.assertRaises(InvalidInput):
            validate_proposal("responsibility", self.good(evidence="张三负责联调并且提前完成"), SOURCE)
        with self.assertRaises(InvalidInput):
            validate_proposal("responsibility", self.good(due_text="下下周一"), SOURCE)

    def test_decision_evidence_must_be_verbatim(self):
        value = self.good()
        value["decisions"] = [{"content": "下月发布", "evidence": "会议决定下月发布"}]
        with self.assertRaises(InvalidInput):
            validate_proposal("responsibility", value, SOURCE)

    def test_unknown_fields_and_bad_values_are_rejected(self):
        for over in ({"admin": True}, {"priority": "ASAP"}, {"due_date": "2026-13-45"}, {"due_date": "下周五"}):
            with self.subTest(over=over), self.assertRaises(InvalidInput):
                validate_proposal("responsibility", self.good(**over), SOURCE)

    def test_dependency_indexes_are_checked(self):
        for depends in ([1], [2], [0]):
            with self.subTest(depends=depends), self.assertRaises(InvalidInput):
                validate_proposal("responsibility", self.good(depends_on=depends), SOURCE)

    def test_saving_requires_at_least_one_task_but_parsing_allows_none(self):
        empty = {"title": "闲聊记录", "source_type": "CHAT", "tasks": []}
        self.assertEqual(validate_proposal("responsibility", empty, SOURCE)["tasks"], [])
        with self.assertRaises(InvalidInput):
            validate_proposal("responsibility", empty, SOURCE, for_save=True)

    def test_responsible_cannot_also_collaborate(self):
        with self.assertRaises(InvalidInput):
            validate_proposal("responsibility", self.good(responsible_user_id=5, collaborator_user_ids=[5]), SOURCE)

    def test_model_answer_in_code_fence_is_parsed(self):
        answer = "```json\n" + json.dumps(self.good(), ensure_ascii=False) + "\n```"
        self.assertEqual(parse_answer("responsibility", answer, SOURCE)["tasks"][0]["title"], "新版首页联调")


class OfflineExtractorTest(unittest.TestCase):
    def test_offline_model_output_passes_the_same_validation(self):
        raw = offline_demo._responsibility(SOURCE)
        data = validate_proposal("responsibility", raw, SOURCE)
        self.assertEqual([t["responsible_name"] for t in data["tasks"]], ["张三", "王五"])
        self.assertEqual(data["tasks"][0]["reviewer_name"], "李四")
        self.assertEqual(data["tasks"][0]["due_text"], "下周五")
        self.assertEqual(data["tasks"][0]["acceptance_criteria"], "测试环境回归通过且无阻断问题")
        self.assertEqual(data["tasks"][1]["due_text"], "月底")
        self.assertEqual(len(data["decisions"]), 1)
        self.assertTrue(any("暂不决定" in item for item in data["unresolved"]))   # 仅讨论的内容没有变成责任事项

    def test_workflow_prompt_is_recognised_by_the_offline_model(self):
        from service.automation_spec import extraction_prompt
        answer, _ = offline_demo.respond([{"role": "system", "content": extraction_prompt("responsibility")},
                                          {"role": "user", "content": SOURCE}])
        self.assertEqual(len(json.loads(answer)["tasks"]), 2)

    def test_text_without_named_owners_creates_no_tasks(self):
        raw = offline_demo._responsibility("今天天气不错。大家随便聊聊，下周也许再说。")
        self.assertEqual(raw["tasks"], [])


class CompletenessTest(unittest.TestCase):
    def codes(self, **task):
        base = {"title": "新版首页联调", "responsible_user_id": 1, "reviewer_user_id": 2, "due_date": "2026-10-20",
                "deliverable": "构建包", "acceptance_criteria": "回归通过"}
        base.update(task)
        return [message for _, message in completeness(base, TODAY)]

    def test_complete_task_has_no_problems(self):
        self.assertEqual(self.codes(), [])

    def test_each_gap_is_reported(self):
        self.assertIn("还没有主责员工", self.codes(responsible_user_id=None))
        self.assertIn("没有截止日期", self.codes(due_date=None))
        self.assertTrue(any("已经过了" in m for m in self.codes(due_date="2026-10-01")))
        self.assertIn("没有写明交付物", self.codes(deliverable=" "))
        self.assertIn("没有验收标准", self.codes(acceptance_criteria=None))
        self.assertIn("没有验收人", self.codes(reviewer_user_id=None))
        self.assertIn("主责人不能验收自己的成果", self.codes(reviewer_user_id=1))
        self.assertTrue(any("空泛" in m for m in self.codes(title="做好相关工作")))


@unittest.skipUnless(_AVAILABLE, _WHY)
class ResponsibilityWorkflowFlowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = SessionLocal()
        cls.client = rc.make_client()
        cls.owner = rc.create_user("rwf-owner")
        cls.u = {n: rc.create_user(f"rwf-{n}") for n in ("head", "zhang", "li", "wang", "zhao1", "outsider")}
        # 账号名全局唯一（平台里员工的唯一标识）：用带随机后缀的账号名代替原文里的"张三"等
        tag = uuid.uuid4().hex[:5]
        cls.names = {"张三": f"zs{tag}", "李四": f"ls{tag}", "王五": f"ww{tag}", "赵六": f"zl{tag}"}
        for key, label in (("zhang", "张三"), ("li", "李四"), ("wang", "王五"), ("zhao1", "赵六")):
            cls.db.execute(text("UPDATE `user` SET name=:n WHERE id=:i"), {"n": cls.names[label], "i": cls.u[key]["id"]})
        cls.org = _create_org(cls.db, "rwf-org-" + uuid.uuid4().hex[:6], cls.owner["id"])
        cls.team = _create_team(cls.db, cls.org, "rwf-team", cls.owner["id"])
        cls.db.commit()
        _add_team_member(cls.db, cls.team, cls.u["head"]["id"], "admin")
        for n in ("zhang", "li", "wang"):
            _add_team_member(cls.db, cls.team, cls.u[n]["id"], "member")
        for n in ("head", "zhang", "li", "wang", "zhao1"):
            _add_org_member(cls.db, cls.org, cls.u[n]["id"], "member")

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

    def h(self, name):
        return self.u[name]["headers"]

    def uid(self, name):
        return self.u[name]["id"]

    def s(self, value):
        """把示例里的人名换成这次测试里真实存在的（带随机后缀的）账号名。"""
        text_ = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
        for label, account in self.names.items():
            text_ = text_.replace(label, account)
        return text_ if isinstance(value, str) else json.loads(text_)

    def generate(self, answer, source=None, user="head"):
        source = self.s(SOURCE if source is None else source)
        answer = self.s(answer)
        patches = (patch.object(works, "async_get_api_config", AsyncMock(return_value={"api_key": "t"})),
                   patch.object(works, "enforce_quota_async", AsyncMock()),
                   patch.object(works, "async_chat_with_usage",
                                AsyncMock(return_value=(json.dumps(answer, ensure_ascii=False), {"total_tokens": 10}))))
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        response = self.client.post("/enterprise/automation", headers=self.h(user), json={
            "request_key": str(uuid.uuid4()), "team_id": self.team, "kind": "responsibility", "model_name": "test-model",
            "source_text": source})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def model_answer(self):
        return {"title": "10月例会责任计划", "source_type": "MEETING", "summary": "新版首页本月发布",
                "decisions": [{"content": "新版首页本月发布", "evidence": "会议决定新版首页本月发布"}],
                "tasks": [
                    {"title": "新版首页联调", "responsible_name": "张三", "collaborator_names": ["王五", "钱七"], "reviewer_name": "李四",
                     "due_text": "下周五前", "due_date": "2020-01-01",   # 模型自己编的日期必须被丢弃
                     "deliverable": "可部署的前端构建包", "acceptance_criteria": "测试环境回归通过且无阻断问题",
                     "evidence": "张三负责新版首页联调，下周五前提交可部署的前端构建包"},
                    {"title": "整理客户反馈清单", "responsible_name": "赵六", "due_text": "月底",   # 赵六是企业里的人，但不在这个部门
                     "evidence": "王五负责整理客户反馈清单，月底前完成"},
                    {"title": "补充会员页方案", "responsible_name": "孙八", "evidence": "关于是否增加会员页的问题，大家还在讨论"}],
                "unresolved": ["会员页是否增加尚未决定"]}

    def test_generate_matches_people_and_converts_dates_without_trusting_the_model(self):
        work = self.generate(self.model_answer(), source=SOURCE + "赵六负责对接外部供应商。")
        self.assertEqual(work["status"], "ready", work)
        tasks = work["proposal"]["tasks"]
        first, second, third = tasks
        self.assertEqual(first["responsible_user_id"], self.uid("zhang"))
        self.assertEqual(first["reviewer_user_id"], self.uid("li"))
        self.assertEqual(first["collaborator_user_ids"], [self.uid("wang")])      # 「钱七」不是员工，被丢弃
        self.assertIn("没有出现在原文中", first["match_text"])                    # 钱七原文没有出现
        self.assertNotEqual(first["due_date"], "2020-01-01")
        self.assertRegex(first["due_date"], r"^\d{4}-\d{2}-\d{2}$")
        self.assertIsNone(second["responsible_user_id"])                          # 赵六不是本部门成员：不能被指派
        self.assertIn("没有匹配到", second["match_text"])
        self.assertIsNone(third["responsible_user_id"])
        self.assertIn("没有出现在原文中", third["match_text"])                    # 孙八是凭空出现的名字
        self.assertIsNone(third["due_date"])
        checks = [c["text"] for c in work["business_checks"]]
        self.assertTrue(any("共整理出 3 项责任" in c for c in checks), checks)
        self.assertTrue(any("第 2 项" in c and "还没有主责员工" in c for c in checks))

    def test_save_creates_a_draft_plan_with_server_side_suggestions_and_member_list(self):
        work = self.generate(self.model_answer())
        proposal = work["proposal"]
        proposal["tasks"][1]["responsible_user_id"] = self.uid("wang")            # 负责人手选了本部门的王五
        posted = []

        def fake(method, path, user_id, team_id, scopes, operation, **kwargs):
            posted.append({"method": method, "path": path, "scopes": scopes, "body": kwargs.get("json_body"),
                           "key": kwargs.get("idempotency_key")})
            return {"id": 77, "title": "10月例会责任计划", "status": "DRAFT", "tasks": [{}, {}, {}], "blockerCount": 6}

        with patch.object(hub, "call", side_effect=fake):
            response = self.client.post(f"/enterprise/automation/{work['id']}/apply", headers=self.h("head"), json={"proposal": proposal})
        self.assertEqual(response.status_code, 200, response.text)
        saved = response.json()
        self.assertEqual(saved["status"], "applied")
        self.assertEqual(saved["business_result"]["id"], 77)
        call = posted[0]
        self.assertEqual(call["path"].split("?")[0], "/responsibility/plans")
        self.assertEqual(call["key"], f"automation-{work['id']}")
        body = call["body"]
        self.assertEqual(body["automationWorkId"], work["id"])
        self.assertEqual(body["sourceText"], self.s(SOURCE))
        self.assertEqual(body["tasks"][0]["aiResponsibleUserId"], self.uid("zhang"))   # 来自服务端保存的整理结果
        self.assertIsNone(body["tasks"][1]["aiResponsibleUserId"])                    # AI 没匹配出来，人工选的不算 AI 命中
        self.assertEqual(body["tasks"][1]["responsibleUserId"], self.uid("wang"))
        self.assertIn(self.uid("zhang"), body["eligible"]["memberIds"])
        self.assertNotIn(self.uid("outsider"), body["eligible"]["memberIds"])

    def test_client_cannot_inject_a_fake_ai_suggestion(self):
        work = self.generate(self.model_answer())
        proposal = work["proposal"]
        proposal["tasks"][2]["ai_responsible_user_id"] = self.uid("li")
        posted = []
        with patch.object(hub, "call", side_effect=lambda *a, **k: posted.append(k.get("json_body")) or {"id": 1, "title": "t", "status": "DRAFT", "tasks": []}):
            response = self.client.post(f"/enterprise/automation/{work['id']}/apply", headers=self.h("head"), json={"proposal": proposal})
        # 多余字段被 schema 拒绝（extra=forbid），不会带着伪造的"AI 建议"进到业务系统
        self.assertEqual(response.status_code, 400)
        self.assertFalse(posted)

    def test_business_rejection_unlocks_the_draft_and_outage_keeps_it_retryable(self):
        work = self.generate(self.model_answer())
        proposal = work["proposal"]
        with patch.object(hub, "call", side_effect=hub.EnterpriseHubError(400, "主责员工不是本部门的有效成员")):
            rejected = self.client.post(f"/enterprise/automation/{work['id']}/apply", headers=self.h("head"), json={"proposal": proposal}).json()
        self.assertEqual(rejected["status"], "ready")
        self.assertIn("主责员工不是本部门的有效成员", rejected["error_message"])
        with patch.object(hub, "call", side_effect=hub.EnterpriseHubError(503, "unavailable")):
            outage = self.client.post(f"/enterprise/automation/{work['id']}/apply", headers=self.h("head"), json={"proposal": proposal}).json()
        self.assertEqual(outage["status"], "retry")

    def test_empty_result_cannot_be_saved_and_outsiders_cannot_generate(self):
        empty = {"title": "闲聊记录", "source_type": "CHAT", "tasks": []}
        work = self.generate(empty, source="今天天气不错，大家随便聊聊，没有任何安排。")
        self.assertEqual(work["status"], "ready")
        self.assertTrue(any("没有识别出明确要谁去做" in c["text"] for c in work["business_checks"]))
        with patch.object(hub, "call") as call:
            response = self.client.post(f"/enterprise/automation/{work['id']}/apply", headers=self.h("head"), json={"proposal": work["proposal"]})
        self.assertEqual(response.status_code, 400)
        call.assert_not_called()
        denied = self.client.post("/enterprise/automation", headers=self.h("outsider"), json={
            "request_key": str(uuid.uuid4()), "team_id": self.team, "kind": "responsibility", "model_name": "test-model",
            "source_text": self.s(SOURCE)})
        self.assertEqual(denied.status_code, 403)

    def test_workflow_is_in_the_catalog_for_every_department(self):
        catalog = self.client.get(f"/enterprise/automation/workflows?team_id={self.team}", headers=self.h("zhang")).json()
        item = next(w for w in catalog["workflows"] if w["id"] == "responsibility")
        self.assertTrue(item["available"])
        self.assertEqual(item["availability"], "所有部门")
        tasks_field = next(f for f in item["form"] if f.get("key") == "tasks")
        self.assertEqual({f["options_from"] for f in tasks_field["fields"] if f.get("options_from")}, {"members", "reviewers"})
        self.assertIsNotNone(get_workflow("responsibility").apply)


if __name__ == "__main__":
    unittest.main()
