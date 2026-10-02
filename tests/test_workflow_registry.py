"""工作流注册表（service/workflows）：定义完整性、部门目录，以及新工作流无需改服务代码即可接入。"""
import json
import typing
import unittest
import uuid
from unittest.mock import AsyncMock, patch

from pydantic import Field
from sqlalchemy import text

from FasdtApi.automation_work import GenerateRequest
from models.init_db import SessionLocal
from service import automation_work_service as svc
from service.automation_spec import extraction_prompt, validate_proposal
from service.exceptions import InvalidInput, PermissionDenied
from service.workflows import REGISTRY, all_workflows, catalog, register, unregister
from service.workflows.base import StrictModel, WorkflowDefinition, WriteRequest
from tests import _route_client as rc
from tests._async_helpers import run_async
from tests.test_enterprise_access import _add_org_member, _add_team_member, _create_org, _create_team


def _model_fields(model):
    return model.model_fields


def _item_model(model, key):
    annotation = model.model_fields[key].annotation
    return typing.get_args(annotation)[0]


class DefinitionCompletenessTest(unittest.TestCase):
    def test_builtin_workflows_registered_in_order(self):
        self.assertEqual([w.id for w in all_workflows()], ["expense", "leave", "procurement", "crm"])

    def test_every_form_field_exists_in_schema(self):
        for workflow in all_workflows():
            with self.subTest(workflow=workflow.id):
                fields = _model_fields(workflow.schema)
                for spec in workflow.form:
                    if spec["type"] in ("note", "sum"):
                        continue
                    self.assertIn(spec["key"], fields)
                    if spec["type"] == "list":
                        item = _item_model(workflow.schema, spec["key"])
                        for sub in spec["fields"]:
                            self.assertIn(sub["key"], item.model_fields)
                self.assertIn("warnings", fields)
                self.assertTrue(workflow.source_label and workflow.example and workflow.hint)
                if workflow.followups:
                    self.assertIn(workflow.followups["key"], fields)

    def test_prompt_only_carries_own_instructions(self):
        crm_prompt = extraction_prompt("crm")
        self.assertIn(REGISTRY["crm"].instructions, crm_prompt)
        self.assertNotIn(REGISTRY["procurement"].instructions, crm_prompt)
        self.assertIn("逐字存在的原文片段", crm_prompt)

    def test_unknown_workflow_rejected(self):
        with self.assertRaises(InvalidInput):
            validate_proposal("not-a-workflow", {}, "x")


class CatalogTest(unittest.TestCase):
    def ids(self, code):
        return [w["id"] for w in catalog(code)["workflows"] if w["available"]]

    def test_department_availability(self):
        self.assertEqual(self.ids("sales"), ["expense", "leave", "crm"])
        self.assertEqual(self.ids("procurement"), ["expense", "leave", "procurement"])
        self.assertEqual(self.ids(None), ["expense", "leave"])

    def test_default_follows_department(self):
        expected = {"sales": "crm", "procurement": "procurement", "hr": "leave", "finance": "expense",
                    "it": "expense", None: "expense"}
        for code, default in expected.items():
            with self.subTest(code=code):
                self.assertEqual(catalog(code)["default_id"], default)

    def test_catalog_lists_unavailable_workflows_for_history_names(self):
        crm = next(w for w in catalog(None)["workflows"] if w["id"] == "crm")
        self.assertFalse(crm["available"])
        self.assertEqual((crm["name"], crm["availability"]), ("沟通记录整理", "销售部门"))


class ContractNote(StrictModel):
    clause: str = Field(min_length=1, max_length=300)
    evidence: str = Field(min_length=1, max_length=500)


class ContractProposal(StrictModel):
    summary: str = Field(min_length=1, max_length=500)
    risks: typing.List[ContractNote] = Field(default_factory=list, max_length=10)
    warnings: typing.List[str] = Field(default_factory=list, max_length=10)


CONTRACT = WorkflowDefinition(
    id="test-contract", title="合同文本 → 风险摘要", name="合同审查", draft_name="合同审查",
    schema=ContractProposal, instructions="提取风险条款。",
    evidence=lambda p: [r.evidence for r in p.risks],
    write=lambda data, work: WriteRequest("/legal/reviews", "legal.write", "create_review_draft",
                                          {"summary": data["summary"], "risks": [r["clause"] for r in data["risks"]]}),
    form=[{"type": "textarea", "key": "summary", "label": "摘要", "required": True},
          {"type": "list", "key": "risks", "label": "风险", "item": "风险", "fields": [
              {"type": "text", "key": "clause", "label": "条款", "required": True},
              {"type": "evidence", "key": "evidence", "label": "依据"}]}],
    source_label="粘贴合同", example="……", hint="……", departments=frozenset({"sales"}), order=99,
)

