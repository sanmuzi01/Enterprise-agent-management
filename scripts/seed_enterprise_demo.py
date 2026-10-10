"""演示企业数据：一家虚构的“星河科技（演示）”——5 个部门、11 个固定账号、已发布的部门助手与中央助手、
业务系统里的预算/产品/客户/设备/假期余额，以及一批“正在发生”的真实业务单据（经真实接口产生，不是直接写表）：
待审批请假、已批准报销与待核对凭证、IT 工单（待接单/已解决/待审批）、进行中的入职办理。

用法（项目根目录；需要 MySQL；第 3 步需要 Java 业务服务在运行，没有就跳过并提示）：
    .venv\\Scripts\\python.exe scripts\\seed_enterprise_demo.py            # 已存在就不重复建
    .venv\\Scripts\\python.exe scripts\\seed_enterprise_demo.py --reset    # 先清掉旧演示数据再建
    .venv\\Scripts\\python.exe scripts\\seed_enterprise_demo.py --purge    # 只清理
设置 OFFLINE_DEMO_MODEL=1 时，所有演示账号的模型都用离线演示模型（不联网、无需 Key）。
绝不在生产环境运行。固定密码只用于演示，见 docs/demo-script.md。
"""
import asyncio
import os
import pathlib
import sys
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

from service.config_validation import is_production  # noqa: E402

if is_production():
    print("拒绝执行：APP_ENV=production，演示脚本会建固定密码的账号，不能对生产库跑。")
    sys.exit(1)

os.environ.setdefault("OFFLINE_DEMO_MODEL", "1")

from tests import _route_client as rc  # noqa: E402  先导入：设置非生产环境变量

from dotenv import load_dotenv  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

load_dotenv(ROOT / ".env")

from models.async_db import AsyncSessionLocal  # noqa: E402
from models.init_db import SessionLocal, bootstrap_database  # noqa: E402

PASSWORD = "Demo@12345"
ORG_NAME = "星河科技（演示）"
MODEL = "demo-offline"
TODAY = datetime.now(timezone(timedelta(hours=8))).date()

# 账号名 → (显示用途, 所在部门, 部门角色, 企业角色)
ACCOUNTS = {
    "demo_owner": ("企业所有者（管理员）", "hr", "member", "owner"),
    "demo_hr": ("人事专员", "hr", "member", "member"),
    "demo_hr2": ("人事专员（复核）", "hr", "member", "member"),
    "demo_fin": ("财务专员", "finance", "member", "member"),
    "demo_fin2": ("财务负责人", "finance", "admin", "member"),
    "demo_it": ("IT 工程师", "it", "member", "member"),
    "demo_it2": ("IT 工程师（二线）", "it", "member", "member"),
    "demo_head": ("销售部负责人", "sales", "admin", "member"),
    "demo_emp": ("销售员工", "sales", "member", "member"),
    "demo_newbie": ("销售新人（待入职办理）", "sales", "member", "member"),
    "demo_buyer": ("采购专员", "procurement", "member", "member"),
}
TEAMS = {"hr": "人事部", "finance": "财务部", "it": "IT 部", "sales": "销售部", "procurement": "采购部"}


def enterprise_engine():
    from urllib.parse import quote_plus
    user = os.getenv("ENTERPRISE_DB_USER") or os.getenv("DB_USER", "root")
    password = os.getenv("ENTERPRISE_DB_PASSWORD") or os.getenv("DB_PASSWORD", "")
    host, port = os.getenv("ENTERPRISE_DB_HOST", "127.0.0.1"), os.getenv("ENTERPRISE_DB_PORT", "3306")
    name = os.getenv("ENTERPRISE_DB_NAME", "enterprise_business")
    return create_engine(f"mysql+pymysql://{user}:{quote_plus(password)}@{host}:{port}/{name}?charset=utf8mb4")


