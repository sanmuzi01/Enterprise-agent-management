"""工作流的业务系统核对（service/workflows/*._business_checks）：Java 只读查询用替身。"""
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from service import enterprise_hub_client as hub
from service.workflows import REGISTRY
from service.workflows.business_checks import UNAVAILABLE, run_checks
from tests._async_helpers import run_async


def fake_reads(routes):
    async def read(path, user_id, team_id, scope, operation, params=None):
        value = routes[path]
        if isinstance(value, Exception):
            raise value
        return value(params) if callable(value) else value
    return read


def check(kind, data, routes, work=None):
    with patch("service.workflows.business_checks.read", side_effect=fake_reads(routes)):
        return run_async(run_checks(REGISTRY[kind], 1, 2, data, work or SimpleNamespace(customer_id=7)))


def levels(results):
    return [(r["level"], r["text"]) for r in results]


PAPER = {"sku": "PAPER", "name": "A4 纸", "unit": "包", "unitPrice": 20, "onHandQty": 2, "safetyStockQty": 10,
         "belowSafetyStock": True}
CHAIR = {"sku": "CHAIR", "name": "椅子", "unit": "把", "unitPrice": 500, "onHandQty": 50, "safetyStockQty": 5,
         "belowSafetyStock": False}


class ProcurementChecksTest(unittest.TestCase):
    def routes(self, **over):
        base = {"/procurement/products/PAPER": PAPER, "/procurement/products/CHAIR": CHAIR,
                "/procurement/budget": {"remainingAmount": 1000}, "/procurement/requests/mine": []}
        base.update(over)
        return base

    def test_unknown_sku_is_blocker(self):
        results = check("procurement", {"items": [{"sku": "NOPE", "quantity": 1}]},
                        self.routes(**{"/procurement/products/NOPE": hub.EnterpriseHubError(404, "x")}))
        self.assertEqual(results[0]["level"], "blocker")
        self.assertIn("NOPE", results[0]["text"])

    def test_stock_budget_and_amount(self):
        results = levels(check("procurement", {"items": [{"sku": "PAPER", "quantity": 10}, {"sku": "CHAIR", "quantity": 1}]},
                               self.routes()))
        texts = " ".join(t for _, t in results)
        self.assertIn("PAPER 当前低于安全库存", texts)
        self.assertIn(("warning", "CHAIR 当前库存 50 已足够覆盖安全库存和本次数量，请确认是否确需采购"), results)
        self.assertIn("预计金额 ¥700.00，部门剩余采购预算 ¥1,000.00", texts)

    def test_budget_exceeded_and_missing_budget(self):
        over = levels(check("procurement", {"items": [{"sku": "CHAIR", "quantity": 3}]}, self.routes()))
        self.assertTrue(any(l == "warning" and "超出部门剩余采购预算" in t for l, t in over))
        missing = levels(check("procurement", {"items": [{"sku": "PAPER", "quantity": 1}]},
                               self.routes(**{"/procurement/budget": hub.EnterpriseHubError(400, "没有预算")})))
        self.assertTrue(any("没有今年的采购预算记录" in t for _, t in missing))

    def test_duplicate_open_request(self):
        mine = [{"id": 9, "status": "SUBMITTED", "lines": [{"sku": "PAPER"}]},
                {"id": 8, "status": "REJECTED", "lines": [{"sku": "PAPER"}]}]
        results = levels(check("procurement", {"items": [{"sku": "PAPER", "quantity": 1}]},
                               self.routes(**{"/procurement/requests/mine": mine})))
        dup = [t for l, t in results if "可能重复采购" in t]
        self.assertEqual(len(dup), 1)
        self.assertIn("#9（待审批）", dup[0])
        self.assertNotIn("#8", dup[0])

    def test_business_service_down_degrades_to_single_warning(self):
        results = check("procurement", {"items": [{"sku": "PAPER", "quantity": 1}]},
                        self.routes(**{"/procurement/products/PAPER": hub.EnterpriseHubError(503, "down")}))
        self.assertEqual(levels(results), [("warning", UNAVAILABLE)])


