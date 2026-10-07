"""安全专项的真实端到端：FastAPI（权限层）+ MySQL + Java 业务服务全部是真的。

一、跨部门 / 跨企业 / 对象级越权矩阵：同一个对象（请假、报销、IT 工单、人事事项、责任计划与任务、AI 整理结果）
   由不同身份依次访问——同部门同事、别的部门成员、别的部门负责人、别的企业的成员与管理员、未登录——只有该对象的合法角色能成功，
   其余全部被拒绝；同时断言合法角色确实成功（防止“全部拒绝”这种假通过）。
二、重复与并发：同一个请求同时发 N 次（重复点击、网络重试、两个审批人同时点），业务结果必须只发生一次——
   请假审批只扣一次余额，报销只审批一次、只生成一张凭证，AI 整理结果只落一份草稿，责任接受 / 提交只记一次，
   带相同 Idempotency-Key 的重放不产生第二条记录。

前置：MySQL 已启动；Java 业务服务在 ENTERPRISE_HUB_BASE_URL（默认 127.0.0.1:8090）运行（jar 含 V9）。
用法：.venv\\Scripts\\python.exe scripts\\e2e_security.py
"""
import os
import pathlib
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("OFFLINE_DEMO_MODEL", "1")
os.environ.setdefault("CONSOLE_LOG_LEVEL", "CRITICAL")

from tests import _route_client as rc  # noqa: E402  先导入：设置非生产环境变量

from dotenv import load_dotenv  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

load_dotenv(ROOT / ".env")

from models.init_db import SessionLocal  # noqa: E402
from tests.test_enterprise_access import _add_org_member, _add_team_member, _create_org, _create_team  # noqa: E402

SUFFIX = uuid.uuid4().hex[:6].upper()
BEIJING = timezone(timedelta(hours=8))
TODAY = datetime.now(BEIJING).date()
MODEL = "demo-offline"
CONCURRENCY = 8


def enterprise_engine():
    from urllib.parse import quote_plus
    user = os.getenv("ENTERPRISE_DB_USER") or os.getenv("DB_USER", "root")
    password = os.getenv("ENTERPRISE_DB_PASSWORD") or os.getenv("DB_PASSWORD", "")
    host, port = os.getenv("ENTERPRISE_DB_HOST", "127.0.0.1"), os.getenv("ENTERPRISE_DB_PORT", "3306")
    name = os.getenv("ENTERPRISE_DB_NAME", "enterprise_business")
    return create_engine(f"mysql+pymysql://{user}:{quote_plus(password)}@{host}:{port}/{name}?charset=utf8mb4")


class Checker:
    def __init__(self):
        self.passed = []
        self.failures = []

    def ok(self, condition, message):
        if condition:
            self.passed.append(message)
            print(f"  PASS {message}")
        else:
            self.failures.append(message)
            print(f"  FAIL {message}")


