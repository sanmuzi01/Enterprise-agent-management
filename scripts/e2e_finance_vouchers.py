"""财务自动记账的真实端到端验收：FastAPI（含权限层）+ MySQL + Java 业务服务全部是真的。

链路：员工提交报销 → 部门负责人批准 → Java 自动生成凭证草稿 → 财务人员核对（改科目、确认风险）→ 入账 → 月度汇总；
同时验证越权（其他部门、其他企业、被停用的成员、申请人自己确认）都被拦住。

前置：MySQL 已启动；Java 业务服务在 ENTERPRISE_HUB_BASE_URL（默认 127.0.0.1:8090）运行（jar 必须是最新构建）。
用法：.venv\\Scripts\\python.exe scripts\\e2e_finance_vouchers.py [--keep]
  --keep   验收通过后保留测试数据并打印登录账号，供浏览器里继续手工验证。账号改名为 kp_ 前缀，
           避免被测试套件清理 rt_ 测试用户时误删。
  --purge  清理 --keep 留下的全部数据（企业/部门/用户和 Java 侧的报销单、凭证、预算），然后退出。
默认结束时清理自建的企业/部门/用户和 Java 侧的报销单、凭证、预算。
"""
import os
import pathlib
import sys
import uuid
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests import _route_client as rc  # noqa: E402  先导入：设置非生产环境变量

from dotenv import load_dotenv  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

load_dotenv(ROOT / ".env")

from models.init_db import SessionLocal  # noqa: E402
from tests.test_enterprise_access import _add_org_member, _add_team_member, _create_org, _create_team  # noqa: E402

SUFFIX = uuid.uuid4().hex[:6].upper()
KEEP = "--keep" in sys.argv
BEIJING = timezone(timedelta(hours=8))


def enterprise_engine():
    from urllib.parse import quote_plus
    user = os.getenv("ENTERPRISE_DB_USER") or os.getenv("DB_USER", "root")
    password = os.getenv("ENTERPRISE_DB_PASSWORD") or os.getenv("DB_PASSWORD", "")
    host = os.getenv("ENTERPRISE_DB_HOST", "127.0.0.1")
    port = os.getenv("ENTERPRISE_DB_PORT", "3306")
    name = os.getenv("ENTERPRISE_DB_NAME", "enterprise_business")
    return create_engine(f"mysql+pymysql://{user}:{quote_plus(password)}@{host}:{port}/{name}?charset=utf8mb4")


class Checker:
    def __init__(self):
        self.passed = []

    def ok(self, condition, message):
        if not condition:
            raise AssertionError(message)
        self.passed.append(message)
        print(f"  PASS {message}")

    def status(self, response, expected, message):
        self.ok(response.status_code == expected, f"{message}（{response.status_code}）"
                if response.status_code == expected else f"{message}：期望 {expected}，实际 {response.status_code} {response.text[:200]}")


def purge_kept():
    ent, db = enterprise_engine(), SessionLocal()
    try:
        team_ids = [r[0] for r in db.execute(text(
            "SELECT t.id FROM teams t JOIN organizations o ON t.organization_id=o.id WHERE o.name LIKE 'e2e-vo%'")).all()]
        org_ids = [r[0] for r in db.execute(text("SELECT id FROM organizations WHERE name LIKE 'e2e-vo%'")).all()]
        user_ids = [r[0] for r in db.execute(text("SELECT id FROM `user` WHERE name LIKE 'kp_%'")).all()]
        with ent.begin() as conn:
            if team_ids:
                ids = ",".join(str(t) for t in team_ids)
                claims = f"SELECT id FROM expense_claim WHERE team_id IN ({ids})"
                conn.execute(text(f"DELETE FROM voucher_entry WHERE voucher_id IN (SELECT id FROM voucher WHERE team_id IN ({ids}))"))
                conn.execute(text(f"DELETE FROM voucher WHERE team_id IN ({ids})"))
                conn.execute(text(f"DELETE FROM expense_line WHERE expense_claim_id IN ({claims})"))
                conn.execute(text(f"DELETE FROM expense_claim WHERE team_id IN ({ids})"))
                conn.execute(text(f"DELETE FROM expense_budget WHERE team_id IN ({ids})"))
        if user_ids:
            rc._purge_users("id IN (" + ",".join(str(u) for u in user_ids) + ")")
        if team_ids:
            ids = ",".join(str(t) for t in team_ids)
            db.execute(text(f"DELETE FROM team_members WHERE team_id IN ({ids})"))
            db.execute(text(f"DELETE FROM teams WHERE id IN ({ids})"))
        for org in org_ids:
            db.execute(text("DELETE FROM organization_members WHERE organization_id=:o"), {"o": org})
            db.execute(text("DELETE FROM organizations WHERE id=:o"), {"o": org})
        db.commit()
        print(f"已清理：{len(org_ids)} 个企业、{len(team_ids)} 个部门、{len(user_ids)} 个用户")
    finally:
        db.close()