class ExpenseChecksTest(unittest.TestCase):
    LINES = {"lines": [{"amount": "260.00", "invoice_no": "G001"}, {"amount": "48.00", "invoice_no": None}]}

    def test_used_invoice_is_blocker_and_missing_invoice_warned(self):
        routes = {"/finance/invoices/usage": lambda p: [{"invoiceNo": "G001", "claimId": 55, "status": "SUBMITTED"}]
                  if p == {"numbers": "G001"} else [],
                  "/finance/budget": {"remainingAmount": 5000}}
        results = levels(check("expense", self.LINES, routes))
        self.assertEqual(results[0][0], "blocker")
        self.assertIn("#55", results[0][1])
        self.assertIn(("warning", "1 条费用没有发票号，审批时可能需要补充票据"), results)
        self.assertIn(("info", "报销合计 ¥308.00，部门剩余报销预算 ¥5,000.00"), results)

    def test_over_budget(self):
        routes = {"/finance/invoices/usage": [], "/finance/budget": {"remainingAmount": 100}}
        self.assertTrue(any("超出部门剩余报销预算" in t for _, t in levels(check("expense", self.LINES, routes))))


class LeaveChecksTest(unittest.TestCase):
    DATA = {"leave_type_code": "annual", "start_date": "2026-10-12", "end_date": "2026-10-14"}

    def routes(self, remaining=5, mine=()):
        return {"/oa/leave/balance": lambda p: [{"leaveTypeCode": "annual", "remainingDays": remaining}] if p == {"year": 2026} else [],
                "/oa/leave/requests/mine": list(mine)}

    def test_balance_ok_and_insufficient(self):
        self.assertIn(("info", "年假余额 5 天，本次 3 天，批准后剩 2 天"), levels(check("leave", self.DATA, self.routes())))
        short = levels(check("leave", self.DATA, self.routes(remaining=2)))
        self.assertTrue(any(l == "warning" and "提交时会被拒绝" in t for l, t in short))

    def test_overlap_ignores_rejected(self):
        mine = [{"id": 3, "status": "SUBMITTED", "startDate": "2026-10-14", "endDate": "2026-10-15"},
                {"id": 4, "status": "REJECTED", "startDate": "2026-10-12", "endDate": "2026-10-12"},
                {"id": 5, "status": "APPROVED", "startDate": "2026-10-20", "endDate": "2026-10-21"}]
        overlaps = [t for _, t in levels(check("leave", self.DATA, self.routes(mine=mine))) if "时间重叠" in t]
        self.assertEqual(len(overlaps), 1)
        self.assertIn("#3", overlaps[0])

    def test_incomplete_leave_skips_lookup(self):
        results = check("leave", {**self.DATA, "start_date": None}, {})
        self.assertEqual(results[0]["level"], "info")


class CrmChecksTest(unittest.TestCase):
    def test_followup_recency_open_opportunities_and_undated_tasks(self):
        last = (datetime.now(timezone.utc) - timedelta(days=12)).isoformat().replace("+00:00", "Z")
        summary = {"name": "星辰科技", "recentFollowUps": [{"createdAt": last}],
                   "opportunities": [{"id": 1, "stage": "PROPOSAL", "amount": 80000},
                                     {"id": 2, "stage": "WON", "amount": 10}]}
        results = levels(check("crm", {"tasks": [{"due_date": None}, {"due_date": "2026-10-08"}]},
                               {"/crm/customers/7": summary}))
        texts = " ".join(t for _, t in results)
        self.assertIn("（12 天前）", texts)
        self.assertIn("在谈商机 #1：方案，金额 ¥80,000.00", texts)
        self.assertNotIn("#2", texts)
        self.assertIn(("warning", "1 条后续待办没有截止日期，建议补充以免遗漏"), results)

    def test_first_followup(self):
        results = levels(check("crm", {"tasks": []}, {"/crm/customers/7": {"name": "新客户", "recentFollowUps": [],
                                                                         "opportunities": []}}))
        self.assertEqual(results, [("info", "新客户 还没有跟进记录，这是首次跟进")])


if __name__ == "__main__":
    unittest.main()
