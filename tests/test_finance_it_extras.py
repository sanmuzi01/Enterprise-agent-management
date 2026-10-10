"""财务与 IT 补齐：发票识别（置信度、低置信度必须人工确认、重复发票 / 重复文件拦截、金额合计校验）、
费用标准（超标准必须写说明、缺票拦截）、ERP 推送幂等与权限、IT 自助（看文章不等于解决、明确确认才计入、
转工单关联会话、重开影响质量指标、低解决率文章进维护清单）。业务系统（Java）、文件解析、外部 ERP 都用替身。"""
import json
import unittest
import uuid
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import text

from service import expense_policy_service, invoice_parser, it_self_service
from service.exceptions import Conflict, InvalidInput, PermissionDenied
from tests import _route_client as rc
from tests._async_helpers import run_async

_AVAILABLE, _WHY = rc.route_tests_available()


def uscc(base17: str) -> str:
    """按 GB 32100 给 17 位本体算出校验码，拼成合法的统一社会信用代码。"""
    total = sum(invoice_parser._USCC_CHARS.index(c) * w for c, w in zip(base17, invoice_parser._USCC_WEIGHTS))
    return base17 + invoice_parser._USCC_CHARS[(31 - total % 31) % 31]


BUYER_ID, SELLER_ID = uscc("91110000MA01ABCD1"), uscc("91310115MA1K3XY02")


def invoice_text(buyer="测试科技有限公司", number="24442000000123456789", net="283.02", tax="16.98", total="300.00",
                 day="2026年09月28日"):
    return (f"电子发票（普通发票）\n发票号码：{number}\n开票日期：{day}\n"
            f"购买方信息 名称：{buyer}  统一社会信用代码/纳税人识别号：{BUYER_ID}\n"
            f"销售方信息 名称：上海海鲜餐饮有限公司  统一社会信用代码/纳税人识别号：{SELLER_ID}\n"
            f"项目名称 餐饮服务\n合 计 ¥{net} ¥{tax}\n价税合计（大写） 叁佰圆整 （小写）¥{total}\n")


# ------------------------------------------------------------------ 发票解析

class InvoiceParserTest(unittest.TestCase):
    today = date(2026, 10, 10)

    def test_text_invoice_fields_and_high_confidence(self):
        parsed = invoice_parser.parse(invoice_text())
        f, c = parsed["fields"], parsed["confidence"]
        self.assertEqual((f["invoice_number"], f["issued_at"], f["total_amount"]), ("24442000000123456789", "2026-09-28", "300.00"))
        self.assertEqual((f["buyer_name"], f["seller_name"]), ("测试科技有限公司", "上海海鲜餐饮有限公司"))
        self.assertEqual((f["buyer_tax_id"], f["seller_tax_id"]), (BUYER_ID, SELLER_ID))
        self.assertEqual((f["amount_without_tax"], f["tax_amount"]), ("283.02", "16.98"))
        conf, checks = invoice_parser.validate(f, c, today=self.today, company_name="测试科技有限公司")
        self.assertEqual([x for x in checks if x["level"] != "info"], [])
        self.assertEqual(invoice_parser.needs_review(conf, f), [])

    def test_ocr_fields_all_need_review(self):
        parsed = invoice_parser.parse(invoice_text(), from_ocr=True)
        review = invoice_parser.needs_review(parsed["confidence"], parsed["fields"])
        self.assertIn("invoice_number", review)
        self.assertIn("total_amount", review)
        self.assertTrue(all(v <= 0.8 for v in parsed["confidence"].values()))

    def test_amount_mismatch_and_bad_number_lower_confidence(self):
        parsed = invoice_parser.parse(invoice_text(total="350.00", number="1234567"))
        conf, checks = invoice_parser.validate(parsed["fields"], parsed["confidence"], today=self.today)
        codes = {c["code"] for c in checks}
        self.assertIn("AMOUNT_MISMATCH", codes)
        self.assertLess(conf["total_amount"], invoice_parser.REVIEW_THRESHOLD)
        parsed = invoice_parser.parse(invoice_text(number="12345678"))      # 8 位号码却没有发票代码
        conf, checks = invoice_parser.validate(parsed["fields"], parsed["confidence"], today=self.today)
        self.assertIn("INVOICE_NUMBER_FORMAT", {c["code"] for c in checks})
        self.assertIn("invoice_number", invoice_parser.needs_review(conf, parsed["fields"]))

    def test_tax_id_checksum_and_buyer_and_supplier(self):
        self.assertTrue(invoice_parser.uscc_valid(BUYER_ID))
        bad = BUYER_ID[:-1] + ("0" if BUYER_ID[-1] != "0" else "1")
        self.assertFalse(invoice_parser.uscc_valid(bad))
        parsed = invoice_parser.parse(invoice_text(buyer="别人家公司"))
        _, checks = invoice_parser.validate(parsed["fields"], parsed["confidence"], today=self.today,
                                            company_name="测试科技有限公司", known_seller={"name": "上海海鲜餐饮", "count": 3})
        codes = {c["code"] for c in checks}
        self.assertIn("BUYER_MISMATCH", codes)
        self.assertIn("SELLER_NAME_CHANGED", codes)