def main():
    check = Checker()
    ent = enterprise_engine()
    db = SessionLocal()
    client = rc.make_client()
    year = datetime.now(BEIJING).year

    owner = rc.create_user("e2e-vo")
    users = {name: rc.create_user(f"e2e-{name}") for name in
             ("employee", "head", "acc1", "acc2", "hr", "outsider")}
    org, other_org = _create_org(db, "e2e-vo-" + SUFFIX, owner["id"]), _create_org(db, "e2e-vo2-" + SUFFIX, owner["id"])
    teams = {code: _create_team(db, org, f"e2e-{code}-{SUFFIX}", owner["id"]) for code in ("sales", "finance", "hr")}
    other_finance = _create_team(db, other_org, f"e2e-ofin-{SUFFIX}", owner["id"])
    for code, team in {**teams, "finance2": other_finance}.items():
        db.execute(text("UPDATE teams SET department_code=:c WHERE id=:t"), {"c": code.rstrip("2"), "t": team})
    db.commit()
    for name in ("employee", "head", "acc1", "acc2", "hr"):
        _add_org_member(db, org, users[name]["id"], "member")
    _add_org_member(db, other_org, users["outsider"]["id"], "member")
    _add_team_member(db, teams["sales"], users["employee"]["id"], "member")
    _add_team_member(db, teams["sales"], users["head"]["id"], "admin")
    _add_team_member(db, teams["finance"], users["acc1"]["id"], "admin")
    _add_team_member(db, teams["finance"], users["acc2"]["id"], "member")
    _add_team_member(db, teams["finance"], users["employee"]["id"], "member")   # 员工兼任财务部门成员：用来验证制单复核分离
    _add_team_member(db, teams["hr"], users["hr"]["id"], "admin")
    _add_team_member(db, other_finance, users["outsider"]["id"], "member")
    with ent.begin() as conn:
        for team in (teams["sales"], teams["finance"]):
            conn.execute(text("INSERT INTO expense_budget (team_id, year, remaining_amount) VALUES (:t, :y, 100000)"),
                         {"t": team, "y": year})

    claim_ids = []

    def headers(name):
        return users[name]["headers"]

    def vouchers_url(path="", team="finance", **params):
        query = "&".join([f"team_id={teams[team]}"] + [f"{k}={v}" for k, v in params.items()])
        return f"/enterprise/finance/vouchers{path}?{query}"

    def file_claim(lines, team="sales", approve=True, applicant="employee"):
        resp = client.post("/enterprise/finance/mine", json={"team_id": teams[team], "lines": lines}, headers=headers(applicant))
        check.status(resp, 200, "员工创建报销草稿")
        claim_id = resp.json()["id"]
        claim_ids.append(claim_id)
        check.status(client.post(f"/enterprise/finance/{claim_id}/submit", headers=headers(applicant)), 200, "员工提交报销")
        if approve:
            head = "head" if team == "sales" else "acc1"
            resp = client.post(f"/enterprise/finance/{claim_id}/decide", headers=headers(head),
                               json={"team_id": teams[team], "action": "approve", "note": "同意"})
            check.status(resp, 200, "负责人批准报销")
        return claim_id

    try:
        print("== 批准即自动生成凭证草稿，销售部门走销售费用 ==")
        inv = [f"INV-{SUFFIX}-{n}" for n in range(1, 6)]
        claim = file_claim([
            {"category": "TRAVEL", "amount": 260, "description": "高铁票", "invoice_no": inv[0]},
            {"category": "MEAL", "amount": 188, "description": "客户宴请", "invoice_no": inv[1]},
            {"category": "OTHER", "amount": 48, "description": "出租车", "invoice_no": None},
        ])
        resp = client.get(vouchers_url(status="DRAFT"), headers=headers("acc1"))
        check.status(resp, 200, "财务人员能看到待核对凭证")
        row = next(v for v in resp.json() if v["expenseClaimId"] == claim)
        check.ok(row["status"] == "DRAFT" and row["applicantName"] == users["employee"]["name"], "凭证草稿带申请人姓名")
        voucher = client.get(vouchers_url(f"/{row['id']}"), headers=headers("acc1")).json()
        entries = voucher["entries"]
        check.ok(voucher["expenseClass"] == "SALES", "销售部门报销按销售费用入账")
        check.ok([e["subjectCode"] for e in entries] == ["6601.01", "6601.02", "6601.04", "2241.01"],
                 "科目建议：差旅→6601.01，客户宴请→6601.02，出租车→6601.04，贷方 2241.01")
        check.ok(sum(float(e["amount"]) for e in entries if e["direction"] == "D") ==
                 sum(float(e["amount"]) for e in entries if e["direction"] == "C") == 496.0, "借贷平衡，合计 496.00")
        check.ok(all(e["basis"] and e["confidence"] for e in entries), "每条分录都有科目依据和置信度")
        check.ok({r["code"] for r in voucher["risks"]} >= {"NO_INVOICE", "ENTERTAINMENT", "MEDIUM_CONFIDENCE"},
                 "风险项：无票据、业务招待费、推断科目")

        print("== 越权：非财务部门、其他企业、直接请求 ==")
        for name, team in (("employee", "sales"), ("head", "sales"), ("hr", "hr")):
            check.status(client.get(vouchers_url(team=team), headers=headers(name)), 403, f"{name} 用自己部门的 team_id 读凭证被拒绝")
        check.status(client.get(vouchers_url(), headers=headers("hr")), 403, "人事部门负责人不能以财务部门身份读凭证")
        check.status(client.post(vouchers_url(f"/{row['id']}/confirm").split("?")[0],
                                 json={"team_id": teams["sales"]}, headers=headers("head")), 403, "销售负责人不能确认凭证")
        check.status(client.get(vouchers_url(f"/{row['id']}"), headers=headers("outsider")), 403, "其他企业的财务看不到本企业凭证（不属于该部门）")
        other = client.get(f"/enterprise/finance/vouchers/{row['id']}?team_id={other_finance}", headers=headers("outsider"))
        check.status(other, 404, "其他企业财务用自己的部门读本企业凭证 id → 按不存在处理")

        print("== 财务核对：改科目 → 风险确认 → 入账 ==")
        out_of_range = client.post(vouchers_url(f"/{row['id']}/entries/{entries[2]['id']}/subject").split("?")[0],
                                   json={"team_id": teams["finance"], "subject_code": "6601.99", "reason": "x"}, headers=headers("acc1"))
        check.status(out_of_range, 422, "改科目必须写明原因（太短被拒绝）")
        changed = client.post(vouchers_url(f"/{row['id']}/entries/{entries[2]['id']}/subject").split("?")[0],
                              json={"team_id": teams["finance"], "subject_code": "6601.03", "reason": "实为办公用品快递费"},
                              headers=headers("acc1"))
        check.status(changed, 200, "财务把出租车一行改成办公费")
        check.ok(changed.json()["entries"][2]["confidence"] == "MANUAL", "改动后置信度为人工，依据留痕")
        confirm_url = vouchers_url(f"/{row['id']}/confirm").split("?")[0]
        refused = client.post(confirm_url, json={"team_id": teams["finance"]}, headers=headers("acc2"))
        check.ok(refused.status_code == 400 and "已核对风险项" in refused.text, "有需核对风险时不勾选确认不能入账")
        posted = client.post(confirm_url, json={"team_id": teams["finance"], "acknowledge_warnings": True, "note": "票据齐全"},
                             headers=headers("acc2"))
        check.status(posted, 200, "勾选已核对后入账成功")
        check.ok(posted.json()["status"] == "POSTED" and posted.json()["voucherNo"].startswith("记-"), "凭证已入账并分配凭证号")
        again = client.post(confirm_url, json={"team_id": teams["finance"], "acknowledge_warnings": True}, headers=headers("acc1"))
        check.status(again, 400, "已入账凭证不能再次确认")

        print("== 制单与复核分离 ==")
        own = file_claim([{"category": "TRAVEL", "amount": 100, "description": "出差", "invoice_no": inv[2]}],
                         team="finance", applicant="employee")
        own_voucher = next(v for v in client.get(vouchers_url(status="DRAFT"), headers=headers("acc1")).json()
                           if v["expenseClaimId"] == own)
        self_confirm = client.post(vouchers_url(f"/{own_voucher['id']}/confirm").split("?")[0],
                                   json={"team_id": teams["finance"]}, headers=headers("employee"))
        check.ok(self_confirm.status_code == 400 and "不能确认自己申请" in self_confirm.text, "申请人本人不能确认自己报销单的凭证")
        check.status(client.post(vouchers_url(f"/{own_voucher['id']}/confirm").split("?")[0],
                                 json={"team_id": teams["finance"]}, headers=headers("acc1")), 200, "其他财务人员可以确认")

        print("== 作废、补生成、停用成员 ==")
        third = file_claim([{"category": "OFFICE_SUPPLY", "amount": 30, "description": "文具", "invoice_no": inv[3]}])
        third_voucher = next(v for v in client.get(vouchers_url(status="DRAFT"), headers=headers("acc1")).json()
                             if v["expenseClaimId"] == third)
        void = client.post(vouchers_url(f"/{third_voucher['id']}/void").split("?")[0],
                           json={"team_id": teams["finance"], "reason": "票据不合规，退回申请人"}, headers=headers("acc1"))
        check.ok(void.status_code == 200 and void.json()["status"] == "VOID", "财务可以作废草稿并写明原因")
        # 模拟上线前已批准、没有凭证的报销单
        legacy = file_claim([{"category": "TRANSPORT", "amount": 20, "description": "地铁", "invoice_no": inv[4]}])
        with ent.begin() as conn:
            conn.execute(text("DELETE FROM voucher_entry WHERE voucher_id IN (SELECT id FROM voucher WHERE expense_claim_id=:c)"), {"c": legacy})
            conn.execute(text("DELETE FROM voucher WHERE expense_claim_id=:c"), {"c": legacy})
        unbooked = client.get(vouchers_url("/unbooked"), headers=headers("acc1")).json()
        check.ok([c["id"] for c in unbooked] == [legacy], "已批准但没有凭证的报销单出现在待生成列表")
        generated = client.post(vouchers_url(f"/from-claim/{legacy}").split("?")[0], json={"team_id": teams["finance"]},
                                headers=headers("acc1"))
        check.ok(generated.status_code == 200 and generated.json()["expenseClass"] == "SALES", "补生成凭证沿用报销单所在部门的类型")
        db.execute(text("UPDATE organization_members SET status='disabled' WHERE organization_id=:o AND user_id=:u"),
                   {"o": org, "u": users["acc1"]["id"]})
        db.commit()
        check.status(client.get(vouchers_url(), headers=headers("acc1")), 403, "企业成员被停用后立即失去凭证权限")

        print("== 月度汇总 ==")
        summary = client.get(vouchers_url("/summary"), headers=headers("acc2"))
        check.status(summary, 200, "月度汇总可读")
        data = summary.json()
        check.ok(data["byStatus"]["POSTED"]["count"] == 2 and data["balanced"] is True, "已入账 2 张且借贷平衡")
        check.ok(data["byStatus"]["VOID"]["count"] == 1 and data["unbookedClaims"] == 0, "作废 1 张，没有遗漏的报销单")
        check.ok("已入账凭证 2 张" in data["narrative"], "月度小结文字与数字一致")
        print(f"\n全部 {len(check.passed)} 项检查通过")
        if KEEP:
            for name, user in users.items():
                user["name"] = f"kp_{name}_{SUFFIX}"
                db.execute(text("UPDATE `user` SET name=:n WHERE id=:i"), {"n": user["name"], "i": user["id"]})
            db.commit()
            rc._created_user_ids.clear()   # 否则测试辅助模块退出时会把这些用户当作自己建的测试用户删掉
            print("\n--keep：保留数据，可在浏览器登录（密码 Passw0rd!Secure）：")
            for name, user in users.items():
                print(f"  {name}: {user['name']}")
            print(f"  销售部门 team_id={teams['sales']}，财务部门 team_id={teams['finance']}")
            db.execute(text("UPDATE organization_members SET status='active' WHERE organization_id=:o AND user_id=:u"),
                       {"o": org, "u": users["acc1"]["id"]})
            db.commit()
    finally:
        if not KEEP:
            with ent.begin() as conn:
                ids = ",".join(str(c) for c in claim_ids) or "0"
                conn.execute(text(f"DELETE FROM voucher_entry WHERE voucher_id IN (SELECT id FROM voucher WHERE expense_claim_id IN ({ids}))"))
                conn.execute(text(f"DELETE FROM voucher WHERE expense_claim_id IN ({ids})"))
                conn.execute(text(f"DELETE FROM expense_line WHERE expense_claim_id IN ({ids})"))
                conn.execute(text(f"DELETE FROM expense_claim WHERE id IN ({ids})"))
                for team in (teams["sales"], teams["finance"]):
                    conn.execute(text("DELETE FROM expense_budget WHERE team_id=:t"), {"t": team})
                conn.execute(text("DELETE FROM audit_event WHERE user_id IN (" + ",".join(
                    str(u["id"]) for u in users.values()) + ")"))
            rc.cleanup()
            all_teams = ",".join(str(t) for t in [*teams.values(), other_finance])
            db.execute(text(f"DELETE FROM team_members WHERE team_id IN ({all_teams})"))
            db.execute(text(f"DELETE FROM teams WHERE id IN ({all_teams})"))
            for o in (org, other_org):
                db.execute(text("DELETE FROM organization_members WHERE organization_id=:o"), {"o": o})
                db.execute(text("DELETE FROM organizations WHERE id=:o"), {"o": o})
            db.commit()
        db.close()


if __name__ == "__main__":
    if "--purge" in sys.argv:
        purge_kept()
    else:
        main()
