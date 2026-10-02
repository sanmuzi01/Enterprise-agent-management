"""Source grounding, authorization, durable outputs and safe write retries."""
import json
import unittest
import uuid
from datetime import timedelta
from unittest.mock import AsyncMock, patch

from sqlalchemy import text, update

from FasdtApi.automation_work import GenerateRequest
from models.init_db import AutomationWork, SessionLocal, engine
from service import automation_work_service as svc
from service.automation_spec import parse_answer, validate_proposal
from service.exceptions import Conflict, InvalidInput, NotFound, PermissionDenied
from tests import _route_client as rc
from tests._async_helpers import run_async
from tests.test_enterprise_access import _create_org, _create_team, _add_org_member, _add_team_member
from utils.timeutil import utcnow

SOURCE = ("客户希望试用，10月8日发送方案。出差高铁票260元，发票号G001。"
          "我要申请年假，2026年10月12日至2026年10月14日，家里有事。采购 PAPER-A4 共10件，CHAIR-01 共2把。")
CRM = {"content": "客户希望试用，待发送方案。", "evidence": "客户希望试用",
       "tasks": [{"title": "发送方案", "due_date": None, "evidence": "10月8日发送方案"}], "warnings": []}
EXPENSE = {"lines": [{"category": "TRAVEL", "amount": "260.00", "description": "高铁票",
                       "invoice_no": "G001", "evidence": "出差高铁票260元，发票号G001"}], "warnings": []}
LEAVE = {"leave_type_code": "annual", "start_date": "2026-10-12", "end_date": "2026-10-14",
         "reason": "家里有事", "evidence": "我要申请年假，2026年10月12日至2026年10月14日", "warnings": []}
PURCHASE = {"items": [{"sku": "PAPER-A4", "quantity": 10, "evidence": "PAPER-A4 共10件"},
                      {"sku": "CHAIR-01", "quantity": 2, "evidence": "CHAIR-01 共2把"}], "warnings": []}
RESPONSES = {"crm": CRM, "expense": EXPENSE, "leave": LEAVE, "procurement": PURCHASE}


class ProposalValidationTest(unittest.TestCase):
    def test_valid_expense_preserves_decimal(self):
        self.assertEqual(validate_proposal("expense", EXPENSE, SOURCE)["lines"][0]["amount"], "260.00")

    def test_rejects_fabricated_evidence(self):
        bad = {**CRM, "evidence": "客户承诺购买100台"}
        with self.assertRaises(InvalidInput):
            validate_proposal("crm", bad, SOURCE)

    def test_rejects_model_commands(self):
        with self.assertRaises(InvalidInput):
            validate_proposal("crm", {**CRM, "submit": True}, SOURCE)

    def test_rejects_duplicate_invoice(self):
        with self.assertRaises(InvalidInput):
            validate_proposal("expense", {**EXPENSE, "lines": EXPENSE["lines"] * 2}, SOURCE)

    def test_rejects_invalid_money(self):
        for amount in ("-1", "NaN", "10.123", "1000000000000"):
            with self.subTest(amount=amount), self.assertRaises(InvalidInput):
                validate_proposal("expense", {"lines": [{**EXPENSE["lines"][0], "amount": amount}]}, SOURCE)

    def test_rejects_invalid_date(self):
        with self.assertRaises(InvalidInput):
            validate_proposal("crm", {**CRM, "tasks": [{**CRM["tasks"][0], "due_date": "明天"}]}, SOURCE)

    def test_markdown_json_and_invalid_response(self):
        self.assertEqual(parse_answer("crm", '```json\n' + json.dumps(CRM) + '\n```', SOURCE)["content"], CRM["content"])
        with self.assertRaises(InvalidInput):
            parse_answer("crm", "我已经提交成功了", SOURCE)


class LeaveProposalTest(unittest.TestCase):
    def test_valid_leave(self):
        result = validate_proposal("leave", LEAVE, SOURCE, for_save=True)
        self.assertEqual((result["start_date"], result["end_date"]), ("2026-10-12", "2026-10-14"))

    def test_incomplete_leave_is_reviewable_but_not_savable(self):
        partial = {**LEAVE, "leave_type_code": None, "start_date": None}
        self.assertIsNone(validate_proposal("leave", partial, SOURCE)["leave_type_code"])
        with self.assertRaises(InvalidInput):
            validate_proposal("leave", partial, SOURCE, for_save=True)

    def test_end_before_start_rejected(self):
        with self.assertRaises(InvalidInput):
            validate_proposal("leave", {**LEAVE, "start_date": "2026-10-15"}, SOURCE)

    def test_unknown_leave_type_rejected(self):
        with self.assertRaises(InvalidInput):
            validate_proposal("leave", {**LEAVE, "leave_type_code": "marriage"}, SOURCE)

    def test_relative_date_rejected(self):
        with self.assertRaises(InvalidInput):
            validate_proposal("leave", {**LEAVE, "start_date": "明天"}, SOURCE)

    def test_fabricated_leave_evidence_rejected(self):
        with self.assertRaises(InvalidInput):
            validate_proposal("leave", {**LEAVE, "evidence": "经理已批准我休假"}, SOURCE)