def purge(db, ent):
    org_ids = [r[0] for r in db.execute(text("SELECT id FROM organizations WHERE name = :n"), {"n": ORG_NAME}).all()]
    user_ids = [r[0] for r in db.execute(text("SELECT id FROM `user` WHERE name LIKE 'demo\\_%'")).all()]
    owner = db.execute(text("SELECT id FROM `user` WHERE name = 'demo_owner'")).scalar()
    # 演示部门：演示企业里的，加上放进已有企业时由 demo_owner 建的
    team_ids = [r[0] for r in db.execute(text(
        "SELECT id FROM teams WHERE organization_id IN (SELECT id FROM organizations WHERE name = :n) OR owner_user_id = :u"),
        {"n": ORG_NAME, "u": owner or 0}).all()]
    ids = ",".join(str(t) for t in team_ids) or "0"
    users = ",".join(str(u) for u in user_ids) or "0"
    with ent.begin() as conn:
        for sql in (
            f"DELETE FROM responsibility_event WHERE plan_id IN (SELECT id FROM responsibility_plan WHERE team_id IN ({ids}))",
            f"DELETE FROM responsibility_deliverable WHERE task_id IN (SELECT id FROM responsibility_task WHERE team_id IN ({ids}))",
            f"DELETE FROM responsibility_dependency WHERE task_id IN (SELECT id FROM responsibility_task WHERE team_id IN ({ids}))",
            f"DELETE FROM responsibility_collaborator WHERE task_id IN (SELECT id FROM responsibility_task WHERE team_id IN ({ids}))",
            f"DELETE FROM responsibility_task WHERE team_id IN ({ids})",
            f"DELETE FROM responsibility_plan WHERE team_id IN ({ids})",
            f"DELETE FROM hr_case_task WHERE case_id IN (SELECT id FROM hr_case WHERE team_id IN ({ids}))",
            f"DELETE FROM hr_case WHERE team_id IN ({ids})",
            f"DELETE FROM it_ticket_comment WHERE ticket_id IN (SELECT id FROM it_ticket WHERE team_id IN ({ids}))",
            f"DELETE FROM it_ticket WHERE team_id IN ({ids})",
            f"DELETE FROM it_device_event WHERE device_id IN (SELECT id FROM it_device WHERE managing_team_id IN ({ids}))",
            f"DELETE FROM it_device WHERE managing_team_id IN ({ids})",
            f"DELETE FROM voucher_entry WHERE voucher_id IN (SELECT id FROM voucher WHERE team_id IN ({ids}))",
            f"DELETE FROM voucher WHERE team_id IN ({ids})",
            f"DELETE FROM expense_line WHERE expense_claim_id IN (SELECT id FROM expense_claim WHERE team_id IN ({ids}))",
            f"DELETE FROM expense_claim WHERE team_id IN ({ids})",
            f"DELETE FROM expense_budget WHERE team_id IN ({ids})",
            f"DELETE FROM department_budget WHERE team_id IN ({ids})",
            f"DELETE FROM purchase_order WHERE purchase_request_id IN (SELECT id FROM purchase_request WHERE team_id IN ({ids}))",
            f"DELETE FROM purchase_request_line WHERE purchase_request_id IN (SELECT id FROM purchase_request WHERE team_id IN ({ids}))",
            f"DELETE FROM purchase_request WHERE team_id IN ({ids})",
            f"DELETE FROM leave_request WHERE applicant_user_id IN ({users})",
            f"DELETE FROM leave_balance WHERE user_id IN ({users})",
            f"DELETE FROM opportunity WHERE team_id IN ({ids})",
            f"DELETE FROM follow_up WHERE author_user_id IN ({users})",
            f"DELETE FROM contact WHERE customer_id IN (SELECT id FROM customer WHERE team_id IN ({ids}))",
            f"DELETE FROM customer WHERE team_id IN ({ids})",
            "DELETE FROM product WHERE sku IN ('DEMO-PAPER', 'DEMO-CHAIR', 'DEMO-PEN', 'DEMO-MOUSE')",
            f"DELETE FROM audit_event WHERE user_id IN ({users})",
        ):
            try:
                conn.execute(text(sql))
            except Exception as exc:  # noqa: BLE001 —— 个别表在老版本业务库里可能不存在
                print(f"  （跳过：{sql[:60]}… {str(exc)[:60]}）")
    if user_ids:
        rc._purge_users("id IN (" + users + ")")
    if team_ids:
        agents = [r[0] for r in db.execute(text(f"SELECT id FROM agent WHERE team_id IN ({ids})")).all()]
        db.commit()
        if agents:
            print("  （部门助手随用户清理一并删除）")
        db.execute(text(f"DELETE FROM team_members WHERE team_id IN ({ids})"))
        db.execute(text(f"DELETE FROM teams WHERE id IN ({ids})"))
    for org in org_ids:
        db.execute(text("DELETE FROM organization_members WHERE organization_id = :o"), {"o": org})
        db.execute(text("DELETE FROM organizations WHERE id = :o"), {"o": org})
    db.commit()
    print(f"已清理：{len(org_ids)} 个企业、{len(team_ids)} 个部门、{len(user_ids)} 个演示账号")


