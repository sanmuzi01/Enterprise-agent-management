"""人事入转调离的真实端到端验收：FastAPI（含权限层）+ MySQL + Java 业务服务全部是真的。

链路：HR 预检 → 发起 → 员工所在部门负责人批准 → 人事/IT/财务/负责人/员工各自办理清单 → HR 办结 → 企业管理员落实系统变更；
规则检查直接读真实业务数据（IT 设备、报销、请假），离职时设备没收回、报销没结清不能办结；
同时验证越权（非 HR 发起、员工批自己、他人代办任务、非管理员落实变更、其他企业）都被拦住。

前置：MySQL 已启动；Java 业务服务在 ENTERPRISE_HUB_BASE_URL（默认 127.0.0.1:8090）运行（jar 必须是最新构建）。
用法：.venv\\Scripts\\python.exe scripts\\e2e_hr_cases.py [--keep | --purge]
  --keep   验收通过后保留数据并打印登录账号（kp_hr_ 前缀），供浏览器里继续验证；--purge 清理保留的数据。
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
TODAY = datetime.now(BEIJING).date()


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

    def ok(self, condition, message):
        if not condition:
            raise AssertionError(message)
        self.passed.append(message)
        print(f"  PASS {message}")

    def status(self, response, expected, message):
        self.ok(response.status_code == expected, f"{message}（{response.status_code}）" if response.status_code == expected
                else f"{message}：期望 {expected}，实际 {response.status_code} {response.text[:300]}")


def clean_business_data(ent, team_ids, user_ids):
    ids = ",".join(str(t) for t in team_ids) or "0"
    users = ",".join(str(u) for u in user_ids) or "0"
    with ent.begin() as conn:
        conn.execute(text(f"DELETE FROM hr_case_task WHERE case_id IN (SELECT id FROM hr_case WHERE team_id IN ({ids}))"))
        conn.execute(text(f"DELETE FROM hr_case WHERE team_id IN ({ids})"))
        conn.execute(text(f"DELETE FROM it_device_event WHERE device_id IN (SELECT id FROM it_device WHERE managing_team_id IN ({ids}))"))
        conn.execute(text(f"DELETE FROM it_device WHERE managing_team_id IN ({ids})"))
        conn.execute(text(f"DELETE FROM voucher_entry WHERE voucher_id IN (SELECT id FROM voucher WHERE team_id IN ({ids}))"))
        conn.execute(text(f"DELETE FROM voucher WHERE team_id IN ({ids})"))
        conn.execute(text(f"DELETE FROM expense_line WHERE expense_claim_id IN (SELECT id FROM expense_claim WHERE team_id IN ({ids}))"))
        conn.execute(text(f"DELETE FROM expense_claim WHERE team_id IN ({ids})"))
        conn.execute(text(f"DELETE FROM expense_budget WHERE team_id IN ({ids})"))
        conn.execute(text(f"DELETE FROM audit_event WHERE user_id IN ({users})"))


def purge_kept():
    ent, db = enterprise_engine(), SessionLocal()
    try:
        orgs = [r[0] for r in db.execute(text("SELECT id FROM organizations WHERE name LIKE 'e2e-hr%'")).all()]
        teams = [r[0] for r in db.execute(text("SELECT t.id FROM teams t JOIN organizations o ON o.id=t.organization_id WHERE o.name LIKE 'e2e-hr%'")).all()]
        users = [r[0] for r in db.execute(text("SELECT id FROM `user` WHERE name LIKE 'kp_hr_%'")).all()]
        clean_business_data(ent, teams, users)
        if users:
            rc._purge_users("id IN (" + ",".join(str(u) for u in users) + ")")
        if teams:
            ids = ",".join(str(t) for t in teams)
            db.execute(text(f"DELETE FROM team_members WHERE team_id IN ({ids})"))
            db.execute(text(f"DELETE FROM teams WHERE id IN ({ids})"))
        for o in orgs:
            db.execute(text("DELETE FROM organization_members WHERE organization_id=:o"), {"o": o})
            db.execute(text("DELETE FROM organizations WHERE id=:o"), {"o": o})
        db.commit()
        print(f"已清理：{len(orgs)} 个企业、{len(teams)} 个部门、{len(users)} 个用户")
    finally:
        db.close()


def main():
    check = Checker()
    ent, db, client = enterprise_engine(), SessionLocal(), rc.make_client()
    owner = rc.create_user("e2e-hr-own")
    names = ("hr1", "hr2", "head", "emp", "newbie", "it1", "fin1", "admin", "out")
    u = {n: rc.create_user(f"e2e-hr-{n}") for n in names}
    org, other_org = _create_org(db, f"e2e-hr-{SUFFIX}", owner["id"]), _create_org(db, f"e2e-hr2-{SUFFIX}", owner["id"])
    t = {code: _create_team(db, org, f"e2e-hr-{code}-{SUFFIX}", owner["id"]) for code in ("hr", "sales", "it", "finance")}
    other_hr = _create_team(db, other_org, f"e2e-hr-ohr-{SUFFIX}", owner["id"])
    for code, team in {**t, "hr2": other_hr}.items():
        db.execute(text("UPDATE teams SET department_code=:c WHERE id=:t"), {"c": code.rstrip("2"), "t": team})
    db.commit()
    for n in ("hr1", "hr2", "head", "emp", "newbie", "it1", "fin1"):
        _add_org_member(db, org, u[n]["id"], "member")
    _add_org_member(db, org, u["admin"]["id"], "admin")
    _add_org_member(db, other_org, u["out"]["id"], "member")
    for n, team, role in (("hr1", "hr", "member"), ("hr2", "hr", "member"), ("head", "sales", "admin"), ("emp", "sales", "member"),
                          ("newbie", "sales", "member"), ("it1", "it", "member"), ("fin1", "finance", "member"), ("admin", "sales", "member")):
        _add_team_member(db, t[team], u[n]["id"], role)
    _add_team_member(db, other_hr, u["out"]["id"], "member")
    with ent.begin() as conn:
        conn.execute(text("INSERT INTO expense_budget (team_id, year, remaining_amount) VALUES (:t, :y, 100000)"), {"t": t["sales"], "y": TODAY.year})

    def h(n):
        return u[n]["headers"]

    def get(path, who, team, **params):
        q = "&".join([f"team_id={t[team]}"] + [f"{k}={v}" for k, v in params.items()])
        return client.get(f"/enterprise/hr{path}?{q}", headers=h(who))

    def post(path, who, team, **body):
        return client.post(f"/enterprise/hr{path}", json={"team_id": t[team], **body}, headers=h(who))

    def case_body(case_type, emp, **over):
        return {"case_type": case_type, "employee_user_id": u[emp]["id"], "employee_team_id": t["sales"],
                "effective_date": (TODAY + timedelta(days=7)).isoformat(), "position": "销售专员", **over}

    who_for = {"HR": ("hr2", "hr"), "IT": ("it1", "it"), "FINANCE": ("fin1", "finance"), "MANAGER": ("head", "sales")}

    def finish_all(case, employee):
        for task in case["tasks"]:
            who, team = who_for.get(task["owner"], (employee, "sales"))
            r = post(f"/cases/{case['id']}/tasks/{task['id']}/done", who, team, note="已办")
            check.ok(r.status_code == 200, f"{task['ownerLabel']}办完「{task['title']}」" if r.status_code == 200 else r.text[:200])

    team_ids = [*t.values(), other_hr]
    try:
        print("== 入职：发起 → 批准 → 各方办理 → 办结 ==")
        me = get("/me", "hr1", "hr").json()
        check.ok(me["is_hr"] and me["roles"] == ["HR"], "人事成员的身份是 HR")
        check.status(post("/cases", "head", "sales", **case_body("ONBOARDING", "newbie")), 403, "非人事成员不能发起")
        pre = post("/cases/precheck", "hr1", "hr", **case_body("ONBOARDING", "newbie")).json()
        check.ok(pre["canSubmit"] and any(c["code"] == "NO_LEAVE_BALANCE" for c in pre["checks"]), "预检读出真实数据：新员工还没有假期余额")
        created = post("/cases", "hr1", "hr", **case_body("ONBOARDING", "newbie"))
        check.status(created, 200, "人事发起入职事项")
        case = created.json()
        check.ok(case["status"] == "PENDING_APPROVAL" and case["employeeName"] == u["newbie"]["name"], "事项待部门负责人批准")
        check.status(post(f"/cases/{case['id']}/approve", "emp", "sales"), 404, "普通员工不能批准（按不存在处理）")
        check.status(post(f"/cases/{case['id']}/approve", "newbie", "sales"), 404, "员工本人不能批准自己的事项")
        approval = get("/cases", "head", "sales", view="approval").json()
        check.ok([c["id"] for c in approval] == [case["id"]], "负责人在“待我批准”里看到它")
        approved = post(f"/cases/{case['id']}/approve", "head", "sales", note="同意")
        check.ok(approved.status_code == 200 and len(approved.json()["tasks"]) == 7, "负责人批准后生成 7 项办理清单")
        it_tasks = get("/cases/my-tasks", "it1", "it").json()
        check.ok(len(it_tasks) == 2 and all(x["owner"] == "IT" for x in it_tasks), "IT 在“我的办理任务”里只看到 2 项 IT 任务")
        fin_task = next(x for x in approved.json()["tasks"] if x["owner"] == "FINANCE")
        check.status(post(f"/cases/{case['id']}/tasks/{fin_task['id']}/done", "it1", "it"), 403, "IT 不能替财务办任务")
        early = post(f"/cases/{case['id']}/complete", "hr1", "hr")
        check.ok(early.status_code == 400 and "必办任务没有完成" in early.text, "必办任务没办完不能办结")
        finish_all(approved.json(), "newbie")
        done = post(f"/cases/{case['id']}/complete", "hr1", "hr")
        check.ok(done.status_code == 200 and done.json()["status"] == "COMPLETED" and not done.json()["effectPending"], "入职办结，无需系统变更")

        print("== 离职：规则检查读真实设备与报销，未结清不能办结 ==")
        device = client.post("/enterprise/it/desk/devices", headers=h("it1"), json={
            "team_id": t["it"], "asset_no": f"HR-{SUFFIX}", "device_type": "LAPTOP", "model": "ThinkPad"}).json()
        client.post(f"/enterprise/it/desk/devices/{device['id']}/assign", headers=h("it1"), json={"team_id": t["it"], "user_id": u["emp"]["id"]})
        claim = client.post("/enterprise/finance/mine", headers=h("emp"), json={"team_id": t["sales"], "lines": [
            {"category": "TRAVEL", "amount": 300, "description": "出差", "invoice_no": f"INV-{SUFFIX}"}]}).json()
        client.post(f"/enterprise/finance/{claim['id']}/submit", headers=h("emp"))
        pre = post("/cases/precheck", "hr1", "hr", **case_body("OFFBOARDING", "emp")).json()
        codes = {c["code"]: c for c in pre["checks"]}
        check.ok("DEVICES_ASSIGNED" in codes and f"HR-{SUFFIX}" in codes["DEVICES_ASSIGNED"]["message"], "预检发现名下设备（来自 IT 台账）")
        check.ok("PENDING_CLAIMS" in codes, "预检发现未结的报销单（来自财务）")
        off = post("/cases", "hr1", "hr", **case_body("OFFBOARDING", "emp")).json()
        dup = post("/cases", "hr2", "hr", **case_body("TRANSFER", "emp", target_team_id=t["it"]))
        check.ok(dup.status_code == 400 and "正在办理离职" in dup.text, "离职办理中不能再发起调岗")
        off = post(f"/cases/{off['id']}/approve", "head", "sales").json()
        finish_all(off, "emp")
        blocked = post(f"/cases/{off['id']}/complete", "hr1", "hr")
        check.ok(blocked.status_code == 400 and f"HR-{SUFFIX}" in blocked.text and "报销" in blocked.text, "设备没收回、报销没结清，不能办结")
        client.post(f"/enterprise/it/desk/devices/{device['id']}/return", headers=h("it1"), json={"team_id": t["it"], "note": "离职收回"})
        client.post(f"/enterprise/finance/{claim['id']}/decide", headers=h("head"), json={"team_id": t["sales"], "action": "approve"})
        rechecked = post(f"/cases/{off['id']}/recheck", "hr1", "hr").json()
        check.ok(not any(c["level"] == "BLOCK" for c in rechecked["checks"]), "收回设备、审批报销后重新检查无阻断")
        done = post(f"/cases/{off['id']}/complete", "hr1", "hr")
        check.ok(done.status_code == 200 and done.json()["effectPending"], "离职办结，提示“停用账号”待落实")
        check.status(post(f"/cases/{off['id']}/apply-effect", "hr1", "hr"), 403, "人事不能直接停用账号，需要企业管理员")
        applied = post(f"/cases/{off['id']}/apply-effect", "admin", "sales")
        check.ok(applied.status_code == 200 and not applied.json()["effectPending"],
                 "企业管理员落实：停用员工账号" if applied.status_code == 200 else f"落实失败 {applied.status_code} {applied.text[:300]}")
        check.status(client.get(f"/enterprise/it/tickets/mine", headers=h("emp")), 200, "（个人数据接口不依赖部门）")
        check.status(get("/cases", "emp", "sales", view="mine"), 403, "离职员工立即失去部门工作台权限")

        print("== 调岗：落实后部门归属改变 ==")
        tr = post("/cases", "hr1", "hr", **case_body("TRANSFER", "newbie", target_team_id=t["it"])).json()
        tr = post(f"/cases/{tr['id']}/approve", "head", "sales").json()
        finish_all(tr, "newbie")
        post(f"/cases/{tr['id']}/complete", "hr1", "hr")
        applied = post(f"/cases/{tr['id']}/apply-effect", "admin", "sales")
        check.status(applied, 200, "企业管理员落实调岗")
        teams_now = [r[0] for r in db.execute(text("SELECT team_id FROM team_members WHERE user_id=:u AND status='active'"), {"u": u["newbie"]["id"]}).all()]
        db.commit()
        check.ok(teams_now == [t["it"]], "员工已从销售部移到 IT 部")
        check.status(client.get(f"/enterprise/it/desk/tickets?team_id={t['it']}", headers=h("newbie")), 200, "调岗后获得 IT 台权限")
        check.status(client.get(f"/enterprise/crm/customers?team_id={t['sales']}", headers=h("newbie")), 403, "调岗后失去原部门权限")

        print("== 越权与汇总 ==")
        check.status(get("/cases", "out", "hr"), 403, "其他企业的人事不能用本企业部门")
        other = client.get(f"/enterprise/hr/cases/{case['id']}?team_id={other_hr}", headers=h("out"))
        check.status(other, 404, "其他企业的人事读本企业事项 id → 按不存在处理")
        summary = get("/cases/summary", "hr1", "hr").json()
        check.ok(summary["byType"]["ONBOARDING"]["COMPLETED"] == 1 and summary["byType"]["OFFBOARDING"]["COMPLETED"] == 1, "汇总统计正确")
        check.ok("已办结 3 件" in summary["narrative"], "人事小结文字与数字一致")
        print(f"\n全部 {len(check.passed)} 项检查通过")

        if KEEP:
            db.execute(text("UPDATE organization_members SET status='active' WHERE organization_id=:o"), {"o": org})
            for n, user in u.items():
                user["name"] = f"kp_hr_{n}_{SUFFIX[:3]}"
                db.execute(text("UPDATE `user` SET name=:n WHERE id=:i"), {"n": user["name"], "i": user["id"]})
            db.commit()
            rc._created_user_ids.clear()
            print("\n--keep：保留数据，可在浏览器登录（密码 Passw0rd!Secure）：")
            for n, user in u.items():
                print(f"  {n}: {user['name']}")
    finally:
        if not KEEP:
            clean_business_data(ent, team_ids, [x["id"] for x in u.values()])
            rc.cleanup()
            ids = ",".join(str(x) for x in team_ids)
            db.execute(text(f"DELETE FROM team_members WHERE team_id IN ({ids})"))
            db.execute(text(f"DELETE FROM teams WHERE id IN ({ids})"))
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