# ------------------------------------------------------------------ 费用标准

class PolicyRulesTest(unittest.TestCase):
    def rule(self, id_, category="MEAL", limit="200", city=None, level=None, receipt=1, start=None, end=None):
        return SimpleNamespace(id=id_, category=category, amount_limit=limit, city_level=city, employee_level=level,
                               receipt_required=receipt, approval_level="finance", effective_from=start, effective_to=end)

    def test_most_specific_rule_and_limits(self):
        rules = [self.rule(1, limit="200"), self.rule(2, limit="300", city="tier1"), self.rule(3, category="TAXI", limit=None)]
        on = date(2026, 10, 10)
        results = expense_policy_service.check_lines(rules, [
            {"category": "MEAL", "amount": 280, "invoiceNo": "1"},
            {"category": "MEAL", "amount": 120, "invoiceNo": ""},
        ], city_level="tier1", employee_level="staff", on=on)
        self.assertEqual((results[0]["rule_id"], results[0]["status"]), (2, "ok"))          # 一线城市用 300 的标准
        self.assertEqual(results[1]["status"], "missing_receipt")
        results = expense_policy_service.check_lines(rules, [{"category": "MEAL", "amount": 280, "invoiceNo": "1"}],
                                                     city_level="tier2", employee_level="staff", on=on)
        self.assertEqual((results[0]["status"], results[0]["over_by"]), ("over_limit", "80"))

    def test_effective_dates(self):
        rules = [self.rule(1, limit="100", start="2026-11-01")]
        results = expense_policy_service.check_lines(rules, [{"category": "MEAL", "amount": 500, "invoiceNo": "1"}],
                                                     city_level=None, employee_level="staff", on=date(2026, 10, 10))
        self.assertIsNone(results[0]["rule_id"])

    def test_validation(self):
        with self.assertRaises(InvalidInput):
            expense_policy_service._validate({"category": "PARTY"})
        with self.assertRaises(InvalidInput):
            expense_policy_service._validate({"category": "MEAL", "amount_limit": "-1"})


# ------------------------------------------------------------------ IT 指标

def session(id_, articles=(1, 2), solved=None, article=None, ticket=None, reopened=0):
    return SimpleNamespace(id=id_, article_ids_json=json.dumps([{"id": a, "title": f"文章{a}"} for a in articles]),
                           confirmed_solved=solved, solved_article_id=article, converted_ticket_id=ticket, reopened=reopened)