_AVAILABLE, _WHY = rc.route_tests_available()


def run_db(fn):
    from models.async_db import AsyncSessionLocal

    async def go():
        async with AsyncSessionLocal() as db:
            return await fn(db)
    return run_async(go())


@unittest.skipUnless(_AVAILABLE, _WHY)
class NewWorkflowPluginTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = SessionLocal()
        cls.user = rc.create_user("wf-user")
        cls.outsider = rc.create_user("wf-out")
        cls.org = _create_org(cls.db, "wf-org-" + uuid.uuid4().hex[:6], cls.user["id"])
        cls.team = _create_team(cls.db, cls.org, "wf-sales", cls.user["id"])
        cls.db.execute(text("UPDATE teams SET department_code='sales' WHERE id=:t"), {"t": cls.team})
        cls.db.commit()
        _add_org_member(cls.db, cls.org, cls.user["id"], "member")
        _add_team_member(cls.db, cls.team, cls.user["id"], "member")
        register(CONTRACT)

    @classmethod
    def tearDownClass(cls):
        unregister(CONTRACT.id)
        cls.db.execute(text("DELETE FROM automation_work WHERE team_id=:t"), {"t": cls.team})
        cls.db.commit()
        rc.cleanup()
        cls.db.execute(text("DELETE FROM teams WHERE id=:t"), {"t": cls.team})
        cls.db.execute(text("DELETE FROM organizations WHERE id=:o"), {"o": cls.org})
        cls.db.commit()
        cls.db.close()

    def test_registered_workflow_runs_through_shared_state_machine(self):
        source = "第8条：违约金为合同总额的50%。第9条：争议由甲方所在地法院管辖。"
        answer = {"summary": "违约金比例偏高", "risks": [{"clause": "违约金 50%", "evidence": "违约金为合同总额的50%"}],
                  "warnings": []}
        request = GenerateRequest(request_key=uuid.uuid4(), team_id=self.team, kind="test-contract",
                                  model_name="glm-4", source_text=source)
        with patch.object(svc, "async_get_api_config", AsyncMock(return_value={"api_key": "x"})), \
             patch.object(svc, "enforce_quota_async", AsyncMock()), \
             patch.object(svc, "async_chat_with_usage", AsyncMock(return_value=(json.dumps(answer), {"total_tokens": 9}))) as model:
            work = run_db(lambda db: svc.generate(db, self.user["id"], request))
        self.assertEqual(work["status"], "ready")
        self.assertIn("提取风险条款。", model.call_args.args[3])
        edited = {**answer, "summary": "核对后：违约金比例偏高"}
        with patch.object(svc.hub, "call", return_value={"id": 5, "status": "DRAFT"}) as call:
            result = run_db(lambda db: svc.apply_work(db, self.user["id"], work["id"], edited))
        self.assertEqual(result["status"], "applied")
        args, kwargs = call.call_args
        self.assertEqual((args[1], args[4], args[5]), ("/legal/reviews", ["legal.write"], "create_review_draft"))
        self.assertEqual(kwargs["json_body"], {"summary": "核对后：违约金比例偏高", "risks": ["违约金 50%"]})
        self.assertEqual(kwargs["idempotency_key"], f"automation-{work['id']}")
        stats = run_db(lambda db: svc.history(db, self.user["id"], self.team))["stats"]
        self.assertEqual(stats["by_kind"]["test-contract"], {"total": 1, "applied": 1, "failed": 0, "edited": 1})
        with self.assertRaises(InvalidInput):
            run_db(lambda db: svc.complete_task(db, self.user["id"], work["id"], 0, True))

    def test_catalog_requires_department_membership(self):
        listed = run_db(lambda db: svc.workflows_for_team(db, self.user["id"], self.team))
        self.assertIn("test-contract", [w["id"] for w in listed["workflows"] if w["available"]])
        with self.assertRaises(PermissionDenied):
            run_db(lambda db: svc.workflows_for_team(db, self.outsider["id"], self.team))


if __name__ == "__main__":
    unittest.main()