class ProcurementProposalTest(unittest.TestCase):
    def test_valid_purchase(self):
        result = validate_proposal("procurement", PURCHASE, SOURCE, for_save=True)
        self.assertEqual([i["quantity"] for i in result["items"]], [10, 2])

    def test_duplicate_sku_rejected(self):
        dup = {"items": [PURCHASE["items"][0], {**PURCHASE["items"][1], "sku": "PAPER-A4"}], "warnings": []}
        with self.assertRaises(InvalidInput):
            validate_proposal("procurement", dup, SOURCE)

    def test_missing_sku_is_reviewable_but_not_savable(self):
        partial = {"items": [{"sku": None, "quantity": 10, "evidence": "PAPER-A4 共10件"}], "warnings": ["缺少 SKU"]}
        validate_proposal("procurement", partial, SOURCE)
        with self.assertRaises(InvalidInput):
            validate_proposal("procurement", partial, SOURCE, for_save=True)

    def test_quantity_must_be_positive_integer(self):
        for quantity in ("10", 0, -1, 1.5, 1000001):
            bad = {"items": [{**PURCHASE["items"][0], "quantity": quantity}], "warnings": []}
            with self.subTest(quantity=quantity), self.assertRaises(InvalidInput):
                validate_proposal("procurement", bad, SOURCE)

    def test_empty_list_rejected(self):
        with self.assertRaises(InvalidInput):
            validate_proposal("procurement", {"items": [], "warnings": []}, SOURCE)

    def test_model_cannot_add_price_or_submit(self):
        bad = {"items": [{**PURCHASE["items"][0], "unit_price": "1.00"}], "warnings": []}
        with self.assertRaises(InvalidInput):
            validate_proposal("procurement", bad, SOURCE)


def run_db(fn):
    from models.async_db import AsyncSessionLocal
    async def go():
        async with AsyncSessionLocal() as db:
            return await fn(db)
    return run_async(go())


_AVAILABLE, _WHY = rc.route_tests_available()