def create_structure(db):
    from models.user_dao import create_user
    from service.auth_service import hash_password
    from tests.test_enterprise_access import _add_org_member, _add_team_member
    users = {}
    for name in ACCOUNTS:
        users[name] = create_user(db, name=name, password=hash_password(PASSWORD), age=30)
    owner = users["demo_owner"]
    # 平台只服务一家企业（id 最小的那条，见 models/enterprise_dao.py）。全新部署启动时已经自动建好了企业
    # （service/enterprise_bootstrap.py），这时演示部门和账号要放进这家企业，不能再建第二家——
    # 否则演示账号都不算“本企业成员”，飞书 / 钉钉绑定这类企业级功能用不了（仿真联调发现的问题）。
    org = db.execute(text("SELECT id FROM organizations ORDER BY id LIMIT 1")).scalar()
    joined_existing = org is not None
    if not joined_existing:
        db.execute(text("INSERT INTO organizations (name, owner_user_id, status, created_at) VALUES (:n, :o, 'active', NOW())"),
                   {"n": ORG_NAME, "o": owner.id})
        db.commit()
        org = db.execute(text("SELECT id FROM organizations WHERE name = :n ORDER BY id DESC LIMIT 1"), {"n": ORG_NAME}).scalar()
    teams = {}
    for code, label in TEAMS.items():
        db.execute(text("INSERT INTO teams (organization_id, name, owner_user_id, status, department_code, created_at) "
                        "VALUES (:o, :n, :u, 'active', :c, NOW())"), {"o": org, "n": label, "u": owner.id, "c": code})
        db.commit()
        # 按“演示负责人建的”找：企业里可能已经有同类型的部门
        teams[code] = db.execute(text("SELECT id FROM teams WHERE organization_id = :o AND department_code = :c AND owner_user_id = :u "
                                      "ORDER BY id DESC LIMIT 1"), {"o": org, "c": code, "u": owner.id}).scalar()
    for name, (_, team, team_role, org_role) in ACCOUNTS.items():
        # 加入已有企业时不抢所有者：demo_owner 以企业管理员身份加入，原所有者不变
        _add_org_member(db, org, users[name].id, "admin" if joined_existing and org_role == "owner" else org_role)
        _add_team_member(db, teams[team], users[name].id, team_role)
    if joined_existing:
        name = db.execute(text("SELECT name FROM organizations WHERE id = :o"), {"o": org}).scalar()
        print(f"    放进已有企业「{name}」（平台只服务一家企业）")
    return org, teams, users


async def create_agents(org, teams, users):
    from service import agent_admin_service, department_agent_service
    owner = users["demo_owner"].id
    async with AsyncSessionLocal() as db:
        for code, team in teams.items():
            await department_agent_service.on_team_saved(db, team, owner, created=True)
            status = await department_agent_service.agent_status(db, team)
            if status.get("agent"):
                await agent_admin_service.update_managed_agent(db, status["agent"]["id"], owner, model_name=MODEL)
            await department_agent_service.publish(db, team, owner)
        central = await agent_admin_service.create_managed_agent(
            db, owner, "星河中央助手", "central", model_name=MODEL, template_id="central", organization_id=org)
        await agent_admin_service.update_managed_agent(db, central["id"], owner, lifecycle_status="published",
                                                       expected_row_version=central.get("row_version"))


def connect_models(db, users):
    from models.user_dao import get_user_by_id  # noqa: F401
    from service.llm.llm_config_service import save_config

    class U:
        def __init__(self, id):
            self.id = id
    for user in users.values():
        save_config(db, U(user.id), MODEL, "offline-demo")


