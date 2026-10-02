"""AI 工作成果的真实端到端验收：Python 服务层 + MySQL + Java 业务服务（HMAC 签名、
幂等、事务）全部是真的，只把模型调用换成确定性替身——模型输出的质量不在这里验，
这里验的是"整理结果 → 人工修改 → 业务草稿"这条链路在真实服务之间是否正确、
重试是否不产生重复草稿、越权是否被拦住。

前置：MySQL 已启动，Java 业务服务在 ENTERPRISE_HUB_BASE_URL（默认 127.0.0.1:8090）运行。
用法：.venv\\Scripts\\python.exe scripts\\e2e_automation_workflows.py
脚本自建隔离的企业/部门/用户/客户/产品，结束时全部清理。
"""
import asyncio
import json
import os
import pathlib
import sys
import uuid
from unittest.mock import AsyncMock, patch

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests import _route_client as rc  # noqa: E402  先导入：设置非生产环境变量

from dotenv import load_dotenv  # noqa: E402
from sqlalchemy import bindparam, create_engine, text  # noqa: E402

load_dotenv(ROOT / ".env")

from FasdtApi.automation_work import GenerateRequest  # noqa: E402
from models.async_db import AsyncSessionLocal, async_engine  # noqa: E402
from models.init_db import SessionLocal  # noqa: E402
from service import automation_work_service as svc  # noqa: E402
from service import enterprise_hub_client as hub  # noqa: E402
from service.exceptions import AppError, NotFound, PermissionDenied  # noqa: E402
from tests.test_enterprise_access import _add_org_member, _add_team_member, _create_org, _create_team  # noqa: E402

MODEL = "glm-4"
SUFFIX = uuid.uuid4().hex[:8].upper()
SKU = f"E2E-{SUFFIX}"
BAD_SKU = f"NO-SUCH-{SUFFIX}"
INVOICE_A, INVOICE_B = f"A{SUFFIX}", f"B{SUFFIX}"
SOURCES = {
    "crm": "2026年10月1日与客户沟通，对方希望先试用。约定2026年10月8日发送方案；预算尚未确定。",
    "expense": f"10月1日出差高铁票260元，发票号{INVOICE_A}；出租车48元，暂无发票。",
    "expense_b": f"10月2日出差高铁票260元，发票号{INVOICE_B}；出租车48元，暂无发票。",
    "leave": "我要申请年假，2026年10月12日至2026年10月14日，原因是家庭事务。",
    "procurement": f"行政部需要采购 {SKU} 共3件；另需 {BAD_SKU} 共2件。",
}


def proposal_for(kind, source):
    if kind == "crm":
        return {"content": "客户希望先试用，待发送方案；预算未定。", "evidence": "对方希望先试用",
                "tasks": [{"title": "发送方案", "due_date": "2026-10-08", "evidence": "约定2026年10月8日发送方案"}],
                "warnings": ["预算尚未确定"]}
    if kind in ("expense", "expense_b"):
        invoice = INVOICE_A if kind == "expense" else INVOICE_B
        return {"lines": [
            {"category": "TRAVEL", "amount": "260.00", "description": "高铁票", "invoice_no": invoice,
             "evidence": f"出差高铁票260元，发票号{invoice}"},
            {"category": "TRANSPORT", "amount": "48.00", "description": "出租车", "invoice_no": None,
             "evidence": "出租车48元，暂无发票"}], "warnings": ["出租车暂无发票"]}
    if kind == "leave":
        return {"leave_type_code": "annual", "start_date": "2026-10-12", "end_date": "2026-10-14",
                "reason": "家庭事务", "evidence": "我要申请年假，2026年10月12日至2026年10月14日", "warnings": []}
    return {"items": [{"sku": SKU, "quantity": 3, "evidence": f"{SKU} 共3件"},
                      {"sku": BAD_SKU, "quantity": 2, "evidence": f"{BAD_SKU} 共2件"}], "warnings": []}


def fake_model(*args, **kwargs):
    system_prompt, source = args[3], args[5]
    kind = next(k for k, s in SOURCES.items() if s == source)
    return json.dumps(proposal_for(kind, source), ensure_ascii=False), {"total_tokens": 321}


LOOP = asyncio.new_event_loop()