@unittest.skipUnless(_AVAILABLE, _WHY)
class AutomationWorkIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        AutomationWork.__table__.create(engine, checkfirst=True)
        cls.db = SessionLocal()
        cls.owner = rc.create_user("aw-owner")
        cls.other = rc.create_user("aw-other")
        cls.org = _create_org(cls.db, "aw-org-" + uuid.uuid4().hex[:8], cls.owner["id"])
        cls.team = _create_team(cls.db, cls.org, "aw-sales", cls.owner["id"])
        cls.proc_team = _create_team(cls.db, cls.org, "aw-proc", cls.owner["id"])
        cls.db.execute(text("UPDATE teams SET department_code='sales' WHERE id=:id"), {"id": cls.team})
        cls.db.execute(text("UPDATE teams SET department_code='procurement' WHERE id=:id"), {"id": cls.proc_team})
        cls.db.commit()
        for user in (cls.owner, cls.other):
            _add_org_member(cls.db, cls.org, user["id"], "member")
            _add_team_member(cls.db, cls.team, user["id"], "member")
        _add_team_member(cls.db, cls.proc_team, cls.owner["id"], "member")

    @classmethod
    def tearDownClass(cls):
        cls.db.execute(text("DELETE FROM automation_work WHERE team_id IN (:a, :b)"),
                       {"a": cls.team, "b": cls.proc_team})
        cls.db.commit()
        rc.cleanup()
        cls.db.execute(text("DELETE FROM teams WHERE id IN (:a, :b)"), {"a": cls.team, "b": cls.proc_team})
        cls.db.execute(text("DELETE FROM organizations WHERE id=:id"), {"id": cls.org})
        cls.db.commit()
        cls.db.close()

    def request(self, kind="crm", team=None, **kwargs):
        team = team or (self.proc_team if kind == "procurement" else self.team)
        kwargs.setdefault("customer_id", 42 if kind == "crm" else None)
        return GenerateRequest(request_key=uuid.uuid4(), team_id=team, kind=kind,
                               source_text=SOURCE + uuid.uuid4().hex, model_name="test-model", **kwargs)

    def generate(self, request=None, response=None):
        request = request or self.request()
        response = response or RESPONSES[request.kind]
        with patch.object(svc, "async_get_api_config", AsyncMock(return_value={"api_key": "test"})), \
             patch.object(svc, "enforce_quota_async", AsyncMock()), \
             patch.object(svc, "async_chat_with_usage", AsyncMock(return_value=(json.dumps(response), {"total_tokens": 100}))), \
             patch("service.crm_workspace_service.get_customer_summary_async", AsyncMock(return_value={"id": 42})):
            return run_db(lambda db: svc.generate(db, self.owner["id"], request))

    def test_generate_saves_reviewable_output_without_business_write(self):
        with patch.object(svc.hub, "call") as call:
            work = self.generate()
        self.assertEqual(work["status"], "ready")
        self.assertEqual(work["total_tokens"], 100)
        call.assert_not_called()
        stored = run_db(lambda db: svc.get_work(db, self.owner["id"], work["id"]))
        self.assertIn("发送方案", stored.proposal_json)

    def test_same_request_does_not_call_model_twice(self):
        req = self.request()
        first = self.generate(req)
        with patch.object(svc, "async_chat_with_usage", AsyncMock()) as model:
            second = run_db(lambda db: svc.generate(db, self.owner["id"], req))
        self.assertEqual(first["id"], second["id"])
        model.assert_not_called()
        req.source_text += "变更"
        with self.assertRaises(Conflict):
            run_db(lambda db: svc.generate(db, self.owner["id"], req))

    def test_no_access_to_other_users_work(self):
        work = self.generate()
        with self.assertRaises(NotFound):
            run_db(lambda db: svc.get_work(db, self.other["id"], work["id"]))

    def test_invalid_department_denied_before_model(self):
        req = self.request(); req.team_id = 999999999
        with patch.object(svc, "async_chat_with_usage", AsyncMock()) as model, self.assertRaises(PermissionDenied):
            run_db(lambda db: svc.generate(db, self.owner["id"], req))
        model.assert_not_called()

    def test_restricted_source_denied_before_model(self):
        with patch.object(svc, "async_chat_with_usage", AsyncMock()) as model, self.assertRaises(PermissionDenied):
            run_db(lambda db: svc.generate(db, self.owner["id"], self.request(sensitivity="restricted")))
        model.assert_not_called()

    def test_invalid_model_output_is_persisted_as_failure(self):
        work = self.generate(response={"content": "没有依据"})
        self.assertEqual(work["status"], "failed")
        self.assertEqual(work["total_tokens"], 100)

    def test_apply_creates_only_draft_and_repeat_is_noop(self):
        work = self.generate()
        with patch.object(svc.hub, "call", return_value={"id": 123, "status": "DRAFT"}) as call:
            result = run_db(lambda db: svc.apply_work(db, self.owner["id"], work["id"], CRM))
            repeat = run_db(lambda db: svc.apply_work(db, self.owner["id"], work["id"], CRM))
        self.assertEqual(result["status"], "applied")
        self.assertEqual(repeat["business_result"]["id"], 123)
        self.assertEqual(call.call_count, 1)
        self.assertEqual(call.call_args.args[1], "/crm/customers/42/followups")
        self.assertIn("automation-", call.call_args.kwargs["idempotency_key"])

    def test_retry_keeps_key_and_body_and_blocks_edits(self):
        work = self.generate(self.request("expense"))
        with patch.object(svc.hub, "call", side_effect=TimeoutError) as first:
            result = run_db(lambda db: svc.apply_work(db, self.owner["id"], work["id"], EXPENSE))
        self.assertEqual(result["status"], "retry")
        changed = {"lines": [{**EXPENSE["lines"][0], "amount": "261.00"}]}
        with self.assertRaises(Conflict):
            run_db(lambda db: svc.apply_work(db, self.owner["id"], work["id"], changed))
        with patch.object(svc.hub, "call", return_value={"id": 456}) as second:
            result = run_db(lambda db: svc.apply_work(db, self.owner["id"], work["id"], EXPENSE))
        self.assertEqual(result["status"], "applied")
        self.assertEqual(first.call_args.kwargs["idempotency_key"], second.call_args.kwargs["idempotency_key"])
        self.assertEqual(first.call_args.kwargs["json_body"], second.call_args.kwargs["json_body"])

    def test_revoked_membership_blocks_save(self):
        work = self.generate()
        self.db.execute(text("UPDATE team_members SET status='disabled' WHERE user_id=:u AND team_id=:t"), {"u": self.owner["id"], "t": self.team}); self.db.commit()
        try:
            with patch.object(svc.hub, "call") as call, self.assertRaises(PermissionDenied):
                run_db(lambda db: svc.apply_work(db, self.owner["id"], work["id"], CRM))
            call.assert_not_called()
        finally:
            self.db.execute(text("UPDATE team_members SET status='active' WHERE user_id=:u AND team_id=:t"), {"u": self.owner["id"], "t": self.team}); self.db.commit()

    def test_tasks_persist_and_stats_are_user_scoped(self):
        work = self.generate()
        with patch.object(svc.hub, "call", return_value={"id": 123}):
            run_db(lambda db: svc.apply_work(db, self.owner["id"], work["id"], CRM))
        result = run_db(lambda db: svc.complete_task(db, self.owner["id"], work["id"], 0, True))
        self.assertEqual(result["completed_tasks"], [0])
        other = run_db(lambda db: svc.history(db, self.other["id"], self.team))
        self.assertEqual(other["stats"]["total"], 0)
        self.assertEqual(other["items"], [])

    def test_interrupted_generation_becomes_retryable_failure(self):
        work = self.generate()
        async def interrupt(db):
            await db.execute(update(AutomationWork).where(AutomationWork.id == work["id"]).values(
                status="processing", updated_at=utcnow() - timedelta(minutes=4)))
            await db.commit()
        run_db(interrupt)
        result = run_db(lambda db: svc.history(db, self.owner["id"], self.team))
        recovered = run_db(lambda db: svc.get_work(db, self.owner["id"], work["id"]))
        self.assertEqual(recovered.status, "failed")
        self.assertGreaterEqual(result["stats"]["failed"], 1)

    def test_processing_lease_blocks_duplicate_save_and_allows_recovery(self):
        work = self.generate()
        async def set_claim(db, old=False):
            await db.execute(update(AutomationWork).where(AutomationWork.id == work["id"]).values(
                status="applying", accepted_json=svc.encode(CRM),
                updated_at=utcnow() - timedelta(minutes=6) if old else utcnow()))
            await db.commit()
        run_db(set_claim)
        with self.assertRaises(Conflict):
            run_db(lambda db: svc.apply_work(db, self.owner["id"], work["id"], CRM))
        run_db(lambda db: set_claim(db, True))
        with patch.object(svc.hub, "call", return_value={"id": 123}):
            result = run_db(lambda db: svc.apply_work(db, self.owner["id"], work["id"], CRM))
        self.assertEqual(result["status"], "applied")

    def apply(self, work, proposal, **hub_kwargs):
        with patch.object(svc.hub, "call", **hub_kwargs) as call:
            result = run_db(lambda db: svc.apply_work(db, self.owner["id"], work["id"], proposal))
        return result, call

    # ---- department scoping ----

    def test_crm_only_in_sales_department(self):
        with patch.object(svc, "async_chat_with_usage", AsyncMock()) as model, self.assertRaises(PermissionDenied):
            run_db(lambda db: svc.generate(db, self.owner["id"], self.request("crm", team=self.proc_team)))
        model.assert_not_called()

    def test_procurement_only_in_procurement_department(self):
        with patch.object(svc, "async_chat_with_usage", AsyncMock()) as model, self.assertRaises(PermissionDenied):
            run_db(lambda db: svc.generate(db, self.owner["id"], self.request("procurement", team=self.team)))
        model.assert_not_called()

    def test_leave_and_expense_available_in_any_department(self):
        for kind in ("leave", "expense"):
            for team in (self.team, self.proc_team):
                with self.subTest(kind=kind, team=team):
                    self.assertEqual(self.generate(self.request(kind, team=team))["status"], "ready")

    def test_non_crm_work_rejects_customer(self):
        with self.assertRaises(InvalidInput):
            run_db(lambda db: svc.generate(db, self.owner["id"], self.request("leave", customer_id=42)))

    # ---- OA leave ----

    def test_leave_apply_creates_oa_draft(self):
        work = self.generate(self.request("leave"))
        result, call = self.apply(work, LEAVE, return_value={"id": 77, "status": "DRAFT"})
        self.assertEqual(result["status"], "applied")
        args, kwargs = call.call_args
        self.assertEqual((args[0], args[1], args[4], args[5]),
                         ("POST", "/oa/leave/requests", ["oa.leave.write"], "create_leave_draft"))
        self.assertEqual(kwargs["json_body"], {"leaveTypeCode": "annual", "startDate": "2026-10-12",
                                               "endDate": "2026-10-14", "reason": "家里有事"})

    def test_incomplete_leave_cannot_be_saved(self):
        work = self.generate(self.request("leave"), response={**LEAVE, "start_date": None})
        self.assertEqual(work["status"], "ready")
        with patch.object(svc.hub, "call") as call, self.assertRaises(InvalidInput):
            run_db(lambda db: svc.apply_work(db, self.owner["id"], work["id"], work["proposal"]))
        call.assert_not_called()
        result, _ = self.apply(work, LEAVE, return_value={"id": 78})
        self.assertEqual(result["status"], "applied")
        self.assertTrue(result["edited"])

    def test_rejected_leave_type_returns_to_editable(self):
        work = self.generate(self.request("leave"))
        result, first = self.apply(work, LEAVE, side_effect=svc.hub.EnterpriseHubError(400, "未知的请假类型: annual"))
        self.assertEqual(result["status"], "ready")
        self.assertIn("未知的请假类型", result["error_message"])
        fixed = {**LEAVE, "leave_type_code": "personal"}
        result, second = self.apply(work, fixed, return_value={"id": 79})
        self.assertEqual(result["status"], "applied")
        self.assertEqual(first.call_args.kwargs["idempotency_key"], second.call_args.kwargs["idempotency_key"])
        self.assertEqual(second.call_args.kwargs["json_body"]["leaveTypeCode"], "personal")

    # ---- procurement ----

    def test_procurement_apply_creates_purchase_draft(self):
        work = self.generate(self.request("procurement"))
        result, call = self.apply(work, PURCHASE, return_value={"id": 88, "status": "DRAFT"})
        self.assertEqual(result["status"], "applied")
        args, kwargs = call.call_args
        self.assertEqual((args[1], args[3], args[4]), ("/procurement/requests", self.proc_team, ["procurement.write"]))
        self.assertEqual(kwargs["json_body"], {"lines": [{"sku": "PAPER-A4", "quantity": 10},
                                                         {"sku": "CHAIR-01", "quantity": 2}]})

    def test_unknown_sku_404_returns_to_editable(self):
        work = self.generate(self.request("procurement"))
        result, _ = self.apply(work, PURCHASE, side_effect=svc.hub.EnterpriseHubError(404, "产品不存在: CHAIR-01"))
        self.assertEqual(result["status"], "ready")
        self.assertIn("产品不存在: CHAIR-01", result["error_message"])
        fixed = {"items": [PURCHASE["items"][0]], "warnings": []}
        result, call = self.apply(work, fixed, return_value={"id": 89})
        self.assertEqual(result["status"], "applied")
        self.assertEqual(call.call_args.kwargs["json_body"]["lines"], [{"sku": "PAPER-A4", "quantity": 10}])

    def test_missing_customer_404_returns_to_editable(self):
        work = self.generate()
        result, _ = self.apply(work, CRM, side_effect=svc.hub.EnterpriseHubError(404, "客户不存在"))
        self.assertEqual(result["status"], "ready")
        self.assertIn("客户不存在", result["error_message"])

    def test_rejection_after_ambiguous_attempt_unlocks(self):
        # A 4xx for the key proves Java rolled back; nothing exists under it, so editing is safe.
        work = self.generate(self.request("procurement"))
        result, _ = self.apply(work, PURCHASE, side_effect=TimeoutError)
        self.assertEqual(result["status"], "retry")
        result, _ = self.apply(work, PURCHASE, side_effect=svc.hub.EnterpriseHubError(404, "产品不存在: CHAIR-01"))
        self.assertEqual(result["status"], "ready")
        result, _ = self.apply(work, {"items": [PURCHASE["items"][0]], "warnings": []}, return_value={"id": 90})
        self.assertEqual(result["status"], "applied")

    def test_conflict_and_server_errors_stay_locked(self):
        for code in (409, 500, 503):
            with self.subTest(code=code):
                work = self.generate(self.request("expense"))
                result, _ = self.apply(work, EXPENSE, side_effect=svc.hub.EnterpriseHubError(code, "busy"))
                self.assertEqual(result["status"], "retry")
                with self.assertRaises(Conflict):
                    run_db(lambda db: svc.apply_work(db, self.owner["id"], work["id"],
                                                     {"lines": [{**EXPENSE["lines"][0], "amount": "1.00"}]}))


if __name__ == "__main__":
    unittest.main()