def seed_business(ent, teams, users):
    year = TODAY.year
    with ent.begin() as conn:
        for code, team in teams.items():
            conn.execute(text("INSERT INTO expense_budget (team_id, year, remaining_amount) VALUES (:t, :y, :a)"),
                         {"t": team, "y": year, "a": 200000})
        conn.execute(text("INSERT INTO department_budget (team_id, year, remaining_amount) VALUES (:t, :y, 80000)"),
                     {"t": teams["procurement"], "y": year})
        for sku, name, unit, price, qty, safe in (("DEMO-PAPER", "A4 复印纸", "包", 22.5, 40, 50), ("DEMO-CHAIR", "人体工学椅", "把", 680, 12, 5),
                                                  ("DEMO-PEN", "签字笔（盒）", "盒", 18, 200, 30), ("DEMO-MOUSE", "无线鼠标", "个", 79, 25, 10)):
            conn.execute(text("INSERT INTO product (sku, name, unit, unit_price, on_hand_qty, safety_stock_qty) VALUES (:s,:n,:u,:p,:q,:f)"),
                         {"s": sku, "n": name, "u": unit, "p": price, "q": qty, "f": safe})
        types = {r[1]: r[0] for r in conn.execute(text("SELECT id, code FROM leave_type")).all()}
        for user in users.values():
            for code, days in (("annual", 10), ("sick", 15), ("personal", 5)):
                conn.execute(text("INSERT INTO leave_balance (user_id, leave_type_id, year, remaining_days) VALUES (:u,:t,:y,:d)"),
                             {"u": user.id, "t": types[code], "y": year, "d": days})
        for name, industry in (("远航物流", "物流"), ("启明教育", "教育"), ("云帆软件", "软件服务")):
            conn.execute(text("INSERT INTO customer (name, industry, owner_user_id, team_id, created_at) VALUES (:n,:i,:o,:t,NOW())"),
                         {"n": name, "i": industry, "o": users["demo_emp"].id, "t": teams["sales"]})
    return types


def seed_activity(teams, users):
    """经真实接口产生进行中的业务：需要 Java 业务服务在运行。"""
    client = rc.make_client()

    def headers(name):
        return {"Authorization": f"Bearer {rc.mint_token(users[name].id)}"}

    def call(method, path, who, **body):
        response = getattr(client, method)(path, headers=headers(who), **({"json": body} if body else {}))
        if response.status_code >= 400:
            raise RuntimeError(f"{method.upper()} {path} → {response.status_code} {response.text[:200]}")
        return response.json() if response.content else None

    t = teams
    # 财务：一张有风险的报销（无票据）已批准 → 自动生成待核对凭证；另一张还在等负责人审批
    claim = call("post", "/enterprise/finance/mine", "demo_emp", team_id=t["sales"], lines=[
        {"category": "TRAVEL", "amount": 860, "description": "客户现场培训差旅", "invoice_no": "DEMO-G1001"},
        {"category": "MEAL", "amount": 188, "description": "客户晚餐招待", "invoice_no": "DEMO-F2002"},
        {"category": "OTHER", "amount": 45, "description": "停车费"}])
    call("post", f"/enterprise/finance/{claim['id']}/submit", "demo_emp")
    call("post", f"/enterprise/finance/{claim['id']}/decide", "demo_head", team_id=t["sales"], action="approve", note="同意")
    pending = call("post", "/enterprise/finance/mine", "demo_emp", team_id=t["sales"], lines=[
        {"category": "OFFICE_SUPPLY", "amount": 129, "description": "打印耗材", "invoice_no": "DEMO-P3003"}])
    call("post", f"/enterprise/finance/{pending['id']}/submit", "demo_emp")
    # 人事：一张请假等负责人审批
    start = (TODAY + timedelta(days=10)).isoformat()
    leave = call("post", "/enterprise/oa/leave/mine", "demo_emp", team_id=t["sales"], leave_type_code="annual",
                 start_date=start, end_date=start, reason="家庭事务")
    call("post", f"/enterprise/oa/leave/{leave['id']}/submit", "demo_emp")
    # IT：设备入库；一张故障待接单、一张已解决待确认、一张设备申请待负责人批准
    for asset, kind, model in (("DEMO-NB-001", "LAPTOP", "ThinkPad T14"), ("DEMO-NB-002", "LAPTOP", "ThinkPad T14"),
                               ("DEMO-MN-001", "MONITOR", "Dell U2723QE")):
        call("post", "/enterprise/it/desk/devices", "demo_it", team_id=t["it"], asset_no=asset, device_type=kind, model=model,
             warranty_until=(TODAY + timedelta(days=700)).isoformat())
    call("post", "/enterprise/it/tickets", "demo_emp", team_id=t["sales"], category="INCIDENT", priority="HIGH",
         title="三楼打印机一直脱机", description="三楼打印机卡纸后一直脱机，整个销售部都没法打印，下午客户会议要用资料")
    done = call("post", "/enterprise/it/tickets", "demo_emp", team_id=t["sales"], category="INCIDENT",
                title="VPN 连不上", description="在家连 VPN 提示认证失败，没法远程办公")
    call("post", f"/enterprise/it/desk/tickets/{done['id']}/assign", "demo_it", team_id=t["it"], take=True)
    call("post", f"/enterprise/it/desk/tickets/{done['id']}/resolve", "demo_it", team_id=t["it"], resolution="重置了 VPN 证书并重新开通远程访问权限")
    call("post", "/enterprise/it/tickets", "demo_newbie", team_id=t["sales"], category="DEVICE",
         title="新人申请笔记本", description="新入职，需要一台笔记本电脑用于外勤拜访客户")
    # 人事：新人入职办理（已批准，办理清单进行中）
    case = call("post", "/enterprise/hr/cases", "demo_hr", team_id=t["hr"], case_type="ONBOARDING",
                employee_user_id=users["demo_newbie"].id, employee_team_id=t["sales"],
                effective_date=(TODAY + timedelta(days=5)).isoformat(), position="销售专员")
    call("post", f"/enterprise/hr/cases/{case['id']}/approve", "demo_head", team_id=t["sales"])
    plans = seed_responsibility(call, t, users)
    return {"claim": claim["id"], "ticket": done["id"], "hr_case": case["id"], "plans": plans}