def session(fn):
    # 单个事件循环贯穿整个脚本：异步连接池里的连接绑定在创建它的循环上。
    async def go():
        async with AsyncSessionLocal() as db:
            return await fn(db)
    return LOOP.run_until_complete(go())


def enterprise_engine():
    user = os.getenv("ENTERPRISE_DB_USER") or os.getenv("DB_USER", "root")
    password = os.getenv("ENTERPRISE_DB_PASSWORD") or os.getenv("DB_PASSWORD", "")
    host = os.getenv("ENTERPRISE_DB_HOST", "127.0.0.1")
    port = os.getenv("ENTERPRISE_DB_PORT", "3306")
    name = os.getenv("ENTERPRISE_DB_NAME", "enterprise_business")
    from urllib.parse import quote_plus
    return create_engine(f"mysql+pymysql://{user}:{quote_plus(password)}@{host}:{port}/{name}?charset=utf8mb4")


class Checker:
    def __init__(self):
        self.passed = []

    def ok(self, condition, message):
        if not condition:
            raise AssertionError(message)
        self.passed.append(message)
        print(f"  PASS {message}")


def main():
    check = Checker()
    ent = enterprise_engine()
    db = SessionLocal()
    owner, stranger = rc.create_user("e2e-aw"), rc.create_user("e2e-st")
    org = _create_org(db, "e2e-aw-" + SUFFIX, owner["id"])
    teams = {code: _create_team(db, org, f"e2e-{code}", owner["id"]) for code in ("sales", "procurement", "hr")}
    for code, team in teams.items():
        db.execute(text("UPDATE teams SET department_code=:c WHERE id=:t"), {"c": code, "t": team})
    db.commit()
    for user in (owner, stranger):
        _add_org_member(db, org, user["id"], "member")
    for team in teams.values():
        _add_team_member(db, team, owner["id"], "member")
    with ent.begin() as conn:
        customer_id = conn.execute(text(
            "INSERT INTO customer (name, industry, owner_user_id, team_id, created_at) "
            "VALUES (:n, '软件服务', :o, :t, NOW())"), {"n": "E2E客户" + SUFFIX, "o": owner["id"], "t": teams["sales"]}
        ).lastrowid
        conn.execute(text(
            "INSERT INTO product (sku, name, unit, unit_price, on_hand_qty, safety_stock_qty) "
            "VALUES (:s, 'E2E 测试纸', '件', 10.00, 100, 5)"), {"s": SKU})
    work_ids = []

    def count(table, column):
        with ent.connect() as conn:
            return conn.execute(text(f"SELECT COUNT(*) FROM {table} WHERE {column}=:u"), {"u": owner["id"]}).scalar()

    def generate(kind, team_code, customer=None, source_key=None):
        req = GenerateRequest(request_key=uuid.uuid4(), team_id=teams[team_code], kind=kind, model_name=MODEL,
                              source_text=SOURCES[source_key or kind], customer_id=customer)
        work = session(lambda s: svc.generate(s, owner["id"], req))
        work_ids.append(work["id"])
        return work

    def apply(work, proposal):
        return session(lambda s: svc.apply_work(s, owner["id"], work["id"], proposal))

    try:
        with patch.object(svc, "async_chat_with_usage", AsyncMock(side_effect=fake_model)), \
             patch.object(svc, "async_get_api_config", AsyncMock(return_value={"api_key": "stub"})):
            print("\n[1] 四条工作流：整理 → 人工修改 → 保存真实业务草稿 → 重复保存不产生新草稿")
            cases = [("crm", "sales", customer_id, "follow_up", "author_user_id"),
                     ("expense", "hr", None, "expense_claim", "applicant_user_id"),
                     ("leave", "hr", None, "leave_request", "applicant_user_id")]
            for kind, team_code, customer, table, column in cases:
                before = count(table, column)
                work = generate(kind, team_code, customer)
                check.ok(work["status"] == "ready" and work["total_tokens"] == 321, f"{kind}: 整理结果已持久化为待核对")
                edited = json.loads(json.dumps(work["proposal"]))
                if kind == "crm":
                    edited["content"] = "核对后：客户希望先试用，10月8日前发送方案。"
                elif kind == "expense":
                    edited["lines"] = edited["lines"][:1]
                result = apply(work, edited)
                check.ok(result["status"] == "applied" and result["business_result"]["status"] == "DRAFT",
                         f"{kind}: Java 返回草稿 #{result['business_result'].get('id')}")
                check.ok(count(table, column) == before + 1, f"{kind}: 业务库恰好新增 1 条草稿")
                again = apply(work, edited)
                check.ok(again["business_result"]["id"] == result["business_result"]["id"]
                         and count(table, column) == before + 1, f"{kind}: 重复保存是无操作")
            with ent.connect() as conn:
                content = conn.execute(text("SELECT content FROM follow_up WHERE author_user_id=:u"),
                                       {"u": owner["id"]}).scalar()
            check.ok(content.startswith("核对后"), "crm: 写入业务库的是人工修改后的内容")

            print("\n[2] 采购：错误 SKU 被 Java 拒绝 → 退回可修改 → 改正后用同一幂等键保存")
            before = count("purchase_request", "requester_user_id")
            work = generate("procurement", "procurement")
            checks = {c["level"]: c["text"] for c in reversed(work["business_checks"])}
            check.ok(BAD_SKU in checks.get("blocker", ""), "procurement: 业务系统核对提前标出不存在的 SKU（会被拒绝）")
            check.ok(any(SKU in c["text"] and "库存 100" in c["text"] for c in work["business_checks"]),
                     "procurement: 核对带出真实库存与单价")
            rejected = apply(work, work["proposal"])
            check.ok(rejected["status"] == "ready" and BAD_SKU in (rejected["error_message"] or ""),
                     "procurement: 未知产品退回可修改，并带回业务原因")
            check.ok(count("purchase_request", "requester_user_id") == before, "procurement: 被拒时业务库无残留")
            fixed = {**work["proposal"], "items": work["proposal"]["items"][:1]}
            saved = apply(work, fixed)
            check.ok(saved["status"] == "applied" and saved["edited"], "procurement: 改正后保存成功")
            with ent.connect() as conn:
                total = conn.execute(text("SELECT total_amount FROM purchase_request WHERE requester_user_id=:u"),
                                     {"u": owner["id"]}).scalar()
            check.ok(float(total) == 30.0, "procurement: 金额由业务系统按产品目录计算（3 × 10.00）")

            print("\n[3] 响应丢失：Java 已写入但 Python 没收到结果 → 按原内容重试不产生重复草稿")
            before = count("expense_claim", "applicant_user_id")
            work = generate("expense", "sales", source_key="expense_b")
            real_call, calls = hub.call, []

            def lose_first_response(*args, **kwargs):
                calls.append(kwargs["idempotency_key"])
                result = real_call(*args, **kwargs)
                if len(calls) == 1:
                    raise TimeoutError("response lost")
                return result
            with patch.object(svc.hub, "call", side_effect=lose_first_response):
                first = apply(work, work["proposal"])
                check.ok(first["status"] == "retry", "expense: 结果未确认时进入待重试并锁定内容")
                second = apply(work, work["proposal"])
            check.ok(second["status"] == "applied" and calls[0] == calls[1], "expense: 重试沿用同一幂等键")
            check.ok(count("expense_claim", "applicant_user_id") == before + 1, "expense: 业务库只有 1 条草稿")

            print("\n[3b] 跨单重复发票：核对提前标出，保存时被 Java 拒绝并退回可修改")
            before = count("expense_claim", "applicant_user_id")
            dup = generate("expense", "hr")
            blockers = [c["text"] for c in dup["business_checks"] if c["level"] == "blocker"]
            check.ok(any(INVOICE_A in t and "已在报销单" in t for t in blockers), "expense: 核对标出发票已被另一张报销单使用")
            rejected = apply(dup, dup["proposal"])
            check.ok(rejected["status"] == "ready" and "已在报销单" in (rejected["error_message"] or ""),
                     "expense: Java 拒绝重复发票，成果退回可修改并带回原因")
            check.ok(count("expense_claim", "applicant_user_id") == before, "expense: 重复发票未产生新报销单")

            print("\n[4] 权限与数据边界")
            bad_customer = GenerateRequest(request_key=uuid.uuid4(), team_id=teams["sales"], kind="crm",
                                           model_name=MODEL, source_text=SOURCES["crm"], customer_id=999999999)
            try:
                session(lambda s: svc.generate(s, owner["id"], bad_customer))
                check.ok(False, "crm: 不存在的客户应被拒绝")
            except AppError:
                check.ok(True, "crm: 不属于本部门的客户在调用模型前被拒绝")
            try:
                session(lambda s: svc.get_work(s, stranger["id"], work_ids[0]))
                check.ok(False, "他人不应看到成果")
            except NotFound:
                check.ok(True, "非本人无法读取工作成果")
            try:
                session(lambda s: svc.history(s, stranger["id"], teams["sales"]))
                check.ok(False, "非部门成员不应看到部门成果")
            except PermissionDenied:
                check.ok(True, "非部门成员无法查看部门成果列表")
            pending = generate("leave", "hr")
            db.execute(text("UPDATE team_members SET status='disabled' WHERE team_id=:t AND user_id=:u"),
                       {"t": teams["hr"], "u": owner["id"]})
            db.commit()
            before = count("leave_request", "applicant_user_id")
            try:
                apply(pending, pending["proposal"])
                check.ok(False, "退出部门后不应能保存")
            except PermissionDenied:
                check.ok(count("leave_request", "applicant_user_id") == before, "退出部门后无法保存，且未调用业务系统")
            finally:
                db.execute(text("UPDATE team_members SET status='active' WHERE team_id=:t AND user_id=:u"),
                           {"t": teams["hr"], "u": owner["id"]})
                db.commit()

            print("\n[5] 持久化：新会话中历史成果与统计仍在")
            hist = session(lambda s: svc.history(s, owner["id"], teams["hr"]))
            check.ok(hist["stats"]["applied"] == 2 and hist["stats"]["edited"] >= 1 and len(hist["items"]) == 4,
                     f"hr 部门统计：{hist['stats']}")
        print(f"\nE2E PASS：{len(check.passed)} 项检查通过（模型为替身，Python/MySQL/Java 为真实服务）")
    finally:
        cleanup(db, ent, owner, org, teams, work_ids)