class ItMetricsTest(unittest.TestCase):
    def test_viewing_is_not_solving(self):
        sessions = [session(1), session(2)]
        feedback = [SimpleNamespace(article_id=1, viewed_at=1, helpful=None)]
        m = it_self_service.compute_metrics(sessions, feedback)
        self.assertEqual((m["confirmed_solved"], m["self_solve_rate"]), (0, 0.0))
        self.assertEqual(m["no_feedback"], 2)

    def test_only_explicit_confirmation_counts_and_reopen_reduces(self):
        sessions = [session(1, solved=1, article=1), session(2, solved=1, article=1, reopened=1),
                    session(3, solved=0, ticket=55), session(4)]
        m = it_self_service.compute_metrics(sessions, [])
        self.assertEqual((m["confirmed_solved"], m["converted_to_ticket"]), (2, 1))
        self.assertEqual(m["self_solve_rate"], 0.25)                    # 2 个已解决里 1 个被重新打开
        self.assertEqual(m["reopen_rate"], round(1 / 3, 4))
        article1 = next(a for a in m["articles"] if a["id"] == 1)
        self.assertEqual((article1["solved"], article1["reopened"]), (2, 1))

    def test_same_problem_needs_overlap_not_just_category(self):
        solved = SimpleNamespace(question="忘记密码了登录不了", article_ids_json=json.dumps([{"id": 1}]))
        self.assertFalse(it_self_service.same_problem(solved, "VPN 连不上，远程办公无法连接", {2, 3}))   # 都是“故障”但不是同一个问题
        self.assertTrue(it_self_service.same_problem(solved, "密码忘记了，还是登录不了"))
        self.assertTrue(it_self_service.same_problem(solved, "别的说法", {1}))                            # 推荐了同一篇文章

    def test_low_solve_rate_article_goes_to_maintenance(self):
        sessions = [session(i, articles=(9,)) for i in range(6)] + [session(10, articles=(9,), solved=1, article=9)]
        feedback = [SimpleNamespace(article_id=9, viewed_at=1, helpful=0) for _ in range(3)]
        m = it_self_service.compute_metrics(sessions, feedback)
        self.assertEqual([a["id"] for a in m["maintenance"]], [9])
        self.assertEqual(len(m["maintenance"][0]["reasons"]), 2)


# ------------------------------------------------------------------ 数据库

def run_db(fn):
    from models.async_db import AsyncSessionLocal

    async def go():
        async with AsyncSessionLocal() as db:
            return await fn(db)
    return run_async(go())