def seed_responsibility(call, t, users):
    """责任协同的历史：一份已闭环的计划（含一次验收退回）和一份进行中的计划（待接受 / 执行中 / 受阻），
    让部门看板和效率指标一开始就有内容；全部走真实接口。演示现场再用会议纪要现做一份新的。"""
    emp, newbie, owner = users["demo_emp"].id, users["demo_newbie"].id, users["demo_owner"].id
    day = lambda n: (TODAY + timedelta(days=n)).isoformat()  # noqa: E731

    def task(title, who, due, deliverable, criteria, evidence, **extra):
        return {"title": title, "responsible_user_id": who, "reviewer_user_id": owner, "due_date": day(due), "deliverable": deliverable,
                "acceptance_criteria": criteria, "priority": "NORMAL", "evidence": evidence, **extra}

    review_text = ("9月新品发布复盘会纪要：demo_emp负责整理发布复盘报告，下周前提交复盘报告，验收标准是包含数据与改进项。"
                   "demo_newbie负责汇总客户反馈，下周前提交反馈汇总表，验收标准是覆盖全部客户。demo_emp负责更新销售话术，下周前提交话术文档。")
    done_plan = call("post", "/enterprise/responsibility/plans", "demo_head", team_id=t["sales"], title="9月新品发布复盘会", source_type="MEETING",
                     source_text=review_text, summary="复盘新品发布，三项改进事项已分派", tasks=[
        task("整理发布复盘报告", emp, 5, "复盘报告", "包含数据与改进项", "demo_emp负责整理发布复盘报告，下周前提交复盘报告，验收标准是包含数据与改进项"),
        task("汇总客户反馈", newbie, 6, "反馈汇总表", "覆盖全部客户", "demo_newbie负责汇总客户反馈，下周前提交反馈汇总表，验收标准是覆盖全部客户"),
        task("更新销售话术", emp, 7, "话术文档", "覆盖新品的核心卖点", "demo_emp负责更新销售话术，下周前提交话术文档")])
    call("post", f"/enterprise/responsibility/plans/{done_plan['id']}/publish", "demo_head", team_id=t["sales"], note="按复盘会决定指派")
    first, second, third = [x["id"] for x in done_plan["tasks"]]
    for task_id, who in ((first, "demo_emp"), (second, "demo_newbie"), (third, "demo_emp")):
        call("post", f"/enterprise/responsibility/tasks/{task_id}/accept", who, team_id=t["sales"])
        call("post", f"/enterprise/responsibility/tasks/{task_id}/submit", who, team_id=t["sales"], summary="已完成，成果见共享盘", link="https://example.com/docs/" + str(task_id))
    call("post", f"/enterprise/responsibility/tasks/{first}/verify", "demo_owner", team_id=t["hr"], note="符合标准")
    call("post", f"/enterprise/responsibility/tasks/{second}/rework", "demo_owner", team_id=t["hr"], reason="没有覆盖云帆软件的反馈")
    call("post", f"/enterprise/responsibility/tasks/{second}/submit", "demo_newbie", team_id=t["sales"], summary="补充了云帆软件的反馈")
    call("post", f"/enterprise/responsibility/tasks/{second}/verify", "demo_owner", team_id=t["hr"])
    call("post", f"/enterprise/responsibility/tasks/{third}/verify", "demo_owner", team_id=t["hr"])

    visit_text = ("10月重点客户回访安排：demo_emp负责回访远航物流并记录需求，本月底前完成，验收标准是形成需求记录。"
                  "demo_newbie负责准备启明教育报价方案，下周前提交报价方案。demo_emp负责对接云帆软件试用账号，下周前完成。")
    live = call("post", "/enterprise/responsibility/plans", "demo_head", team_id=t["sales"], title="10月重点客户回访安排", source_type="MEETING",
                source_text=visit_text, summary="三个重点客户本月跟进", tasks=[
        task("回访远航物流并记录需求", emp, 20, "需求记录", "形成需求记录", "demo_emp负责回访远航物流并记录需求，本月底前完成，验收标准是形成需求记录"),
        task("准备启明教育报价方案", newbie, 8, "报价方案", "含两档价格方案", "demo_newbie负责准备启明教育报价方案，下周前提交报价方案"),
        task("对接云帆软件试用账号", emp, 9, "试用账号开通记录", "客户确认可登录", "demo_emp负责对接云帆软件试用账号，下周前完成")])
    call("post", f"/enterprise/responsibility/plans/{live['id']}/publish", "demo_head", team_id=t["sales"])
    a, _b, c = [x["id"] for x in live["tasks"]]
    call("post", f"/enterprise/responsibility/tasks/{a}/accept", "demo_emp", team_id=t["sales"])
    call("post", f"/enterprise/responsibility/tasks/{a}/progress", "demo_emp", team_id=t["sales"], note="已约好周三上门回访", percent=30)
    call("post", f"/enterprise/responsibility/tasks/{c}/accept", "demo_emp", team_id=t["sales"])
    call("post", f"/enterprise/responsibility/tasks/{c}/block", "demo_emp", team_id=t["sales"], reason="等待 IT 开通试用环境账号")
    return {"completed": done_plan["id"], "in_progress": live["id"]}