def cleanup(db, ent, owner, org, teams, work_ids):
    with ent.begin() as conn:
        uid = {"u": owner["id"]}
        conn.execute(text("DELETE FROM follow_up WHERE author_user_id=:u"), uid)
        conn.execute(text("DELETE FROM customer WHERE owner_user_id=:u"), uid)
        conn.execute(text("DELETE FROM leave_request WHERE applicant_user_id=:u"), uid)
        conn.execute(text("DELETE FROM purchase_request_line WHERE purchase_request_id IN "
                          "(SELECT id FROM purchase_request WHERE requester_user_id=:u)"), uid)
        conn.execute(text("DELETE FROM purchase_request WHERE requester_user_id=:u"), uid)
        conn.execute(text("DELETE FROM expense_line WHERE expense_claim_id IN "
                          "(SELECT id FROM expense_claim WHERE applicant_user_id=:u)"), uid)
        conn.execute(text("DELETE FROM expense_claim WHERE applicant_user_id=:u"), uid)
        conn.execute(text("DELETE FROM product WHERE sku=:s"), {"s": SKU})
        conn.execute(text("DELETE FROM audit_event WHERE user_id=:u"), uid)
        for work_id in work_ids:
            conn.execute(text("DELETE FROM idempotency_record WHERE idempotency_key=:k"), {"k": f"automation-{work_id}"})
    team_ids = list(teams.values())
    db.execute(text("DELETE FROM automation_work WHERE user_id=:u"), {"u": owner["id"]})
    db.execute(text("DELETE FROM team_members WHERE team_id IN :t").bindparams(
        bindparam("t", expanding=True)), {"t": team_ids})
    db.execute(text("DELETE FROM organization_members WHERE organization_id=:o"), {"o": org})
    db.execute(text("DELETE FROM teams WHERE id IN :t").bindparams(
        bindparam("t", expanding=True)), {"t": team_ids})
    db.execute(text("DELETE FROM organizations WHERE id=:o"), {"o": org})
    db.commit()
    db.close()
    rc.cleanup()
    LOOP.run_until_complete(async_engine.dispose())
    LOOP.close()


if __name__ == "__main__":
    main()