def main() -> int:
    from service.llm.llm_config_service import save_config
    check = Checker()
    ent, db, client = enterprise_engine(), SessionLocal(), rc.make_client()
    names = ("emp", "peer", "head", "ops_emp", "ops_head", "itstaff", "hr1", "oadmin", "b_emp", "b_head", "b_oadmin", "boss")
    u = {n: rc.create_user("sec-" + n) for n in names}
    owner = rc.create_user("sec-own")
    org, org_b = _create_org(db, f"e2e-sec-{SUFFIX}", owner["id"]), _create_org(db, f"e2e-sec-b-{SUFFIX}", owner["id"])
    teams = {n: _create_team(db, org, f"e2e-sec-{n}-{SUFFIX}", owner["id"]) for n in ("sales", "ops", "it", "hr", "finance")}
    teams["b"] = _create_team(db, org_b, f"e2e-sec-bt-{SUFFIX}", owner["id"])
    for code, team in (("sales", "sales"), ("it", "it"), ("hr", "hr"), ("finance", "finance")):
        db.execute(text("UPDATE teams SET department_code=:c WHERE id=:t"), {"c": code, "t": teams[team]})
    db.commit()
    for n in ("emp", "peer", "head", "ops_emp", "ops_head", "itstaff", "hr1", "boss"):
        _add_org_member(db, org, u[n]["id"], "member")
    _add_org_member(db, org, u["oadmin"]["id"], "admin")
    for n in ("b_emp", "b_head"):
        _add_org_member(db, org_b, u[n]["id"], "member")
    _add_org_member(db, org_b, u["b_oadmin"]["id"], "admin")
    for n, team, role in (("emp", "sales", "member"), ("peer", "sales", "member"), ("head", "sales", "admin"), ("ops_emp", "ops", "member"), ("ops_head", "ops", "admin"),
                          ("itstaff", "it", "member"), ("hr1", "hr", "member"), ("boss", "finance", "admin"), ("b_emp", "b", "member"), ("b_head", "b", "admin")):
        _add_team_member(db, teams[team], u[n]["id"], role)
    _add_team_member(db, teams["sales"], u["oadmin"]["id"], "member")                  # 企业管理员同时是销售部成员（验收人）
    home = {"emp": "sales", "peer": "sales", "head": "sales", "ops_emp": "ops", "ops_head": "ops", "itstaff": "it", "hr1": "hr", "oadmin": "sales", "b_emp": "b",
            "b_head": "b", "b_oadmin": "b", "boss": "finance"}

    class U:
        def __init__(self, uid):
            self.id = uid
    for n in names:
        save_config(db, U(u[n]["id"]), MODEL, "offline-demo")

    def uid(n):
        return u[n]["id"]

    def h(n):
        return {} if n == "anon" else u[n]["headers"]

    def call(method, path, who, team=None, **body):
        """业务路由：team_id 默认是调用者自己所在的部门（工作台里的“当前部门”）。"""
        payload = {"team_id": teams[team] if team else teams[home.get(who, "sales")], **body} if method != "GET" else None
        if method == "GET":
            sep = "&" if "?" in path else "?"
            return client.get(f"{path}{sep}team_id={teams[team] if team else teams[home.get(who, 'sales')]}", headers=h(who))
        return client.post(path, json=payload, headers=h(who))

    def denied(response):
        return response.status_code in (400, 401, 403, 404, 409)

    def matrix(title, attackers, request):
        """每个无权身份只请求一次，全部必须被拒绝（400/401/403/404/409）。422 说明请求格式有问题、根本没走到权限判断，按漏网处理。"""
        results = {n: request(n) for n in attackers}
        bad = [(n, r.status_code, r.text[:70]) for n, r in results.items() if not denied(r)]
        check.ok(not bad, f"{title}：{len(attackers)} 种无权身份全部被拒绝" + (f"（漏网：{bad}）" if bad else ""))

    def positive(message, response, ok=(200,)):
        check.ok(response.status_code in ok, f"{message}（{response.status_code}" + ("" if response.status_code in ok else f" {response.text[:120]}") + "）")

    ids = ",".join(str(x["id"]) for x in u.values())
    try:
        with ent.begin() as conn:
            for who in ("emp", "peer"):
                conn.execute(text("INSERT INTO leave_balance (user_id, leave_type_id, year, remaining_days) SELECT :u, id, :y, 10 FROM leave_type WHERE code='annual'"),
                             {"u": uid(who), "y": TODAY.year})
            conn.execute(text("INSERT INTO expense_budget (team_id, year, remaining_amount) VALUES (:t, :y, 5000)"), {"t": teams["sales"], "y": TODAY.year})

        outsiders = ("peer", "ops_emp", "ops_head", "itstaff", "hr1", "b_emp", "b_head", "b_oadmin", "anon")

        # ============================ 一、越权矩阵 ============================
        print("== 一-1. 请假：提交 / 审批 ==")
        start = (TODAY + timedelta(days=30)).isoformat()
        draft = client.post("/enterprise/oa/leave/mine", headers=h("emp"), json={"team_id": teams["sales"], "leave_type_code": "annual", "start_date": start,
                                                                                    "end_date": start, "reason": "安全测试"})
        check.ok(draft.status_code == 200, "员工创建请假草稿")
        leave_id = draft.json()["id"]
        matrix("别人提交我的请假", outsiders + ("head", "oadmin"), lambda who: client.post(f"/enterprise/oa/leave/{leave_id}/submit", headers=h(who)))
        check.ok(client.post(f"/enterprise/oa/leave/{leave_id}/submit", headers=h("emp")).status_code == 200, "本人可以提交")
        matrix("无权审批", ("emp", "peer", "ops_emp", "ops_head", "itstaff", "hr1", "b_emp", "b_head", "b_oadmin", "anon"),
               lambda who: call("POST", f"/enterprise/oa/leave/{leave_id}/decide", who, action="approve", note="x"))
        # 用别的部门当前部门的 team_id 伪装（team_id 不是权限凭证）
        spoof = client.post(f"/enterprise/oa/leave/{leave_id}/decide", headers=h("ops_head"), json={"team_id": teams["sales"], "action": "approve", "note": "冒充"})
        check.ok(denied(spoof), "别的部门负责人即使把 team_id 填成销售部也不能审批（身份由服务端按成员关系判断）")
        spoof = client.post(f"/enterprise/oa/leave/{leave_id}/decide", headers=h("b_head"), json={"team_id": teams["sales"], "action": "approve", "note": "冒充"})
        check.ok(denied(spoof), "别的企业的负责人填成本企业部门编号同样被拒绝")
        positive("本部门负责人可以审批", call("POST", f"/enterprise/oa/leave/{leave_id}/decide", "head", action="approve", note="同意"))

        print("\n== 一-2. 报销：提交 / 审批 ==")
        claim = client.post("/enterprise/finance/mine", headers=h("emp"), json={"team_id": teams["sales"], "lines": [
            {"category": "TRAVEL", "amount": 300, "description": "出差", "invoice_no": f"INV-{SUFFIX}-1"}]})
        check.ok(claim.status_code == 200, "员工创建报销草稿")
        claim_id = claim.json()["id"]
        matrix("别人提交我的报销", outsiders + ("head",), lambda who: client.post(f"/enterprise/finance/{claim_id}/submit", headers=h(who)))
        positive("本人可以提交报销", client.post(f"/enterprise/finance/{claim_id}/submit", headers=h("emp")))
        matrix("无权审批报销", ("emp", "peer", "ops_emp", "ops_head", "itstaff", "hr1", "b_emp", "b_head", "b_oadmin", "anon"),
               lambda who: call("POST", f"/enterprise/finance/{claim_id}/decide", who, action="approve", note="x"))

        print("\n== 一-3. IT 工单 ==")
        ticket = client.post("/enterprise/it/tickets", headers=h("emp"), json={"team_id": teams["sales"], "category": "INCIDENT", "title": f"安全测试工单{SUFFIX}",
                                                                                 "description": "打印机坏了"})
        check.ok(ticket.status_code == 200, "员工提交工单")
        ticket_id = ticket.json()["id"]
        matrix("查看别人的工单", ("peer", "ops_emp", "b_emp", "b_head", "b_oadmin", "anon"), lambda who: call("GET", f"/enterprise/it/tickets/{ticket_id}", who))
        matrix("评论别人的工单", ("peer", "ops_emp", "b_emp", "b_head", "anon"),
               lambda who: call("POST", f"/enterprise/it/tickets/{ticket_id}/comments", who, body="入侵"))
        matrix("取消别人的工单", ("peer", "ops_emp", "b_emp", "b_head", "anon"), lambda who: call("POST", f"/enterprise/it/tickets/{ticket_id}/cancel", who))
        matrix("非 IT 人员使用 IT 工作台", ("emp", "peer", "head", "ops_emp", "hr1", "b_emp", "b_head", "anon"),
               lambda who: call("POST", f"/enterprise/it/desk/tickets/{ticket_id}/assign", who, assignee_user_id=uid("itstaff")))
        check.ok(call("GET", f"/enterprise/it/tickets/{ticket_id}", "emp").status_code == 200, "本人可以查看自己的工单")

        print("\n== 一-4. AI 整理结果（automation work） ==")
        work = client.post("/enterprise/automation", headers=h("emp"), json={
            "request_key": str(uuid.uuid4()), "team_id": teams["sales"], "kind": "leave", "model_name": MODEL,
            "source_text": f"我想请年假，{(TODAY + timedelta(days=40)).isoformat()} 到 {(TODAY + timedelta(days=41)).isoformat()}，家里有事。"})
        check.ok(work.status_code == 200, f"员工整理请假材料（{work.status_code}）")
        work_id = work.json()["id"]
        matrix("查看别人的整理结果", ("peer", "ops_emp", "itstaff", "b_emp", "b_head", "b_oadmin", "anon"), lambda who: client.get(f"/enterprise/automation/{work_id}", headers=h(who)))
        matrix("保存别人的整理结果", ("peer", "ops_emp", "itstaff", "b_emp", "b_head", "anon"),
               lambda who: client.post(f"/enterprise/automation/{work_id}/apply", headers=h(who), json={"proposal": work.json().get("proposal") or {}}))
        check.ok(client.get(f"/enterprise/automation/{work_id}", headers=h("emp")).status_code == 200, "本人可以查看自己的整理结果")

        print("\n== 一-5. 责任计划与任务 ==")
        plan = client.post("/enterprise/responsibility/plans", headers=h("head"), json={
            "team_id": teams["sales"], "title": f"安全测试计划{SUFFIX}", "source_type": "OTHER", "tasks": [{
                "title": "联调", "responsible_user_id": uid("emp"), "reviewer_user_id": uid("oadmin"), "due_date": (TODAY + timedelta(days=10)).isoformat(),
                "deliverable": "构建包", "acceptance_criteria": "回归通过", "priority": "NORMAL"}]})
        check.ok(plan.status_code == 200, f"负责人创建责任计划（{plan.status_code} {plan.text[:100]}）")
        plan_id = plan.json()["id"]
        matrix("查看别部门的责任计划", ("ops_emp", "ops_head", "itstaff", "hr1", "b_emp", "b_head", "b_oadmin", "anon"),
               lambda who: call("GET", f"/enterprise/responsibility/plans/{plan_id}", who))
        matrix("非负责人发布计划", ("emp", "peer", "ops_emp", "ops_head", "b_head", "b_oadmin", "anon"), lambda who: call("POST", f"/enterprise/responsibility/plans/{plan_id}/publish", who))
        published = call("POST", f"/enterprise/responsibility/plans/{plan_id}/publish", "head", note="发布")
        check.ok(published.status_code == 200, f"本部门负责人可以发布（{published.status_code} {published.text[:120]}）")
        task_id = client.get(f"/enterprise/responsibility/tasks?team_id={teams['sales']}&view=mine", headers=h("emp")).json()[0]["id"]
        matrix("别人接受我的责任", ("peer", "head", "ops_emp", "ops_head", "oadmin", "b_emp", "b_head", "anon"),
               lambda who: call("POST", f"/enterprise/responsibility/tasks/{task_id}/accept", who))
        matrix("别部门的人查看任务", ("ops_emp", "ops_head", "itstaff", "b_emp", "b_head", "b_oadmin", "anon"), lambda who: call("GET", f"/enterprise/responsibility/tasks/{task_id}", who))

        print("\n== 一-6. 通用：未登录与失效身份 ==")
        for path in ("/enterprise/workspace", f"/enterprise/home?team_id={teams['sales']}", f"/enterprise/attendance/me?team_id={teams['sales']}",
                     f"/enterprise/responsibility/tasks?team_id={teams['sales']}", "/admin/issues", "/enterprise/automation?team_id=1"):
            check.ok(client.get(path).status_code in (401, 403), f"未登录访问 {path.split('?')[0]} 被拒绝")
        check.ok(client.get("/enterprise/workspace", headers={"Authorization": "Bearer not.a.token"}).status_code == 401, "伪造的 token 被拒绝")
        check.ok(client.get("/admin/issues", headers=h("emp")).status_code in (401, 403), "普通员工访问管理员接口被拒绝")
        check.ok(client.get("/admin/issues", headers=h("oadmin")).status_code in (200, 401, 403), "企业管理员访问平台管理员接口按角色处理（不是 5xx）")

        # ============================ 二、重复与并发 ============================
        def burst(fn, n=CONCURRENCY):
            with ThreadPoolExecutor(max_workers=n) as pool:
                return list(pool.map(lambda _: fn(), range(n)))

        def successes(responses):
            return sum(1 for r in responses if r.status_code == 200)

        def why(responses):
            """失败时最常见的非 200 响应，方便判断是真问题还是脚本前提不对。"""
            bad = [f"{r.status_code} {r.text[:90]}" for r in responses if r.status_code != 200]
            return bad[0] if bad else "-"

        with ent.connect() as conn:
            balance_before = conn.execute(text("SELECT remaining_days FROM leave_balance WHERE user_id=:u AND year=:y"), {"u": uid("emp"), "y": TODAY.year}).scalar()

        print("\n== 二-1. 请假：重复提交 / 并发审批 ==")
        start2 = (TODAY + timedelta(days=50)).isoformat()
        leave2 = client.post("/enterprise/oa/leave/mine", headers=h("emp"), json={"team_id": teams["sales"], "leave_type_code": "annual", "start_date": start2,
                                                                                     "end_date": start2, "reason": "并发测试"}).json()["id"]
        submits = burst(lambda: client.post(f"/enterprise/oa/leave/{leave2}/submit", headers=h("emp")))
        check.ok(all(r.status_code < 500 for r in submits), "并发提交没有 5xx")
        decisions = burst(lambda: client.post(f"/enterprise/oa/leave/{leave2}/decide", headers=h("head"), json={"team_id": teams["sales"], "action": "approve", "note": "同意"}))
        check.ok(all(r.status_code < 500 for r in decisions), "并发审批没有 5xx")
        with ent.connect() as conn:
            balance_after = conn.execute(text("SELECT remaining_days FROM leave_balance WHERE user_id=:u AND year=:y"), {"u": uid("emp"), "y": TODAY.year}).scalar()
            statuses = conn.execute(text("SELECT status, COUNT(*) FROM leave_request WHERE applicant_user_id=:u GROUP BY status"), {"u": uid("emp")}).all()
            audit = dict(conn.execute(text("SELECT action, COUNT(*) FROM audit_event WHERE resource_type='leave_request' AND resource_id=:r GROUP BY action"), {"r": leave2}).all())
        check.ok(balance_before - balance_after == 1, f"这张 1 天的请假并发审批后只扣了 1 天（{balance_before} → {balance_after}）")
        check.ok(dict(statuses).get("APPROVED") == 2, f"数据库里恰好 2 张已批准（{dict(statuses)}）")
        check.ok(audit.get("oa.leave.submitted") == 1, f"并发提交只留下 1 条“提交”审计（{audit.get('oa.leave.submitted')} 条；成功响应 {successes(submits)} 次）")
        check.ok(audit.get("oa.leave.approved") == 1, f"并发审批只留下 1 条“批准”审计（{audit.get('oa.leave.approved')} 条；成功响应 {successes(decisions)} 次）")
        reject_after = client.post(f"/enterprise/oa/leave/{leave2}/decide", headers=h("head"), json={"team_id": teams["sales"], "action": "reject", "note": "反悔"})
        check.ok(denied(reject_after), "已批准的请假不能再被驳回")

        print("\n== 二-1b. 资源争用：同一个人的两张请假同时批准，不能把余额花两遍 ==")
        def draft_leave(who, day_offset, days):
            first = (TODAY + timedelta(days=day_offset)).isoformat()
            last = (TODAY + timedelta(days=day_offset + days - 1)).isoformat()
            made = client.post("/enterprise/oa/leave/mine", headers=h(who), json={"team_id": teams["sales"], "leave_type_code": "annual", "start_date": first, "end_date": last, "reason": "余额争用"})
            lid = made.json()["id"]
            client.post(f"/enterprise/oa/leave/{lid}/submit", headers=h(who))
            return lid
        l1, l2 = draft_leave("peer", 12, 6), draft_leave("peer", 30, 6)           # 余额 10 天，两张各 6 天：只能批准一张
        approvals = list(ThreadPoolExecutor(max_workers=2).map(
            lambda lid: client.post(f"/enterprise/oa/leave/{lid}/decide", headers=h("head"), json={"team_id": teams["sales"], "action": "approve", "note": "同意"}), (l1, l2)))
        with ent.connect() as conn:
            peer_balance = conn.execute(text("SELECT remaining_days FROM leave_balance WHERE user_id=:u AND year=:y"), {"u": uid("peer"), "y": TODAY.year}).scalar()
            approved_n = conn.execute(text("SELECT COUNT(*) FROM leave_request WHERE applicant_user_id=:u AND status='APPROVED'"), {"u": uid("peer")}).scalar()
        check.ok(approved_n == 1 and peer_balance == 4, f"余额 10 天、两张各 6 天的请假同时批准：只批准 {approved_n} 张，余额 {peer_balance}（不会变成负数或被覆盖）；{why(approvals)}")
        check.ok(all(r.status_code < 500 for r in approvals), "争用时没有 5xx（余额不足以 400 返回）")

        print("\n== 二-1c. 资源争用：同部门两张报销同时批准，不能把预算花两遍 ==")
        with ent.begin() as conn:
            conn.execute(text("UPDATE expense_budget SET remaining_amount=1000 WHERE team_id=:t AND year=:y"), {"t": teams["sales"], "y": TODAY.year})

        def draft_claim(who, amount, tag):
            made = client.post("/enterprise/finance/mine", headers=h(who), json={"team_id": teams["sales"], "lines": [
                {"category": "TRAVEL", "amount": amount, "description": "预算争用", "invoice_no": f"INV-{SUFFIX}-{tag}"}]})
            cid = made.json()["id"]
            client.post(f"/enterprise/finance/{cid}/submit", headers=h(who))
            return cid
        c1, c2 = draft_claim("emp", 800, "B1"), draft_claim("peer", 800, "B2")
        verdicts = list(ThreadPoolExecutor(max_workers=2).map(
            lambda cid: client.post(f"/enterprise/finance/{cid}/decide", headers=h("head"), json={"team_id": teams["sales"], "action": "approve", "note": "同意"}), (c1, c2)))
        with ent.connect() as conn:
            budget_left = conn.execute(text("SELECT remaining_amount FROM expense_budget WHERE team_id=:t AND year=:y"), {"t": teams["sales"], "y": TODAY.year}).scalar()
            approved_claims = conn.execute(text("SELECT COUNT(*) FROM expense_claim WHERE id IN (:a, :b) AND status='APPROVED'"), {"a": c1, "b": c2}).scalar()
        check.ok(approved_claims == 1 and float(budget_left) == 200.0, f"预算 1000、两张各 800 的报销同时批准：只批准 {approved_claims} 张，预算剩 {budget_left}（不会超支）")
        check.ok(all(r.status_code < 500 for r in verdicts), "预算争用时没有 5xx")
        with ent.begin() as conn:
            conn.execute(text("UPDATE expense_budget SET remaining_amount=5000 WHERE team_id=:t AND year=:y"), {"t": teams["sales"], "y": TODAY.year})

        print("\n== 二-2. 报销：并发审批、并发生成凭证 ==")
        decisions = burst(lambda: client.post(f"/enterprise/finance/{claim_id}/decide", headers=h("head"), json={"team_id": teams["sales"], "action": "approve", "note": "同意"}))
        check.ok(successes(decisions) == 1, f"同一张报销单同时审批 {CONCURRENCY} 次，只有 1 次成功（实际 {successes(decisions)}）")
        with ent.connect() as conn:
            vouchers_auto = conn.execute(text("SELECT COUNT(*) FROM voucher WHERE expense_claim_id=:c"), {"c": claim_id}).scalar()
        check.ok(vouchers_auto <= 1, f"审批后自动生成的凭证草稿不超过 1 张（{vouchers_auto}）")
        gens = burst(lambda: client.post(f"/enterprise/finance/vouchers/from-claim/{claim_id}", headers=h("boss"), json={"team_id": teams["finance"]}))
        with ent.connect() as conn:
            vouchers = conn.execute(text("SELECT COUNT(*) FROM voucher WHERE expense_claim_id=:c"), {"c": claim_id}).scalar()
        check.ok(vouchers == 1, f"同一张报销单并发 {CONCURRENCY} 次生成凭证，业务库里始终只有 1 张（{vouchers}；成功响应 {successes(gens)} 次）")
        no_server_error = all(r.status_code < 500 for r in gens)
        check.ok(no_server_error, "并发生成凭证没有任何 5xx（冲突要以 4xx 或幂等成功返回）")

        print("\n== 二-3. AI 整理结果：并发保存 ==")
        proposal = dict(work.json()["proposal"])
        proposal.update({"leave_type_code": "annual", "start_date": (TODAY + timedelta(days=45)).isoformat(), "end_date": (TODAY + timedelta(days=46)).isoformat()})
        saves = burst(lambda: client.post(f"/enterprise/automation/{work_id}/apply", headers=h("emp"), json={"proposal": proposal}))
        with ent.connect() as conn:
            drafts = conn.execute(text("SELECT COUNT(*) FROM leave_request WHERE applicant_user_id=:u AND start_date=:d"), {"u": uid("emp"), "d": (TODAY + timedelta(days=45)).isoformat()}).scalar()
        check.ok(drafts == 1, f"同一份整理结果并发保存 {CONCURRENCY} 次，只落 1 份请假草稿（{drafts}；成功响应 {successes(saves)} 次；{why(saves)}）")
        check.ok(all(r.status_code < 500 for r in saves), "并发保存没有任何 5xx")

        print("\n== 二-4. 责任：并发接受 / 并发提交 ==")
        accepts = burst(lambda: call("POST", f"/enterprise/responsibility/tasks/{task_id}/accept", "emp"))
        check.ok(successes(accepts) == 1, f"同一项责任并发接受 {CONCURRENCY} 次，只有 1 次成功（实际 {successes(accepts)}）")
        submits = burst(lambda: call("POST", f"/enterprise/responsibility/tasks/{task_id}/submit", "emp", summary="已完成", link="https://example.com/build"))
        check.ok(successes(submits) == 1, f"同一项责任并发提交成果 {CONCURRENCY} 次，只有 1 次成功（实际 {successes(submits)}）")
        with ent.connect() as conn:
            events = dict(conn.execute(text("SELECT event_type, COUNT(*) FROM responsibility_event WHERE task_id=:t GROUP BY event_type"), {"t": task_id}).all())
        check.ok(all(n == 1 for k, n in events.items() if k in ("ACCEPTED", "SUBMITTED")), f"履责记录里接受与提交各只有 1 条（{events}）")
        verify = burst(lambda: call("POST", f"/enterprise/responsibility/tasks/{task_id}/verify", "oadmin", note="通过"))
        check.ok(successes(verify) == 1, f"同一项成果并发验收 {CONCURRENCY} 次，只有 1 次成功（实际 {successes(verify)}；{why(verify)}）")

        print("\n== 二-5. 业务系统层：相同 Idempotency-Key 的重放 ==")
        from service import enterprise_hub_client as hub
        key = str(uuid.uuid4())
        body = {"lines": [{"category": "MEAL", "amount": 55, "description": "重放测试", "invoiceNo": f"INV-{SUFFIX}-RP"}]}
        def replay():
            try:
                return hub.call("POST", "/finance/expenses", uid("emp"), teams["sales"], ["finance.write"], "security_replay", json_body=body, idempotency_key=key)
            except Exception as exc:  # noqa: BLE001 —— 处理中的重复请求被拒绝（409）也是合法的
                return {"error": str(exc)[:80]}
        results = [replay() for _ in range(4)] + list(ThreadPoolExecutor(max_workers=2).map(lambda _: replay(), range(2)))
        ids_seen = {r["id"] for r in results if "id" in r}
        with ent.connect() as conn:
            created = conn.execute(text("SELECT COUNT(*) FROM expense_line WHERE invoice_no=:i"), {"i": f"INV-{SUFFIX}-RP"}).scalar()
        check.ok(len(ids_seen) == 1 and created == 1, f"相同 Idempotency-Key 重放 6 次（4 次顺序 + 2 次并发），业务系统只创建 1 张报销单（返回编号 {ids_seen}，库里 {created} 条明细）")
        other_body = {"lines": [{"category": "MEAL", "amount": 56, "description": "新请求", "invoiceNo": f"INV-{SUFFIX}-RP2"}]}
        other = hub.call("POST", "/finance/expenses", uid("emp"), teams["sales"], ["finance.write"], "security_replay", json_body=other_body, idempotency_key=str(uuid.uuid4()))
        check.ok(other["id"] not in ids_seen, "换一个 Idempotency-Key 才是新的请求")

        print(f"\n通过 {len(check.passed)} 项，失败 {len(check.failures)} 项")
        for failure in check.failures:
            print("  失败：", failure)
        return 1 if check.failures else 0
    finally:
        tids = ",".join(str(t) for t in teams.values())
        with ent.begin() as conn:
            for stmt in (
                    f"DELETE FROM voucher_entry WHERE voucher_id IN (SELECT id FROM voucher WHERE team_id IN ({tids}))", f"DELETE FROM voucher WHERE team_id IN ({tids})",
                    f"DELETE FROM expense_line WHERE expense_claim_id IN (SELECT id FROM expense_claim WHERE team_id IN ({tids}))", f"DELETE FROM expense_claim WHERE team_id IN ({tids})",
                    f"DELETE FROM it_ticket_comment WHERE ticket_id IN (SELECT id FROM it_ticket WHERE team_id IN ({tids}))", f"DELETE FROM it_ticket WHERE team_id IN ({tids})",
                    f"DELETE FROM responsibility_event WHERE plan_id IN (SELECT id FROM responsibility_plan WHERE team_id IN ({tids}))",
                    f"DELETE FROM responsibility_deliverable WHERE task_id IN (SELECT id FROM responsibility_task WHERE team_id IN ({tids}))",
                    f"DELETE FROM responsibility_dependency WHERE task_id IN (SELECT id FROM responsibility_task WHERE team_id IN ({tids}))",
                    f"DELETE FROM responsibility_collaborator WHERE task_id IN (SELECT id FROM responsibility_task WHERE team_id IN ({tids}))",
                    f"DELETE FROM responsibility_task WHERE team_id IN ({tids})", f"DELETE FROM responsibility_plan WHERE team_id IN ({tids})",
                    f"DELETE FROM leave_request WHERE applicant_user_id IN ({ids})", f"DELETE FROM leave_balance WHERE user_id IN ({ids})",
                    f"DELETE FROM audit_event WHERE user_id IN ({ids})"):
                try:
                    conn.execute(text(stmt))
                except Exception as exc:  # noqa: BLE001 —— 某张表的列名与假设不同时给出提示，不让清理中断
                    print("  清理提示：", stmt[:60], type(exc).__name__)
        db.commit()
        db.execute(text(f"DELETE FROM automation_work WHERE team_id IN ({','.join(str(t) for t in teams.values())})"))
        db.execute(text(f"DELETE FROM work_item WHERE user_id IN ({ids})"))
        db.execute(text(f"DELETE FROM notification WHERE user_id IN ({ids})"))
        db.commit()
        rc.cleanup()
        db.execute(text(f"DELETE FROM team_members WHERE team_id IN ({tids})"))
        db.execute(text(f"DELETE FROM teams WHERE id IN ({tids})"))
        for o in (org, org_b):
            db.execute(text("DELETE FROM organization_members WHERE organization_id=:o"), {"o": o})
            db.execute(text("DELETE FROM organizations WHERE id=:o"), {"o": o})
        db.commit()
        db.close()


if __name__ == "__main__":
    sys.exit(main())