@unittest.skipUnless(_AVAILABLE, _WHY)
class FinanceItDbTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from models.init_db import SessionLocal
        from tests.test_enterprise_access import _add_org_member, _add_team_member, _create_org, _create_team
        cls.db = SessionLocal()
        cls.user = rc.create_user("fin-emp")
        cls.other = rc.create_user("fin-oth")
        cls.company = "测试科技有限公司" + uuid.uuid4().hex[:4]
        cls.org = _create_org(cls.db, cls.company, cls.user["id"])
        cls.team = _create_team(cls.db, cls.org, "fin-team", cls.user["id"])
        cls.sales = _create_team(cls.db, cls.org, "fin-sales", cls.other["id"])
        cls.db.execute(text("UPDATE teams SET department_code='sales' WHERE id=:t"), {"t": cls.sales})
        cls.db.commit()
        _add_org_member(cls.db, cls.org, cls.user["id"], "member")
        _add_org_member(cls.db, cls.org, cls.other["id"], "member")
        _add_team_member(cls.db, cls.team, cls.user["id"], "member")
        _add_team_member(cls.db, cls.sales, cls.other["id"], "member")

    @classmethod
    def tearDownClass(cls):
        cls.clean()
        rc.cleanup()
        cls.db.execute(text("DELETE FROM teams WHERE id IN (:a, :b)"), {"a": cls.team, "b": cls.sales})
        cls.db.execute(text("DELETE FROM organizations WHERE id=:o"), {"o": cls.org})
        cls.db.commit()
        cls.db.close()

    @classmethod
    def clean(cls):
        cls.db.commit()
        users = {"a": cls.user["id"], "b": cls.other["id"]}
        cls.db.execute(text("DELETE FROM invoice_extraction WHERE user_id IN (:a, :b)"), users)
        cls.db.execute(text("DELETE FROM it_article_feedback WHERE user_id IN (:a, :b)"), users)
        cls.db.execute(text("DELETE FROM it_self_service_session WHERE user_id IN (:a, :b)"), users)
        cls.db.execute(text("DELETE FROM expense_policy_rule WHERE organization_id=:o"), {"o": cls.org})
        cls.db.execute(text("DELETE FROM erp_export WHERE organization_id=:o"), {"o": cls.org})
        cls.db.commit()

    def setUp(self):
        self.clean()

    def extract(self, body_text, ocr=False, content=None):
        from service import invoice_service
        content = content or (b"%PDF-1.4\n" + uuid.uuid4().hex.encode())
        with patch.object(invoice_service.document_intake, "extract_text", AsyncMock(return_value={"text": body_text, "ocr": ocr})), \
                patch.object(invoice_service, "_used_in_claims", AsyncMock(return_value=None)):
            return run_db(lambda db: invoice_service.extract(db, self.user["id"], self.team, "inv.pdf", content))

    def confirm(self, extraction_id, values=None, fields=()):
        from service import invoice_service
        with patch.object(invoice_service, "_used_in_claims", AsyncMock(return_value=None)):
            return run_db(lambda db: invoice_service.confirm(db, self.user["id"], extraction_id, values or {}, list(fields)))

    # -------- 发票

    def test_text_invoice_is_confirmed_and_ocr_needs_review(self):
        good = self.extract(invoice_text(buyer=self.company))
        self.assertEqual(good["status"], "confirmed")
        ocr = self.extract(invoice_text(buyer=self.company, number="24442000000999999999"), ocr=True)
        self.assertEqual(ocr["status"], "needs_review")
        self.assertIn("total_amount", ocr["needs_review"])
        with self.assertRaisesRegex(InvalidInput, "还有字段没确认"):
            self.confirm(ocr["id"])
        done = self.confirm(ocr["id"], fields=ocr["needs_review"])
        self.assertEqual(done["status"], "confirmed")

    def test_low_confidence_invoice_cannot_be_used_for_a_claim(self):
        from service import invoice_service
        ocr = self.extract(invoice_text(buyer=self.company, number="24442000000888888888"), ocr=True)
        with self.assertRaisesRegex(InvalidInput, "没确认"):
            run_db(lambda db: invoice_service.take_for_claim(db, self.user["id"], [ocr["id"]]))

    def test_duplicate_invoice_and_duplicate_file_are_blocked(self):
        first = self.extract(invoice_text(buyer=self.company, number="24442000000777777777"))
        self.assertEqual(first["status"], "confirmed")
        again = self.extract(invoice_text(buyer=self.company, number="24442000000777777777"))
        self.assertIn("DUPLICATE_INVOICE", {c["code"] for c in again["checks"]})
        self.assertEqual(again["status"], "needs_review")
        with self.assertRaisesRegex(InvalidInput, "已经确认"):
            self.confirm(again["id"], fields=invoice_parser.FIELDS)
        same_bytes = b"%PDF-1.4 same file"
        self.extract(invoice_text(buyer=self.company, number="24442000000666666666"), content=same_bytes)
        dup = self.extract(invoice_text(buyer=self.company, number="24442000000555555555"), content=same_bytes)
        self.assertIn("DUPLICATE_FILE", {c["code"] for c in dup["checks"]})

    def test_mismatched_total_cannot_be_confirmed_until_fixed(self):
        bad = self.extract(invoice_text(buyer=self.company, number="24442000000444444444", total="350.00"))
        self.assertEqual(bad["status"], "needs_review")
        with self.assertRaisesRegex(InvalidInput, "≠"):
            self.confirm(bad["id"], fields=invoice_parser.FIELDS)
        fixed = self.confirm(bad["id"], values={"total_amount": "300.00"}, fields=invoice_parser.FIELDS)
        self.assertEqual(fixed["status"], "confirmed")
        self.assertIn("total_amount", fixed["corrected_fields"])

    # -------- 报销 + 费用标准

    def test_expense_policy_and_invoice_in_claim(self):
        from service import finance_workspace_service as fin
        run_db(lambda db: expense_policy_service.save_rule(db, self.user["id"], {"category": "MEAL", "amount_limit": "200"}))
        # 测试企业不是“最小编号的企业”时，规则存在默认企业里：这里直接用规则对象检查
        self.db.execute(text("UPDATE expense_policy_rule SET organization_id=:o WHERE created_by=:u"),
                        {"o": self.org, "u": self.user["id"]})
        self.db.commit()
        invoice = self.extract(invoice_text(buyer=self.company, number="24442000000333333333"))
        calls = []

        def fake_call(method, path, user_id, team_id, scopes, operation, json_body=None, idempotency_key=None):
            calls.append(json_body)
            return {"id": 9901, "status": "DRAFT"}
        with patch("service.expense_policy_service._org", AsyncMock(return_value=self.org)), \
                patch("service.finance_workspace_service.hub.call", side_effect=fake_call):
            with self.assertRaisesRegex(InvalidInput, "超标准说明"):
                run_db(lambda db: fin.create_my_expense_draft_async(db, self.user["id"], self.team, [
                    {"category": "MEAL", "amount": 300, "invoiceExtractionId": invoice["id"]}]))
            with self.assertRaisesRegex(InvalidInput, "必须有发票"):
                run_db(lambda db: fin.create_my_expense_draft_async(db, self.user["id"], self.team, [
                    {"category": "MEAL", "amount": 50}]))
            self.assertEqual(calls, [])                                       # 没过检查：业务系统一次都没调
            with self.assertRaisesRegex(InvalidInput, "超过了发票金额"):
                run_db(lambda db: fin.create_my_expense_draft_async(db, self.user["id"], self.team, [
                    {"category": "MEAL", "amount": 500, "invoiceExtractionId": invoice["id"], "overStandardReason": "x"}]))
            claim = run_db(lambda db: fin.create_my_expense_draft_async(db, self.user["id"], self.team, [
                {"category": "MEAL", "amount": 300, "invoiceExtractionId": invoice["id"], "overStandardReason": "接待客户 6 人"}]))
        self.assertEqual(claim["id"], 9901)
        line = calls[0]["lines"][0]
        self.assertEqual(line["invoiceNo"], "24442000000333333333")           # 发票号由识别结果带入
        self.assertIn("超标准 100", line["description"])
        self.assertIn("接待客户 6 人", line["description"])
        self.db.commit()
        status = self.db.execute(text("SELECT status, claim_id FROM invoice_extraction WHERE id=:i"), {"i": invoice["id"]}).one()
        self.assertEqual(tuple(status), ("used", 9901))

    # -------- ERP

    def test_erp_push_is_idempotent_and_needs_finance_staff(self):
        from service import erp_connector
        sent = []
        voucher = {"id": 77, "status": "POSTED", "voucherNo": "记-001", "entries": [], "expenseClaimId": 5}
        env = {"ERP_WEBHOOK_URL": "https://erp.example.test/hook", "ERP_WEBHOOK_SECRET": "test-secret"}
        with patch.dict("os.environ", env), \
                patch.object(erp_connector.vouchers, "get_voucher_async", AsyncMock(return_value=voucher)), \
                patch.object(erp_connector, "_send", side_effect=lambda body, key: sent.append(key) or "ERP-9"):
            first = run_db(lambda db: erp_connector.push_voucher(db, self.user["id"], self.team, 77))
            again = run_db(lambda db: erp_connector.push_voucher(db, self.user["id"], self.team, 77))
        self.assertEqual((first["status"], first["erp_document_id"], first["duplicate"]), ("sent", "ERP-9", False))
        self.assertTrue(again["duplicate"])
        self.assertEqual(sent, ["voucher-77"])                                 # ERP 只收到一次
        with patch.dict("os.environ", env), \
                patch.object(erp_connector.vouchers, "get_voucher_async", AsyncMock(return_value={**voucher, "id": 78, "status": "DRAFT"})):
            with self.assertRaises(Conflict):
                run_db(lambda db: erp_connector.push_voucher(db, self.user["id"], self.team, 78))
        with patch.dict("os.environ", env):                                    # 不是财务部门的人：真实权限检查拒绝
            with self.assertRaises(PermissionDenied):
                run_db(lambda db: erp_connector.push_voucher(db, self.other["id"], self.sales, 77))

    def test_erp_failure_can_be_retried_with_same_key(self):
        from service import erp_connector
        keys = []

        def flaky(body, key):
            keys.append(key)
            if len(keys) == 1:
                raise RuntimeError("ERP 返回 HTTP 503")
            return "ERP-10"
        env = {"ERP_WEBHOOK_URL": "https://erp.example.test/hook", "ERP_WEBHOOK_SECRET": "test-secret"}
        with patch.dict("os.environ", env), \
                patch.object(erp_connector.vouchers, "get_voucher_async", AsyncMock(return_value={"id": 79, "status": "POSTED"})), \
                patch.object(erp_connector, "_send", side_effect=flaky):
            with self.assertRaisesRegex(InvalidInput, "推送失败"):
                run_db(lambda db: erp_connector.push_voucher(db, self.user["id"], self.team, 79))
            ok = run_db(lambda db: erp_connector.push_voucher(db, self.user["id"], self.team, 79))
        self.assertEqual((ok["status"], ok["attempts"]), ("sent", 2))
        self.assertEqual(keys, ["voucher-79", "voucher-79"])

    # -------- IT 自助

    def it_patches(self):
        from service import it_service
        suggestion = {"classification": {"category": "INCIDENT", "priority": "NORMAL"},
                      "articles": [{"id": 11, "title": "打印机脱机", "steps": "1. 重启"}, {"id": 12, "title": "驱动", "steps": "…"}]}
        counter = iter(range(5000, 6000))
        return [patch.object(it_service, "suggest_solutions_async", AsyncMock(return_value=suggestion)),
                patch.object(it_service, "call_hub", AsyncMock(side_effect=lambda *a, **k: {"id": next(counter), "status": "OPEN"})),
                patch.object(it_service, "_enrich_detail", AsyncMock(side_effect=lambda db, t: t))]

    def test_view_and_rate_at_the_same_time(self):
        """前端“打开文章”和“评价”几乎同时发出：两个请求都要写同一条反馈，不能撞唯一约束。"""
        import asyncio
        from models.async_db import AsyncSessionLocal
        patches = self.it_patches()
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        uid = self.user["id"]
        s = run_db(lambda db: it_self_service.start(db, uid, self.team, "打印机脱机了"))

        async def both():
            async def view():
                async with AsyncSessionLocal() as db:
                    await it_self_service.view_article(db, uid, s["id"], 11)

            async def rate():
                async with AsyncSessionLocal() as db:
                    await it_self_service.rate_article(db, uid, s["id"], 11, True)
            await asyncio.gather(view(), rate())
        run_async(both())
        self.db.commit()
        row = self.db.execute(text("SELECT COUNT(*), MAX(helpful), MAX(viewed_at IS NOT NULL) FROM it_article_feedback "
                                   "WHERE session_id=:s"), {"s": s["id"]}).one()
        self.assertEqual(tuple(row), (1, 1, 1))

    def test_self_service_flow_and_metrics(self):
        from service import it_service
        patches = self.it_patches()
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        uid = self.user["id"]
        s1 = run_db(lambda db: it_self_service.start(db, uid, self.team, "打印机脱机了"))
        run_db(lambda db: it_self_service.view_article(db, uid, s1["id"], 11))
        self.db.commit()
        solved = self.db.execute(text("SELECT confirmed_solved FROM it_self_service_session WHERE id=:i"), {"i": s1["id"]}).scalar()
        self.assertIsNone(solved)                                             # 看过文章不等于解决
        done = run_db(lambda db: it_self_service.mark_solved(db, uid, s1["id"], 11))
        self.assertTrue(done["confirmed_solved"])

        s2 = run_db(lambda db: it_self_service.start(db, uid, self.team, "还是打不了"))
        converted = run_db(lambda db: it_self_service.convert_to_ticket(db, uid, s2["id"], self.team, "INCIDENT", "NORMAL",
                                                                       "打印机", "还是脱机"))
        ticket_id = converted["ticket"]["id"]
        self.assertEqual(converted["session"]["converted_ticket_id"], ticket_id)   # 工单和自助会话对上
        self.db.commit()
        reopened = self.db.execute(text("SELECT reopened FROM it_self_service_session WHERE id=:i"), {"i": s1["id"]}).scalar()
        self.assertEqual(reopened, 1)                                        # 7 天内同类问题又报修：记为重新打开
        with self.assertRaises(Conflict):
            run_db(lambda db: it_self_service.mark_solved(db, uid, s2["id"]))

        run_db(lambda db: it_service.reopen_ticket_async(db, uid, ticket_id, "又坏了"))
        self.db.commit()
        self.assertEqual(self.db.execute(text("SELECT reopened FROM it_self_service_session WHERE id=:i"), {"i": s2["id"]}).scalar(), 1)

        with self.assertRaises(PermissionDenied):                             # 指标只给 IT 部门人员看
            run_db(lambda db: it_self_service.metrics(db, uid, self.team))
        with patch.object(it_service, "_desk", AsyncMock(return_value={"scope": [self.team]})):
            m = run_db(lambda db: it_self_service.metrics(db, uid, self.team))
        self.assertEqual((m["sessions"], m["confirmed_solved"], m["converted_to_ticket"]), (2, 1, 1))
        self.assertEqual(m["self_solve_rate"], 0.0)                           # 唯一的“已解决”后来又报修了
        self.assertEqual(m["reopen_rate"], 1.0)


if __name__ == "__main__":
    unittest.main()