def main() -> int:
    ent = enterprise_engine()
    bootstrap_database()
    db = SessionLocal()
    try:
        if "--purge" in sys.argv:
            purge(db, ent)
            return 0
        if "--reset" in sys.argv:
            purge(db, ent)
        elif db.execute(text("SELECT 1 FROM `user` WHERE name = 'demo_owner'")).first():
            print("演示数据已存在。要重建加 --reset。")
            print_hint()
            return 0
        print("1/4 建企业、部门、账号…")
        org, teams, users = create_structure(db)
        print("2/4 部门助手与模型连接…")
        asyncio.run(create_agents(org, teams, users))
        connect_models(db, users)
        print("3/4 业务系统数据（预算、产品、客户、假期余额）…")
        seed_business(ent, teams, users)
        print("4/4 进行中的业务单据（经真实接口）…")
        try:
            created = seed_activity(teams, users)
            print(f"    已生成：报销单 #{created['claim']}（含待核对凭证）、IT 工单、人事入职办理 #{created['hr_case']}、责任计划 #{created['plans']['completed']} / #{created['plans']['in_progress']} 等")
        except Exception as exc:  # noqa: BLE001
            print(f"    跳过（需要 Java 业务服务在 {os.getenv('ENTERPRISE_HUB_BASE_URL', 'http://127.0.0.1:8090')} 运行）：{exc}")
    finally:
        db.close()
    print_hint()
    return 0


def print_hint():
    print("\n" + "-" * 60)
    print(f"  Docker 登录 http://localhost:8080（本地前端开发：http://localhost:5173），所有演示账号密码：{PASSWORD}")
    for name, (purpose, team, _, org_role) in ACCOUNTS.items():
        print(f"   {name:<12} {TEAMS[team]:<6} {purpose}")
    print("  演示脚本见 docs/demo-script.md")
    print("-" * 60)


if __name__ == "__main__":
    raise SystemExit(main())
