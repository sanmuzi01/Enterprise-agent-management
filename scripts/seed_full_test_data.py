"""全功能测试数据：在**已有企业和部门**上补齐每个功能都能点开试的数据（不新建第二家企业、不和已发布的部门助手冲突）。

和 scripts/seed_enterprise_demo.py 的区别：那个脚本从零建一家演示企业（空库用）；这个脚本用于已经在用的环境——
沿用企业里现有的人事 / 采购 / 销售 / 财务部门，缺 IT 部门就补一个，把演示账号（demo_*，密码 Demo@12345）放进这些部门，
然后尽量**经真实接口**（和员工在页面上操作同一条路）产生各模块的数据：

  人事：假期余额、待审批 / 已批准的请假、新人入职办理；考勤：两周打卡记录 → 分析出迟到 / 缺卡 / 旷工异常，一条已说明待认定
  财务：报销（已批准自动生成待核对凭证 / 待审批）、文本发票识别（低置信度字段待核对）
  采购：产品库存（含低于安全库存的）、部门预算、采购申请（待审批）
  IT：设备台账、工单（待接单 / 已解决待确认 / 设备申请待批准）
  CRM：客户、联系人、商机（不同阶段）、跟进记录；沟通收集（邮件、会议、电话纪要、“保存到CRM”、转发的聊天记录、待归属）；
       风险扫描与下一步建议
  责任协同：一份已闭环（含一次退回）和一份进行中的计划
  其他：提效统计、提醒 / 待办 / 站内通知（跑一遍提醒规则）

可以重复运行：已经有的数据不会重复造（按名字 / 单号判断）。只清理测试数据：加 --purge（删 demo_* 账号和它们产生的业务数据、
DEMO- 开头的产品和设备、脚本建的 IT 部门；你自己的账号、部门、客户都不动）。

用法（Docker）：docker compose exec api python scripts/seed_full_test_data.py [--purge]
绝不在生产环境运行。
"""
import asyncio
import os
import pathlib
import sys
import traceback
import uuid
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

from service.config_validation import is_production  # noqa: E402

if is_production():
    print("拒绝执行：APP_ENV=production。测试数据脚本会建固定密码的账号，不能对生产库跑。")
    sys.exit(1)

from scripts.seed_enterprise_demo import ACCOUNTS, PASSWORD, enterprise_engine  # noqa: E402
from tests import _route_client as rc  # noqa: E402

from sqlalchemy import text  # noqa: E402

from models.async_db import AsyncSessionLocal  # noqa: E402
from models.init_db import SessionLocal  # noqa: E402

BJ = timezone(timedelta(hours=8))
TODAY = datetime.now(BJ).date()
YEAR = TODAY.year
IT_TEAM_NAME = "IT 部"
RESULTS = []


def step(title):
    """每一步单独成败：一步失败不影响后面的（打印原因），最后汇总。"""
    def wrap(fn):
        def run(*args, **kwargs):
            try:
                note = fn(*args, **kwargs)
                RESULTS.append((title, "✓", note or ""))
                print(f"  ✓ {title}" + (f"：{note}" if note else ""), flush=True)
            except Exception as exc:  # noqa: BLE001
                RESULTS.append((title, "✗", f"{type(exc).__name__}: {str(exc)[:200]}"))
                print(f"  ✗ {title}：{type(exc).__name__}: {str(exc)[:300]}", flush=True)
                if os.getenv("SEED_DEBUG"):
                    traceback.print_exc()
        return run
    return wrap


def run_async(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ------------------------------------------------------------------ 结构：部门、账号、助手

def enterprise_id(db) -> int:
    org = db.execute(text("SELECT id FROM organizations ORDER BY id LIMIT 1")).scalar()
    if org is None:
        raise SystemExit("库里还没有企业：先启动一次平台（会自动建企业），或用 scripts/seed_enterprise_demo.py 从零建演示企业。")
    return int(org)


def ensure_accounts(db, org):
    from models.user_dao import create_user
    from service.auth_service import hash_password
    from tests.test_enterprise_access import _add_org_member
    users, created = {}, []
    for name in ACCOUNTS:
        user = db.execute(text("SELECT id FROM `user` WHERE name = :n"), {"n": name}).scalar()
        if user is None:
            user = create_user(db, name=name, password=hash_password(PASSWORD), age=30).id
            created.append(name)
        else:
            # 演示账号统一用文档里的密码（docs/demo-script.md），方便登录测试；只动 demo_* 账号
            db.execute(text("UPDATE `user` SET password = :p, is_disabled = 0 WHERE id = :u"), {"p": hash_password(PASSWORD), "u": user})
            db.commit()
        users[name] = int(user)
        member = db.execute(text("SELECT status FROM organization_members WHERE organization_id = :o AND user_id = :u"),
                            {"o": org, "u": user}).scalar()
        if member is None:
            # demo_owner 以企业管理员身份加入（不抢原有的企业所有者）
            _add_org_member(db, org, user, "admin" if ACCOUNTS[name][3] == "owner" else ACCOUNTS[name][3])
        elif member != "active":
            db.execute(text("UPDATE organization_members SET status = 'active' WHERE organization_id = :o AND user_id = :u"),
                       {"o": org, "u": user})
            db.commit()
        if ACCOUNTS[name][3] == "owner":
            # 演示的企业管理员：责任协同的验收人、跨部门查看都要求企业管理员身份（以前半截导入可能只是普通成员）
            db.execute(text("UPDATE organization_members SET role_id = (SELECT id FROM enterprise_role WHERE scope = 'organization' "
                            "AND code = 'admin') WHERE organization_id = :o AND user_id = :u"), {"o": org, "u": user})
            db.commit()
    return users, created


def ensure_teams(db, org, users):
    teams = {}
    for code in ("hr", "procurement", "sales", "finance", "it"):
        team = db.execute(text("SELECT id FROM teams WHERE organization_id = :o AND department_code = :c AND status = 'active' "
                               "ORDER BY id LIMIT 1"), {"o": org, "c": code}).scalar()
        if team is None:
            name = IT_TEAM_NAME if code == "it" else {"hr": "人事部", "procurement": "采购部", "sales": "销售部", "finance": "财务部"}[code]
            db.execute(text("INSERT INTO teams (organization_id, name, owner_user_id, status, department_code, created_at) "
                            "VALUES (:o, :n, :u, 'active', :c, NOW())"), {"o": org, "n": name, "u": users["demo_owner"], "c": code})
            db.commit()
            team = db.execute(text("SELECT id FROM teams WHERE organization_id = :o AND department_code = :c AND owner_user_id = :u "
                                   "ORDER BY id DESC LIMIT 1"), {"o": org, "c": code, "u": users["demo_owner"]}).scalar()
        teams[code] = int(team)
    return teams


def ensure_memberships(db, teams, users):
    from tests.test_enterprise_access import _add_team_member
    placed = []
    for name, (_, code, team_role, _) in ACCOUNTS.items():
        current = db.execute(text("SELECT team_id FROM team_members WHERE user_id = :u AND status = 'active'"), {"u": users[name]}).scalar()
        if current is None:
            db.execute(text("DELETE FROM team_members WHERE user_id = :u"), {"u": users[name]})   # 停用的旧记录（一人一个部门）
            db.commit()
            _add_team_member(db, teams[code], users[name], team_role)
            placed.append(name)
    return placed


async def ensure_agents(teams, operator):
    from service import department_agent_service as svc
    notes = []
    async with AsyncSessionLocal() as db:
        for code, team in teams.items():
            status = await svc.agent_status(db, team)
            if status["agent"] is None:
                await svc.on_team_saved(db, team, operator, created=True)
                status = await svc.agent_status(db, team)
            if status["state"] == "needs_repair":
                status = await svc.repair(db, team, operator)
            if status["state"] == "pending_publish":
                status = await svc.publish(db, team, operator)
            notes.append(f"{status['team_name']}={status['state_label']}")
    return "，".join(notes)


# ------------------------------------------------------------------ 业务系统基础数据（直接写业务库，幂等）

def seed_business_base(ent, teams, users):
    demo_ids = list(users.values())
    with ent.begin() as conn:
        for team in teams.values():
            conn.execute(text("INSERT INTO expense_budget (team_id, year, remaining_amount) SELECT :t, :y, 200000 FROM DUAL "
                              "WHERE NOT EXISTS (SELECT 1 FROM expense_budget WHERE team_id = :t AND year = :y)"), {"t": team, "y": YEAR})
        conn.execute(text("INSERT INTO department_budget (team_id, year, remaining_amount) SELECT :t, :y, 80000 FROM DUAL "
                          "WHERE NOT EXISTS (SELECT 1 FROM department_budget WHERE team_id = :t AND year = :y)"),
                     {"t": teams["procurement"], "y": YEAR})
        products = (("DEMO-PAPER", "A4 复印纸", "包", 22.5, 40, 50), ("DEMO-CHAIR", "人体工学椅", "把", 680, 12, 5),
                    ("DEMO-PEN", "签字笔（盒）", "盒", 18, 200, 30), ("DEMO-MOUSE", "无线鼠标", "个", 79, 25, 10),
                    ("DEMO-TONER", "打印机硒鼓", "个", 260, 2, 6), ("DEMO-MONITOR", "27 寸显示器", "台", 1499, 4, 3),
                    ("DEMO-WATER", "桶装水", "桶", 18, 6, 20), ("DEMO-HEADSET", "降噪耳机", "副", 399, 1, 4))
        for sku, name, unit, price, qty, safe in products:
            conn.execute(text("INSERT INTO product (sku, name, unit, unit_price, on_hand_qty, safety_stock_qty, supplier_code) "
                              "SELECT :s, :n, :u, :p, :q, :f, 'SUP-DEMO' FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM product WHERE sku = :s)"),
                         {"s": sku, "n": name, "u": unit, "p": price, "q": qty, "f": safe})
        types = {r[1]: r[0] for r in conn.execute(text("SELECT id, code FROM leave_type")).all()}
        for uid in demo_ids:
            for code, days in (("annual", 10), ("sick", 15), ("personal", 5)):
                conn.execute(text("INSERT INTO leave_balance (user_id, leave_type_id, year, remaining_days) SELECT :u, :t, :y, :d FROM DUAL "
                                  "WHERE NOT EXISTS (SELECT 1 FROM leave_balance WHERE user_id = :u AND leave_type_id = :t AND year = :y)"),
                             {"u": uid, "t": types[code], "y": YEAR, "d": days})
        customers = (("华星科技有限公司", "制造", (("王总", "采购总监", "13800001111", "wang@huaxing.com"), ("刘工", "IT 主管", "13800002222", "liu@huaxing.com"))),
                     ("远航物流", "物流", (("陈经理", "仓储经理", "13900003333", "chen@yuanhang-logistics.com"),)),
                     ("启明教育", "教育", (("李老师", "教务主任", "13700004444", "li@qiming-edu.cn"),)),
                     ("云帆软件", "软件服务", (("赵总", "CEO", "13600005555", "zhao@yunfan.io"),)),
                     ("蓝海餐饮集团", "餐饮", (("孙经理", "行政经理", "13500006666", "sun@lanhai-food.com"),)))
        for name, industry, contacts in customers:
            conn.execute(text("INSERT INTO customer (name, industry, owner_user_id, team_id, created_at) SELECT :n, :i, :o, :t, NOW() FROM DUAL "
                              "WHERE NOT EXISTS (SELECT 1 FROM customer WHERE name = :n AND team_id = :t)"),
                         {"n": name, "i": industry, "o": users["demo_emp"], "t": teams["sales"]})
            cid = conn.execute(text("SELECT id FROM customer WHERE name = :n AND team_id = :t"), {"n": name, "t": teams["sales"]}).scalar()
            for cname, title, phone, email in contacts:
                conn.execute(text("INSERT INTO contact (customer_id, name, title, phone, email) SELECT :c, :n, :ti, :p, :e FROM DUAL "
                                  "WHERE NOT EXISTS (SELECT 1 FROM contact WHERE customer_id = :c AND name = :n)"),
                             {"c": cid, "n": cname, "ti": title, "p": phone, "e": email})
    return f"{len(products)} 个产品（{sum(1 for p in products if p[4] < p[5])} 个低于安全库存）、{len(customers)} 个客户及联系人、预算和假期余额"


# ------------------------------------------------------------------ 经真实接口产生的业务

class Api:
    """在 api 进程里直接调接口（和页面上操作同一条路：鉴权、权限、业务规则都生效）。"""

    def __init__(self, users):
        self.client = rc.make_client()
        self.users = users

    def headers(self, who):
        return {"Authorization": f"Bearer {rc.mint_token(self.users[who])}"}

    def call(self, method, path, who, body=None, **kwargs):
        if body is not None:
            kwargs["json"] = body
        response = getattr(self.client, method)(path, headers=self.headers(who), **kwargs)
        if response.status_code >= 400:
            raise RuntimeError(f"{method.upper()} {path} → {response.status_code} {response.text[:200]}")
        return response.json() if response.content else None


def exists(ent, sql, **params) -> bool:
    with ent.connect() as conn:
        return bool(conn.execute(text(sql), params).scalar())


def seed_leave_and_finance(api, ent, t, users):
    notes = []
    if not exists(ent, "SELECT 1 FROM expense_claim c JOIN expense_line l ON l.expense_claim_id = c.id "
                       "WHERE c.applicant_user_id = :u AND l.description = '客户现场培训差旅'", u=users["demo_emp"]):
        claim = api.call("post", "/enterprise/finance/mine", "demo_emp", {"team_id": t["sales"], "lines": [
            {"category": "TRAVEL", "amount": 860, "description": "客户现场培训差旅", "invoice_no": "DEMO-G1001"},
            {"category": "MEAL", "amount": 188, "description": "客户晚餐招待", "invoice_no": "DEMO-F2002"},
            {"category": "OTHER", "amount": 45, "description": "停车费"}]})
        api.call("post", f"/enterprise/finance/{claim['id']}/submit", "demo_emp")
        api.call("post", f"/enterprise/finance/{claim['id']}/decide", "demo_head", {"team_id": t["sales"], "action": "approve", "note": "同意"})
        pending = api.call("post", "/enterprise/finance/mine", "demo_emp", {"team_id": t["sales"], "lines": [
            {"category": "OFFICE_SUPPLY", "amount": 129, "description": "打印耗材", "invoice_no": "DEMO-P3003"}]})
        api.call("post", f"/enterprise/finance/{pending['id']}/submit", "demo_emp")
        draft = api.call("post", "/enterprise/finance/mine", "demo_newbie", {"team_id": t["sales"], "lines": [
            {"category": "TRAVEL", "amount": 320, "description": "拜访客户高铁票"}]})
        notes.append(f"报销：已批准 #{claim['id']}（生成待核对凭证）、待审批 #{pending['id']}、草稿 #{draft['id']}")
    if not exists(ent, "SELECT 1 FROM leave_request WHERE applicant_user_id = :u AND reason = '家庭事务'", u=users["demo_emp"]):
        start = (TODAY + timedelta(days=10)).isoformat()
        leave = api.call("post", "/enterprise/oa/leave/mine", "demo_emp", {"team_id": t["sales"], "leave_type_code": "annual",
                                                                           "start_date": start, "end_date": start, "reason": "家庭事务"})
        api.call("post", f"/enterprise/oa/leave/{leave['id']}/submit", "demo_emp")
        notes.append(f"请假：待审批 #{leave['id']}")
    past = _workdays_back(4)[0]
    if not exists(ent, "SELECT 1 FROM leave_request WHERE applicant_user_id = :u AND reason = '看病'", u=users["demo_newbie"]):
        leave = api.call("post", "/enterprise/oa/leave/mine", "demo_newbie", {"team_id": t["sales"], "leave_type_code": "sick",
                                                                              "start_date": past.isoformat(), "end_date": past.isoformat(), "reason": "看病"})
        api.call("post", f"/enterprise/oa/leave/{leave['id']}/submit", "demo_newbie")
        api.call("post", f"/enterprise/oa/leave/{leave['id']}/decide", "demo_head", {"team_id": t["sales"], "action": "approve", "note": "注意休息"})
        notes.append(f"请假：已批准病假 #{leave['id']}（考勤分析时这天不算旷工）")
    return "；".join(notes) or "已有，跳过"


def seed_procurement(api, ent, t, users):
    if exists(ent, "SELECT 1 FROM purchase_request WHERE requester_user_id = :u", u=users["demo_buyer"]):
        return "已有，跳过"
    draft = api.call("post", "/enterprise/procurement/mine", "demo_buyer",
                     {"team_id": t["procurement"], "lines": [{"sku": "DEMO-TONER", "quantity": 6}, {"sku": "DEMO-PAPER", "quantity": 30}]})
    api.call("post", f"/enterprise/procurement/{draft['id']}/submit", "demo_buyer")
    second = api.call("post", "/enterprise/procurement/mine", "demo_buyer",
                      {"team_id": t["procurement"], "lines": [{"sku": "DEMO-HEADSET", "quantity": 4}]})
    return f"采购申请：待审批 #{draft['id']}、草稿 #{second['id']}"


def seed_it(api, ent, t, users):
    notes = []
    for asset, kind, model in (("DEMO-NB-001", "LAPTOP", "ThinkPad T14"), ("DEMO-NB-002", "LAPTOP", "ThinkPad T14"),
                               ("DEMO-MN-001", "MONITOR", "Dell U2723QE"), ("DEMO-PR-001", "PERIPHERAL", "HP LaserJet M404 打印机")):
        if not exists(ent, "SELECT 1 FROM it_device WHERE asset_no = :a", a=asset):
            api.call("post", "/enterprise/it/desk/devices", "demo_it", {"team_id": t["it"], "asset_no": asset, "device_type": kind,
                                                                         "model": model, "warranty_until": (TODAY + timedelta(days=700)).isoformat()})
            notes.append(asset)
    if not exists(ent, "SELECT 1 FROM it_ticket WHERE requester_user_id = :u AND title = '三楼打印机一直脱机'", u=users["demo_emp"]):
        api.call("post", "/enterprise/it/tickets", "demo_emp", {"team_id": t["sales"], "category": "INCIDENT", "priority": "HIGH",
                                                                 "title": "三楼打印机一直脱机",
                                                                 "description": "三楼打印机卡纸后一直脱机，整个销售部都没法打印，下午客户会议要用资料"})
        done = api.call("post", "/enterprise/it/tickets", "demo_emp", {"team_id": t["sales"], "category": "INCIDENT",
                                                                        "title": "VPN 连不上", "description": "在家连 VPN 提示认证失败，没法远程办公"})
        api.call("post", f"/enterprise/it/desk/tickets/{done['id']}/assign", "demo_it", {"team_id": t["it"], "take": True})
        api.call("post", f"/enterprise/it/desk/tickets/{done['id']}/resolve", "demo_it",
                 {"team_id": t["it"], "resolution": "重置了 VPN 证书并重新开通远程访问权限"})
        api.call("post", "/enterprise/it/tickets", "demo_newbie", {"team_id": t["sales"], "category": "DEVICE",
                                                                    "title": "新人申请笔记本", "description": "新入职，需要一台笔记本电脑用于外勤拜访客户"})
        api.call("post", "/enterprise/it/tickets", "demo_buyer", {"team_id": t["procurement"], "category": "ACCOUNT", "priority": "LOW",
                                                                   "title": "开通供应商门户账号", "description": "新供应商要求在他们门户上对账，需要开通账号"})
        notes.append("工单 4 张（待接单 / 已解决待确认 / 设备申请待批准 / 账号申请）")
    return "、".join(notes) or "已有，跳过"


def seed_hr(api, ent, t, users):
    if exists(ent, "SELECT 1 FROM hr_case WHERE employee_user_id = :u", u=users["demo_newbie"]):
        return "已有，跳过"
    case = api.call("post", "/enterprise/hr/cases", "demo_hr", {"team_id": t["hr"], "case_type": "ONBOARDING",
                                                                 "employee_user_id": users["demo_newbie"], "employee_team_id": t["sales"],
                                                                 "effective_date": (TODAY + timedelta(days=5)).isoformat(), "position": "销售专员"})
    api.call("post", f"/enterprise/hr/cases/{case['id']}/approve", "demo_head", {"team_id": t["sales"]})
    return f"入职办理 #{case['id']}（已批准，办理清单进行中）"


def seed_responsibility(api, ent, t, users):
    if exists(ent, "SELECT 1 FROM responsibility_plan WHERE created_by = :u AND title = '9月新品发布复盘会'", u=users["demo_head"]):
        return "已有，跳过"
    from scripts.seed_enterprise_demo import seed_responsibility as demo_plans

    class Who:
        def __init__(self, uid):
            self.id = uid

    def call(method, path, who, **body):
        return api.call(method, path, who, body or None)
    plans = demo_plans(call, t, {name: Who(uid) for name, uid in users.items()})
    return f"计划 #{plans['completed']}（已闭环）、#{plans['in_progress']}（进行中）"


def seed_crm(api, ent, t, users):
    with ent.connect() as conn:
        customers = dict(conn.execute(text("SELECT name, id FROM customer WHERE team_id = :t"), {"t": t["sales"]}).all())
    notes = []
    plan = (("华星科技有限公司", "NEGOTIATION", 380000, 20, "等王总确认预算，下周三前答复"),
            ("远航物流", "PROPOSAL", 126000, 35, "仓储方案已发，等对方内部评估"),
            ("启明教育", "QUALIFIED", 58000, 50, "安排课程平台试用"),
            ("云帆软件", "WON", 92000, -5, "已签约，安排交付"),
            ("蓝海餐饮集团", "LEAD", 30000, 60, "约第一次拜访"))
    for name, stage, amount, days, next_step in plan:
        cid = customers.get(name)
        if not cid or exists(ent, "SELECT 1 FROM opportunity WHERE customer_id = :c", c=cid):
            continue
        api.call("post", f"/enterprise/crm/customers/{cid}/opportunities", "demo_emp",
                 {"team_id": t["sales"], "stage": stage, "amount": amount,
                  "expected_close_date": (TODAY + timedelta(days=days)).isoformat(), "next_step": next_step})
        followup = api.call("post", f"/enterprise/crm/customers/{cid}/followups", "demo_emp",
                            {"team_id": t["sales"], "content": f"电话沟通：{next_step}。"})
        if stage != "LEAD":
            api.call("post", f"/enterprise/crm/followups/{followup['id']}/confirm", "demo_emp")
        notes.append(f"{name}（{stage}）")
    return "商机与跟进：" + "、".join(notes) if notes else "已有，跳过"


def _eml(sender, subject, body, days_ago):
    from email.message import EmailMessage
    from email.utils import format_datetime
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = sender, "demo_emp@example.com", subject
    msg["Date"] = format_datetime(datetime.now(BJ) - timedelta(days=days_ago))
    msg["Message-ID"] = f"<demo-{uuid.uuid5(uuid.NAMESPACE_URL, subject).hex}@example.com>"
    msg.set_content(body)
    return msg.as_bytes()


def seed_crm_activities(api, t, users):
    """沟通收集：邮件（对上客户 / 对不上待归属）、会议、电话纪要、“保存到CRM”、转发的聊天记录。来源编号固定，重复运行不会重复。"""
    created = 0
    for sender, subject, body, days in (
            ("wang@huaxing.com", "关于二期报价的反馈", "王总：二期报价我们内部看过了，价格可以再谈，预算下周三前给答复。", 3),
            ("chen@yuanhang-logistics.com", "仓储方案疑问", "陈经理：方案里的分拣时效能不能再提前半天？", 6),
            ("someone@gmail.com", "合作意向", "您好，我们是一家连锁药店，想了解贵司的方案，方便约个时间吗？", 1)):
        result = api.call("post", "/enterprise/crm-copilot/ingest/email", "demo_emp", data={"team_id": str(t["sales"])},
                          files={"file": (f"{subject}.eml", _eml(sender, subject, body, days), "message/rfc822")})
        created += 0 if result.get("duplicate") else 1
    start = (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y%m%dT020000Z")
    ics = ("BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:demo-qiming-review\nSUMMARY:启明教育试用复盘会\n"
           f"DTSTART:{start}\nATTENDEE:mailto:li@qiming-edu.cn\nDESCRIPTION:李老师反馈老师们用得顺手，希望增加作业批改功能。\n"
           "END:VEVENT\nEND:VCALENDAR\n").encode()
    result = api.call("post", "/enterprise/crm-copilot/ingest/calendar", "demo_emp", data={"team_id": str(t["sales"])},
                      files={"file": ("meeting.ics", ics, "text/calendar")})
    created += result.get("created", 0)

    async def chats():
        from service.crm import chat_ingest
        from service.integrations.base import ChatLine
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        async with AsyncSessionLocal() as db:
            await chat_ingest.save(db, users["demo_emp"], "feishu", "demo-msg-yunfan-1", "保存到CRM：云帆软件赵总说交付排期要提前到月底，需要我们加人")
            lines = [ChatLine("ou_demo_emp", "demo_emp", "赵总，交付计划表发您了，请看下排期", now - timedelta(hours=5)),
                     ChatLine("ou_zhao", "外部成员1", "收到，月底前能上线吗？我们这边催得紧", now - timedelta(hours=4, minutes=50)),
                     ChatLine("ou_demo_emp", "demo_emp", "可以，我们加派两名工程师", now - timedelta(hours=4, minutes=40))]
            await chat_ingest.save_forward(db, users["demo_emp"], "feishu", "demo-forward-yunfan-1", lines)
    run_async(chats())
    return f"邮件 3 封（1 封对不上客户进“待归属”）、会议 1 个、聊天保存 2 条（本次新增 {created} 条邮件 / 会议）"


async def seed_crm_insights():
    from service.reminders.rules import run_crm_risks
    async with AsyncSessionLocal() as db:
        created, _ = await run_crm_risks(db)
    return f"按规则扫描风险并生成下一步建议（新增提醒 {created} 条）"


def text_pdf(lines) -> bytes:
    """生成一个带文字层的最小 PDF（和电子发票一样文字可以直接读出来，不需要 OCR）。
    文字用 Type0 字体 + Identity-H 编码，CID 取汉字的 Unicode 码位，ToUnicode 表把它映射回原字——
    读 PDF 文字的程序（pypdf 等）能原样读出中文。没有嵌入字体文件，只用于测试“上传发票 → 识别 → 核对”流程。"""
    chars = sorted({ch for line in lines for ch in line})
    stream = "BT /F1 12 Tf 50 780 Td 16 TL\n" + "".join(
        "<" + "".join(f"{ord(ch):04X}" for ch in line) + "> Tj T*\n" for line in lines) + "ET"
    blocks = [chars[i:i + 100] for i in range(0, len(chars), 100)]
    cmap = ("/CIDInit /ProcSet findresource begin 12 dict begin begincmap /CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) "
            "/Supplement 0 >> def /CMapName /Adobe-Identity-UCS def /CMapType 2 def 1 begincodespacerange <0000> <FFFF> "
            "endcodespacerange\n"
            + "".join(f"{len(b)} beginbfchar\n" + "".join(f"<{ord(c):04X}> <{ord(c):04X}>\n" for c in b) + "endbfchar\n" for b in blocks)
            + "endcmap CMapName currentdict /CMap defineresource pop end end")
    objects = ["<< /Type /Catalog /Pages 2 0 R >>",
               "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
               f"<< /Length {len(stream.encode())} >>\nstream\n{stream}\nendstream",
               "<< /Type /Font /Subtype /Type0 /BaseFont /SimSun /Encoding /Identity-H /DescendantFonts [6 0 R] /ToUnicode 7 0 R >>",
               "<< /Type /Font /Subtype /CIDFontType2 /BaseFont /SimSun /CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) "
               "/Supplement 0 >> /CIDToGIDMap /Identity /DW 1000 >>",
               f"<< /Length {len(cmap.encode())} >>\nstream\n{cmap}\nendstream"]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n{body}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{o:010d} 00000 n \n" for o in offsets).encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def seed_invoice(api, t, users):
    db = SessionLocal()
    try:
        found = db.execute(text("SELECT id, status, invoice_number, total_amount FROM invoice_extraction "
                                "WHERE user_id = :u AND invoice_number = '24440000000012345678' ORDER BY id LIMIT 1"),
                           {"u": users["demo_emp"]}).first()
    finally:
        db.close()
    if found:
        return f"已有（识别记录 #{found[0]}，状态 {found[1]}），跳过"
    issued = TODAY - timedelta(days=2)
    lines = ["电子发票（普通发票）", "发票代码：044031900111  发票号码：24440000000012345678",
             f"开票日期：{issued.year}年{issued.month:02d}月{issued.day:02d}日",
             "购买方 名称：默认企业 纳税人识别号：91440300MA5XXXXXX1",
             "销售方 名称：深圳市远航商旅服务有限公司 纳税人识别号：91440300MA5YYYYYY2",
             "项目名称 *旅游服务*住宿费", "价税合计（大写）捌佰陆拾元整 （小写）¥860.00"]
    result = api.call("post", "/enterprise/finance/invoices/extract", "demo_emp", data={"team_id": str(t["sales"])},
                      files={"file": ("住宿费发票.pdf", text_pdf(lines), "application/pdf")})
    db = SessionLocal()
    try:
        row = db.execute(text("SELECT id, status, invoice_number, total_amount, seller_name FROM invoice_extraction "
                              "WHERE id = :i"), {"i": result.get("id")}).first()
    finally:
        db.close()
    return (f"识别记录 #{row[0]}（状态：{row[1]}，发票号 {row[2]}、价税合计 {row[3]}、销售方 {row[4]}；"
            "金额、税额没识别出来，需要员工逐项核对后才能用于报销）")


def _workdays_back(count):
    days, day = [], TODAY - timedelta(days=1)
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day -= timedelta(days=1)
    return sorted(days)


def seed_attendance(api, t, users):
    """两周打卡（每日汇总格式，和钉钉“打卡时间”报表一样）：大部分正常，故意放几条迟到、缺卡、整天没打卡。"""
    days = _workdays_back(10)
    rows = ["姓名,日期,上班打卡时间,下班打卡时间"]
    for name in ("demo_emp", "demo_newbie", "demo_head", "demo_hr", "demo_hr2", "demo_owner"):
        for i, day in enumerate(days):
            punch_in, punch_out = "08:52", "18:10"
            if name == "demo_emp" and i in (2, 7):
                punch_in = "09:38"                                    # 迟到
            if name == "demo_emp" and i == 5:
                punch_out = ""                                        # 下班缺卡
            if name == "demo_newbie" and i in (3, len(days) - 4):
                continue                                              # 整天没打卡（其中一天有已批准的病假）
            rows.append(f"{name},{day.isoformat()},{punch_in},{punch_out}")
    csv_bytes = ("\n".join(rows) + "\n").encode("utf-8-sig")
    notes = []
    result = api.call("post", "/enterprise/attendance/import", "demo_hr", data={"team_id": str(t["hr"]), "aliases": "{}"},
                      files={"file": ("打卡记录.csv", csv_bytes, "text/csv")})
    analysis = api.call("post", "/enterprise/attendance/analyze", "demo_hr",
                        {"team_id": t["hr"], "start": days[0].isoformat(), "end": days[-1].isoformat()})
    notes.append(f"导入：匹配 {result.get('matched_people')} 人、新打卡 {result.get('new_punches')} 条；"
                 f"分析：{ {k: v for k, v in (analysis or {}).items() if isinstance(v, int)} }")
    mine = api.call("get", f"/enterprise/attendance/anomalies?team_id={t['sales']}&view=mine", "demo_emp")
    items = mine if isinstance(mine, list) else (mine or {}).get("items", [])
    pending = [a for a in items if a.get("status") == "open"]
    if pending:
        api.call("post", f"/enterprise/attendance/anomalies/{pending[0]['id']}/explain", "demo_emp",
                 {"team_id": t["sales"], "text": "早上去客户现场拜访，忘记打卡"})
    return "；".join(notes) + f"；demo_emp 的异常 {len(items)} 条" + ("（已说明 1 条，等负责人认定）" if pending else "")


async def seed_rollups():
    from service.reminders.rules import RULES
    notes = []
    for rule in RULES:
        async with AsyncSessionLocal() as db:           # 每条规则用自己的会话：一条出错不影响下一条
            try:
                created, _closed = await rule.run(db)
                if created:
                    notes.append(f"{rule.label} {created}")
            except Exception as exc:  # noqa: BLE001
                notes.append(f"{rule.label} 失败（{type(exc).__name__}: {str(exc)[:60]}）")
    return "，".join(notes) or "没有新提醒"


# ------------------------------------------------------------------ 规模数据：人更多、时间跨两个月

# 每个部门补到 1 位演示负责人 + 3～7 名员工（登录名都以 demo_ 开头，--purge 一起清理）
SCALE_STAFF = {
    "demo_proc_head": ("procurement", "admin"), "demo_buy01": ("procurement", "member"), "demo_buy02": ("procurement", "member"),
    "demo_buy03": ("procurement", "member"),
    "demo_hr_head": ("hr", "admin"), "demo_hr01": ("hr", "member"), "demo_hr02": ("hr", "member"),
    "demo_fin01": ("finance", "member"), "demo_fin02": ("finance", "member"),
    "demo_it_head": ("it", "admin"), "demo_it01": ("it", "member"), "demo_it02": ("it", "member"),
    "demo_sales01": ("sales", "member"), "demo_sales02": ("sales", "member"), "demo_sales03": ("sales", "member"),
    "demo_sales04": ("sales", "member"), "demo_sales05": ("sales", "member"), "demo_sales06": ("sales", "member"),
}
HEADS = {"sales": "demo_head", "procurement": "demo_proc_head", "hr": "demo_hr_head", "finance": "demo_fin2", "it": "demo_it_head"}
MORE_CUSTOMERS = (("北辰制造", "制造"), ("星河医疗器械", "医疗"), ("青松物业", "物业"), ("锦程旅行社", "旅游"), ("东方建材", "建材"),
                  ("和光传媒", "传媒"), ("森林生鲜", "零售"), ("天行汽车服务", "汽车"), ("博远咨询", "咨询"), ("银河金融科技", "金融"),
                  ("长风能源", "能源"), ("知行教育科技", "教育"), ("海纳电商", "电商"), ("万家连锁药房", "医药"), ("瑞丰农业", "农业"))
CONTACT_NAMES = ("周总", "吴经理", "郑主任", "冯工", "陈总监", "褚经理", "卫总", "蒋主管", "沈经理", "韩总")


def ensure_scale_accounts(db, org, teams, users):
    from models.user_dao import create_user
    from service.auth_service import hash_password
    from tests.test_enterprise_access import _add_org_member, _add_team_member
    created = 0
    for name, (code, role) in SCALE_STAFF.items():
        uid = db.execute(text("SELECT id FROM `user` WHERE name = :n"), {"n": name}).scalar()
        if uid is None:
            uid = create_user(db, name=name, password=hash_password(PASSWORD), age=30).id
            created += 1
        users[name] = int(uid)
        if db.execute(text("SELECT 1 FROM organization_members WHERE organization_id = :o AND user_id = :u"), {"o": org, "u": uid}).scalar() is None:
            _add_org_member(db, org, uid, "member")
        if db.execute(text("SELECT 1 FROM team_members WHERE user_id = :u AND status = 'active'"), {"u": uid}).scalar() is None:
            db.execute(text("DELETE FROM team_members WHERE user_id = :u"), {"u": uid})
            db.commit()
            _add_team_member(db, teams[code], uid, role)
    return created


def staff_of(code):
    names = [n for n, (_, team, _, _) in ACCOUNTS.items() if team == code] + [n for n, (team, _) in SCALE_STAFF.items() if team == code]
    return [n for n in names if n != HEADS[code] and n != "demo_owner"]


def shift(ent, table, row_id, days, columns, date_columns=()):
    """把一条记录的时间整体往前挪 days 天（让数据分布在过去两个月：趋势、超时、逾期都看得到）。"""
    sets = [f"{c} = DATE_SUB({c}, INTERVAL :d DAY)" for c in (*columns, *date_columns)]
    with ent.begin() as conn:
        conn.execute(text(f"UPDATE {table} SET {', '.join(sets)} WHERE id = :i"), {"d": days, "i": row_id})


def seed_scale(api, ent, teams, users):
    import random
    rng = random.Random(20261011)
    if exists(ent, "SELECT 1 FROM expense_claim WHERE applicant_user_id = :u", u=users["demo_sales01"]):
        return "已有，跳过"
    counts = {"请假": 0, "报销": 0, "工单": 0, "采购": 0, "客户": 0, "商机": 0, "跟进": 0, "沟通记录": 0}

    def safe(fn, *args):
        try:
            return fn(*args)
        except Exception as exc:  # noqa: BLE001 —— 个别单据业务规则不允许（例如预算不够）：跳过这一条
            print(f"    · 跳过一条：{str(exc)[:120]}", flush=True)
            return None

    # ---- 请假：每人一条已处理的历史请假，一半人还有一条待审批
    def leave(name, code):
        head, team = HEADS[code], teams[code]
        start = TODAY + timedelta(days=rng.randint(15, 40))
        days_len = rng.choice((0, 0, 1))
        kind = rng.choice(("annual", "annual", "sick", "personal"))
        row = api.call("post", "/enterprise/oa/leave/mine", name, {"team_id": team, "leave_type_code": kind, "start_date": start.isoformat(),
                                                                  "end_date": (start + timedelta(days=days_len)).isoformat(),
                                                                  "reason": rng.choice(("家里有事", "看病", "孩子开家长会", "回老家办证", "休息调整"))})
        api.call("post", f"/enterprise/oa/leave/{row['id']}/submit", name)
        action = "reject" if rng.random() < 0.15 else "approve"
        api.call("post", f"/enterprise/oa/leave/{row['id']}/decide", head, {"team_id": team, "action": action,
                                                                            "note": "同意" if action == "approve" else "这周项目上线，换个时间"})
        back = rng.randint(20, 58) + (start - TODAY).days
        shift(ent, "leave_request", row["id"], back, ("created_at", "submitted_at", "decided_at"), ("start_date", "end_date"))
        counts["请假"] += 1
        if rng.random() < 0.5:
            start = TODAY + timedelta(days=rng.randint(3, 20))
            row = api.call("post", "/enterprise/oa/leave/mine", name, {"team_id": team, "leave_type_code": "annual", "start_date": start.isoformat(),
                                                                      "end_date": start.isoformat(), "reason": "调休"})
            api.call("post", f"/enterprise/oa/leave/{row['id']}/submit", name)
            shift(ent, "leave_request", row["id"], rng.randint(1, 4), ("created_at", "submitted_at"))
            counts["请假"] += 1

    # ---- 报销：每人两张，已批准 / 驳回 / 待审批 / 草稿都有，部分缺发票号
    expense_items = (("TRAVEL", "出差高铁票", 300, 900), ("TRAVEL", "出差住宿", 350, 1200), ("MEAL", "客户午餐", 120, 600),
                     ("TRANSPORT", "打车费", 25, 180), ("OFFICE_SUPPLY", "办公用品", 40, 400), ("OTHER", "快递费", 15, 90))

    def expense(name, code):
        head, team = HEADS[code], teams[code]
        for _ in range(2):
            lines = []
            for category, desc, low, high in rng.sample(expense_items, rng.randint(1, 3)):
                line = {"category": category, "amount": rng.randint(low, high), "description": desc}
                if rng.random() < 0.8:
                    line["invoice_no"] = f"DEMO-{rng.randint(10000000, 99999999)}"
                lines.append(line)
            row = api.call("post", "/enterprise/finance/mine", name, {"team_id": team, "lines": lines})
            fate = rng.random()
            back = rng.randint(1, 55)
            if fate < 0.1:
                shift(ent, "expense_claim", row["id"], back, ("created_at",))
            else:
                api.call("post", f"/enterprise/finance/{row['id']}/submit", name)
                if fate < 0.4:
                    back = rng.randint(1, 5)                       # 待审批：最近几天提交的，有的已超过 24 小时
                else:
                    action = "reject" if fate > 0.9 else "approve"
                    api.call("post", f"/enterprise/finance/{row['id']}/decide", head,
                             {"team_id": team, "action": action, "note": "同意" if action == "approve" else "请补充发票"})
                shift(ent, "expense_claim", row["id"], back, ("created_at", "submitted_at", "decided_at"))
            counts["报销"] += 1

    # ---- IT 工单：各种状态都有，旧的未接单工单会超过 SLA
    problems = (("INCIDENT", "HIGH", "会议室投影连不上", "下午有客户会议，投影一直无信号"),
                ("INCIDENT", "NORMAL", "邮箱收不到外部邮件", "从昨天下午开始收不到客户的邮件"),
                ("INCIDENT", "URGENT", "ERP 登录报错", "登录 ERP 提示账号锁定，影响月底结账"),
                ("INCIDENT", "LOW", "电脑开机很慢", "开机要五分钟，想清理一下"),
                ("ACCOUNT", "NORMAL", "开通 CRM 账号", "新同事需要 CRM 账号"),
                ("PERMISSION", "NORMAL", "申请共享盘权限", "需要访问财务共享盘的报销模板目录"),
                ("DEVICE", "NORMAL", "申请第二块显示器", "做报表需要双屏"),
                ("INCIDENT", "NORMAL", "打印机卡纸", "二楼打印机经常卡纸"))
    it_staff = ["demo_it", "demo_it2", "demo_it01", "demo_it02"]

    def ticket(name, code):
        category, priority, title, desc = rng.choice(problems)
        row = api.call("post", "/enterprise/it/tickets", name, {"team_id": teams[code], "category": category, "priority": priority,
                                                                 "title": title, "description": desc})
        back = rng.randint(0, 40)
        if category != "INCIDENT":
            if rng.random() < 0.6:
                api.call("post", f"/enterprise/it/tickets/{row['id']}/decide", HEADS[code], {"team_id": teams[code], "action": "approve", "note": "同意"})
            else:
                back = rng.randint(1, 4)                           # 等负责人批准
        fate = rng.random()
        if category == "INCIDENT" or back > 4:
            if fate < 0.55:
                who = rng.choice(it_staff)
                api.call("post", f"/enterprise/it/desk/tickets/{row['id']}/assign", who, {"team_id": teams["it"], "take": True})
                api.call("post", f"/enterprise/it/desk/tickets/{row['id']}/resolve", who,
                         {"team_id": teams["it"], "resolution": rng.choice(("重启并更新了驱动", "重置密码并解锁账号", "更换了网线", "清理了缓存和启动项"))})
                if rng.random() < 0.6:
                    api.call("post", f"/enterprise/it/tickets/{row['id']}/confirm", name, {})
            elif fate < 0.8:
                api.call("post", f"/enterprise/it/desk/tickets/{row['id']}/assign", rng.choice(it_staff), {"team_id": teams["it"], "take": True})
        shift(ent, "it_ticket", row["id"], back, ("created_at", "updated_at", "first_response_at", "sla_due_at", "resolved_at", "closed_at"))
        counts["工单"] += 1

    # ---- 采购：采购部员工的申请，已批准 / 驳回 / 等负责人审批 / 草稿
    def purchase(name):
        skus = ("DEMO-PAPER", "DEMO-TONER", "DEMO-PEN", "DEMO-MOUSE", "DEMO-HEADSET", "DEMO-WATER", "DEMO-MONITOR", "DEMO-CHAIR")
        for _ in range(3):
            lines = [{"sku": sku, "quantity": rng.randint(2, 20)} for sku in rng.sample(skus, rng.randint(1, 3))]
            row = api.call("post", "/enterprise/procurement/mine", name, {"team_id": teams["procurement"], "lines": lines})
            fate = rng.random()
            back = rng.randint(1, 50)
            if fate >= 0.1:
                api.call("post", f"/enterprise/procurement/{row['id']}/submit", name)
                if fate < 0.45:
                    back = rng.randint(1, 4)                       # 等负责人审批（采购部负责人都能看到）
                else:
                    action = "reject" if fate > 0.85 else "approve"
                    api.call("post", f"/enterprise/procurement/{row['id']}/decide", HEADS["procurement"],
                             {"team_id": teams["procurement"], "action": action, "note": "同意" if action == "approve" else "本季度预算紧张，下季度再买"})
            shift(ent, "purchase_request", row["id"], back, ("created_at", "submitted_at", "decided_at"))
            counts["采购"] += 1

    for code in ("sales", "procurement", "hr", "finance", "it"):
        for name in staff_of(code):
            safe(leave, name, code)
            safe(expense, name, code)
            if code != "it":
                safe(ticket, name, code)
                if rng.random() < 0.5:
                    safe(ticket, name, code)
    for name in staff_of("procurement"):
        safe(purchase, name)

    # ---- CRM：再加 15 个客户，分给销售员工；商机、跟进、沟通记录分布在两个月里
    sales_staff = staff_of("sales")
    with ent.begin() as conn:
        for i, (cname, industry) in enumerate(MORE_CUSTOMERS):
            owner = users[sales_staff[i % len(sales_staff)]]
            conn.execute(text("INSERT INTO customer (name, industry, owner_user_id, team_id, created_at) "
                              "SELECT :n, :i, :o, :t, DATE_SUB(NOW(), INTERVAL :d DAY) FROM DUAL "
                              "WHERE NOT EXISTS (SELECT 1 FROM customer WHERE name = :n AND team_id = :t)"),
                         {"n": cname, "i": industry, "o": owner, "t": teams["sales"], "d": rng.randint(30, 90)})
            cid = conn.execute(text("SELECT id FROM customer WHERE name = :n AND team_id = :t"), {"n": cname, "t": teams["sales"]}).scalar()
            contact = CONTACT_NAMES[i % len(CONTACT_NAMES)]
            conn.execute(text("INSERT INTO contact (customer_id, name, title, phone, email) SELECT :c, :n, '采购负责人', :p, :e FROM DUAL "
                              "WHERE NOT EXISTS (SELECT 1 FROM contact WHERE customer_id = :c)"),
                         {"c": cid, "n": contact, "p": f"139{rng.randint(10000000, 99999999)}", "e": f"contact{i}@customer{i}.example.com"})
            counts["客户"] += 1
        owners = dict(conn.execute(text("SELECT id, owner_user_id FROM customer WHERE team_id = :t"), {"t": teams["sales"]}).all())
    names_by_id = {uid: name for name, uid in users.items()}
    stages = ("LEAD", "LEAD", "QUALIFIED", "QUALIFIED", "PROPOSAL", "PROPOSAL", "NEGOTIATION", "WON", "LOST")
    for cid, owner_id in owners.items():
        owner = names_by_id.get(owner_id)
        if owner is None:
            continue                                               # 不是演示账号的客户（你自己建的）不动
        if not exists(ent, "SELECT 1 FROM opportunity WHERE customer_id = :c", c=cid):
            stage = rng.choice(stages)
            if safe(api.call, "post", f"/enterprise/crm/customers/{cid}/opportunities", owner,
                    {"team_id": teams["sales"], "stage": stage, "amount": rng.randrange(20000, 600000, 1000),
                     "expected_close_date": (TODAY + timedelta(days=rng.randint(-20, 90))).isoformat(),
                     "next_step": rng.choice(("约下次拜访", "发正式报价", "等客户内部评审", "安排产品演示", "准备合同"))}):
                counts["商机"] += 1
        for _ in range(rng.randint(2, 4)):
            followup = safe(api.call, "post", f"/enterprise/crm/customers/{cid}/followups", owner,
                            {"team_id": teams["sales"], "content": rng.choice((
                                "电话沟通了需求，对方关注价格和交付周期", "上门拜访，见了采购和使用部门", "发送了报价单，等待反馈",
                                "客户提出要试用两周", "对方预算还在审批中", "约好下周做产品演示"))})
            if followup:
                if rng.random() < 0.8:
                    safe(api.call, "post", f"/enterprise/crm/followups/{followup['id']}/confirm", owner)
                shift(ent, "follow_up", followup["id"], rng.randint(1, 60), ("created_at", "confirmed_at"))
                counts["跟进"] += 1
        if rng.random() < 0.7:
            when = datetime.now(BJ) - timedelta(days=rng.randint(1, 50), hours=rng.randint(0, 8))
            kind = rng.choice(("call", "meeting", "quote"))
            if safe(api.call, "post", "/enterprise/crm-copilot/activities", owner,
                    {"team_id": teams["sales"], "customer_id": cid, "activity_type": kind,
                     "title": {"call": "电话纪要", "meeting": "拜访会议", "quote": "报价"}[kind],
                     "content": rng.choice(("对方确认了需求范围，下一步出方案", "客户对价格有异议，希望再降 5%", "讨论了交付排期和验收标准")),
                     "occurred_at": when.strftime("%Y-%m-%dT%H:%M:%S")}):
                counts["沟通记录"] += 1
    return "、".join(f"{k} {v}" for k, v in counts.items())


def seed_scale_responsibility(api, ent, teams, users, real_head=None):
    """采购部和销售部各一份进行中的责任计划：有待接受、执行中、受阻、待验收、已逾期。
    real_head：采购部里真实的负责人账号（例如你自己的账号）——分给他两项，出现在他的“待我接受”里，由他自己去接受。
    可以接着上次没做完的继续：计划已经建好就从库里读出事项，只补还停在“待接受”的演示员工的动作。"""
    day = lambda n: (TODAY + timedelta(days=n)).isoformat()  # noqa: E731
    notes = []

    def find(title, head):
        with ent.connect() as conn:
            return conn.execute(text("SELECT id FROM responsibility_plan WHERE created_by = :u AND title = :t ORDER BY id DESC LIMIT 1"),
                                {"u": users[head], "t": title}).scalar()

    def tasks_of(plan_id):
        with ent.connect() as conn:
            return conn.execute(text("SELECT id, responsible_user_id, status FROM responsibility_task WHERE plan_id = :p ORDER BY seq, id"),
                                {"p": plan_id}).all()

    def make(code, head, title, items):
        found = find(title, head)
        if found:
            return found, False
        source = "；".join(f"{label}负责{task_title}，{due}天内提交{deliverable}" for label, _, task_title, due, deliverable in items) + "。"
        tasks = [{"title": task_title, "responsible_user_id": uid, "reviewer_user_id": users[head], "due_date": day(due),
                  "deliverable": deliverable, "acceptance_criteria": f"{deliverable}内容完整、数据准确",
                  "priority": "HIGH" if due <= 3 else "NORMAL", "evidence": f"{label}负责{task_title}，{due}天内提交{deliverable}"}
                 for label, uid, task_title, due, deliverable in items]
        plan = api.call("post", "/enterprise/responsibility/plans", head, {"team_id": teams[code], "title": title, "source_type": "MEETING",
                                                                            "source_text": source, "summary": f"{title}：{len(tasks)} 项事项已分派",
                                                                            "tasks": tasks})
        api.call("post", f"/enterprise/responsibility/plans/{plan['id']}/publish", head, {"team_id": teams[code], "note": "按会议决定分派"})
        return plan["id"], True

    def act(code, plan_id, actions):
        """按顺序给演示员工的事项做动作（只动还停在“待接受”的、演示账号的事项；真实账号的留给本人）。"""
        names = {uid: name for name, uid in users.items()}
        demo_tasks = [(tid, names[uid]) for tid, uid, status in tasks_of(plan_id) if uid in names and status == "PENDING_ACCEPT"]
        for (tid, who), action in zip(demo_tasks, actions):
            if action == "none":
                continue
            body = {"team_id": teams[code]}
            api.call("post", f"/enterprise/responsibility/tasks/{tid}/accept", who, body)
            if action == "progress":
                api.call("post", f"/enterprise/responsibility/tasks/{tid}/progress", who, {**body, "note": "已完成一半", "percent": 50})
            elif action == "block":
                api.call("post", f"/enterprise/responsibility/tasks/{tid}/block", who, {**body, "reason": "等对方提供数据"})
            elif action == "submit":
                api.call("post", f"/enterprise/responsibility/tasks/{tid}/submit", who,
                         {**body, "summary": "已完成，见附件", "link": "https://example.com/deliverable"})

    buyers = [n for n in staff_of("procurement") if n.startswith("demo_")]
    items = [(b, users[b], t, d, deliv) for b, (t, d, deliv) in zip(buyers, (
        ("整理三家打印耗材供应商报价对比", 2, "报价对比表"), ("统计上季度办公用品消耗", 5, "消耗统计表"),
        ("梳理采购审批超时的单据", 7, "超时单据清单"), ("更新合格供应商名录", 10, "供应商名录")))]
    if real_head:
        items += [("采购负责人", real_head, "审核 Q4 采购预算分配", 6, "预算分配方案"),
                  ("采购负责人", real_head, "确定年度框架协议谈判策略", 12, "谈判策略说明")]
    plan_id, created = make("procurement", "demo_proc_head", "Q4 供应商评估与采购成本优化", items)
    act("procurement", plan_id, ("submit", "progress", "block", "accept"))
    with ent.begin() as conn:                                     # 受阻的那项已经过了截止日期（采购部看板上的“逾期”）
        conn.execute(text("UPDATE responsibility_task SET due_date = DATE_SUB(CURDATE(), INTERVAL 1 DAY) "
                          "WHERE plan_id = :p AND status = 'BLOCKED' ORDER BY id LIMIT 1"), {"p": plan_id})
    notes.append(f"采购部计划 #{plan_id}（{len(tasks_of(plan_id))} 项）" + ("" if created else "，补齐了事项进度"))

    sellers = staff_of("sales")[:6]
    items = [(s, users[s], t, d, deliv) for s, (t, d, deliv) in zip(sellers, (
        ("回访 10 月签约客户", 3, "回访记录"), ("整理华星科技二期需求", 4, "需求说明"), ("准备启明教育演示环境", 2, "演示账号"),
        ("更新重点客户商机金额", 5, "商机清单"), ("完成蓝海餐饮首次拜访", 6, "拜访纪要"), ("汇总本月丢单原因", 8, "丢单分析")))]
    plan_id, created = make("sales", "demo_head", "10 月销售冲刺分工", items)
    act("sales", plan_id, ("accept", "progress", "block", "submit", "none", "accept"))
    # 两项挪到已经过了截止日期（部门看板上的“逾期”）
    with ent.begin() as conn:
        conn.execute(text("UPDATE responsibility_task SET due_date = DATE_SUB(CURDATE(), INTERVAL 2 DAY) "
                          "WHERE plan_id = :p AND status IN ('IN_PROGRESS', 'BLOCKED') ORDER BY id LIMIT 2"), {"p": plan_id})
    notes.append(f"销售部计划 #{plan_id}（{len(tasks_of(plan_id))} 项，2 项已逾期）")
    return "；".join(notes) + ("；分给你的 2 项在你的“待我接受”里" if real_head else "")


def seed_scale_hr(api, ent, teams, users):
    if exists(ent, "SELECT 1 FROM hr_case WHERE employee_user_id = :u", u=users["demo_sales06"]):
        return "已有，跳过"
    probation = api.call("post", "/enterprise/hr/cases", "demo_hr", {"team_id": teams["hr"], "case_type": "PROBATION",
                                                                       "employee_user_id": users["demo_sales02"], "employee_team_id": teams["sales"],
                                                                       "effective_date": (TODAY + timedelta(days=12)).isoformat(), "position": "销售专员"})
    leaving = api.call("post", "/enterprise/hr/cases", "demo_hr", {"team_id": teams["hr"], "case_type": "OFFBOARDING",
                                                                     "employee_user_id": users["demo_sales06"], "employee_team_id": teams["sales"],
                                                                     "effective_date": (TODAY + timedelta(days=20)).isoformat(), "reason": "个人发展"})
    api.call("post", f"/enterprise/hr/cases/{leaving['id']}/approve", "demo_head", {"team_id": teams["sales"]})
    return f"转正 #{probation['id']}（待负责人批准）、离职 #{leaving['id']}（办理中）"


def seed_scale_attendance(api, teams, users):
    days = _workdays_back(20)
    import random
    rng = random.Random(1011)
    rows = ["姓名,日期,上班打卡时间,下班打卡时间"]
    for name in users:
        for day in days:
            fate = rng.random()
            if fate < 0.03:
                continue                                           # 整天没打卡
            punch_in = f"09:{rng.randint(10, 59):02d}" if fate < 0.10 else f"08:{rng.randint(30, 59):02d}"
            punch_out = "" if 0.10 <= fate < 0.13 else f"18:{rng.randint(0, 50):02d}"
            rows.append(f"{name},{day.isoformat()},{punch_in},{punch_out}")
    data = ("\n".join(rows) + "\n").encode("utf-8-sig")
    result = api.call("post", "/enterprise/attendance/import", "demo_hr", data={"team_id": str(teams["hr"]), "aliases": "{}"},
                      files={"file": ("四周打卡.csv", data, "text/csv")})
    analysis = api.call("post", "/enterprise/attendance/analyze", "demo_hr",
                        {"team_id": teams["hr"], "start": days[0].isoformat(), "end": days[-1].isoformat()})
    return f"{len(days)} 个工作日、{result.get('matched_people')} 人、新打卡 {result.get('new_punches')} 条；异常共 {analysis.get('total_anomalies')} 条"


# ------------------------------------------------------------------ 清理

def purge(db, ent):
    users = [r[0] for r in db.execute(text("SELECT id FROM `user` WHERE name LIKE 'demo\\_%'")).all()]
    owner = db.execute(text("SELECT id FROM `user` WHERE name = 'demo_owner'")).scalar()
    ids = ",".join(str(int(u)) for u in users) or "0"
    it_team = db.execute(text("SELECT id FROM teams WHERE name = :n AND owner_user_id = :o"), {"n": IT_TEAM_NAME, "o": owner or 0}).scalar()
    with ent.begin() as conn:
        customers = f"SELECT id FROM customer WHERE owner_user_id IN ({ids})"
        claims = f"SELECT id FROM expense_claim WHERE applicant_user_id IN ({ids})"
        tickets = f"SELECT id FROM it_ticket WHERE requester_user_id IN ({ids})"
        purchases = f"SELECT id FROM purchase_request WHERE requester_user_id IN ({ids})"
        plans = f"SELECT id FROM responsibility_plan WHERE created_by IN ({ids})"
        for sql in (
            f"DELETE FROM voucher_entry WHERE voucher_id IN (SELECT id FROM voucher WHERE expense_claim_id IN ({claims}))",
            f"DELETE FROM voucher WHERE expense_claim_id IN ({claims})",
            f"DELETE FROM expense_line WHERE expense_claim_id IN ({claims})",
            f"DELETE FROM expense_claim WHERE id IN ({claims})",
            f"DELETE FROM purchase_order WHERE purchase_request_id IN ({purchases})",
            f"DELETE FROM purchase_request_line WHERE purchase_request_id IN ({purchases})",
            f"DELETE FROM purchase_request WHERE id IN ({purchases})",
            f"DELETE FROM it_ticket_comment WHERE ticket_id IN ({tickets})",
            f"DELETE FROM it_device_event WHERE ticket_id IN ({tickets}) OR device_id IN (SELECT id FROM it_device WHERE asset_no LIKE 'DEMO-%')",
            f"DELETE FROM it_ticket WHERE id IN ({tickets})",
            "DELETE FROM it_device WHERE asset_no LIKE 'DEMO-%'",
            f"DELETE FROM hr_case_task WHERE case_id IN (SELECT id FROM hr_case WHERE employee_user_id IN ({ids}) OR initiator_user_id IN ({ids}))",
            f"DELETE FROM hr_case WHERE employee_user_id IN ({ids}) OR initiator_user_id IN ({ids})",
            f"DELETE FROM responsibility_event WHERE plan_id IN ({plans})",
            f"DELETE FROM responsibility_deliverable WHERE task_id IN (SELECT id FROM responsibility_task WHERE plan_id IN ({plans}))",
            f"DELETE FROM responsibility_dependency WHERE task_id IN (SELECT id FROM responsibility_task WHERE plan_id IN ({plans}))",
            f"DELETE FROM responsibility_collaborator WHERE task_id IN (SELECT id FROM responsibility_task WHERE plan_id IN ({plans}))",
            f"DELETE FROM responsibility_task WHERE plan_id IN ({plans})",
            f"DELETE FROM responsibility_plan WHERE id IN ({plans})",
            f"DELETE FROM leave_request WHERE applicant_user_id IN ({ids})",
            f"DELETE FROM leave_balance WHERE user_id IN ({ids})",
            f"DELETE FROM follow_up WHERE customer_id IN ({customers}) OR author_user_id IN ({ids})",
            f"DELETE FROM opportunity WHERE customer_id IN ({customers}) OR owner_user_id IN ({ids})",
            f"DELETE FROM contact WHERE customer_id IN ({customers})",
            f"DELETE FROM customer WHERE id IN (SELECT id FROM ({customers}) x)",
            "DELETE FROM product WHERE sku LIKE 'DEMO-%' AND id NOT IN (SELECT product_id FROM purchase_request_line)",
            f"DELETE FROM audit_event WHERE user_id IN ({ids})",
        ):
            conn.execute(text(sql))
    if users:
        rc._purge_users("id IN (" + ids + ")")
    if it_team:
        db.execute(text("UPDATE agent SET lifecycle_status = 'retired' WHERE team_id = :t"), {"t": it_team})
        db.execute(text("DELETE FROM team_members WHERE team_id = :t"), {"t": it_team})
        db.execute(text("UPDATE teams SET status = 'disabled' WHERE id = :t"), {"t": it_team})
        db.commit()
    print(f"已清理：{len(users)} 个演示账号及其业务数据、DEMO- 开头的产品和设备" + ("、脚本建的 IT 部门（停用）" if it_team else ""))


# ------------------------------------------------------------------ 入口

def _real_procurement_head(db, teams):
    """采购部里真实的负责人（不是 demo_ 账号）里最近登录的那位：给他分两项责任，用自己的账号登录也能看到数据。"""
    return db.execute(text(
        "SELECT u.id FROM team_members m JOIN `user` u ON u.id = m.user_id JOIN enterprise_role r ON r.id = m.role_id "
        "WHERE m.team_id = :t AND m.status = 'active' AND r.code = 'admin' AND u.name NOT LIKE 'demo%' "
        "ORDER BY u.last_login_at IS NULL, u.last_login_at DESC LIMIT 1"), {"t": teams["procurement"]}).scalar()


def main() -> int:
    db = SessionLocal()
    ent = enterprise_engine()
    try:
        if "--purge" in sys.argv:
            purge(db, ent)
            return 0
        org = enterprise_id(db)
        print("1/4 部门、账号、部门助手…")
        users, created = ensure_accounts(db, org)
        teams = ensure_teams(db, org, users)
        placed = ensure_memberships(db, teams, users)
        more = ensure_scale_accounts(db, org, teams, users)
        print(f"  ✓ 演示账号 {len(users)} 个（新建 {len(created) + more}，放进部门 {len(placed)}）；部门：" +
              "、".join(f"{code}={tid}" for code, tid in teams.items()))
        operator = db.execute(text("SELECT owner_user_id FROM organizations WHERE id = :o"), {"o": org}).scalar() or users["demo_owner"]
        step("部门助手（缺的建好并发布，旧的一键修复）")(lambda: run_async(ensure_agents(teams, operator)))()
        print("2/4 业务系统基础数据…")
        step("预算、产品库存、客户与联系人、假期余额")(seed_business_base)(ent, teams, users)
        print("3/4 经真实接口产生业务（需要 Java 业务中心在运行）…")
        api = Api(users)
        step("请假与报销")(seed_leave_and_finance)(api, ent, teams, users)
        step("采购申请")(seed_procurement)(api, ent, teams, users)
        step("IT 设备与工单")(seed_it)(api, ent, teams, users)
        step("人事入职办理")(seed_hr)(api, ent, teams, users)
        step("责任协同计划")(seed_responsibility)(api, ent, teams, users)
        step("CRM 商机与跟进")(seed_crm)(api, ent, teams, users)
        step("CRM 沟通收集（邮件、会议、聊天）")(seed_crm_activities)(api, teams, users)
        step("发票识别（PDF 电子发票，低置信度字段待核对）")(seed_invoice)(api, teams, users)
        step("考勤打卡导入与异常分析")(seed_attendance)(api, teams, users)
        print("    规模数据：每个部门 3～7 人、两个月的历史…")
        step("请假、报销、IT 工单、采购、CRM（分布在过去两个月）")(seed_scale)(api, ent, teams, users)
        real_head = _real_procurement_head(db, teams)
        step("责任协同：采购部、销售部进行中的计划")(seed_scale_responsibility)(api, ent, teams, users, real_head)
        step("人事：转正与离职办理")(seed_scale_hr)(api, ent, teams, users)
        step("考勤：四周打卡与异常")(seed_scale_attendance)(api, teams, users)
        print("4/4 风险扫描、提醒、提效统计…")
        step("CRM 风险与下一步建议")(lambda: run_async(seed_crm_insights()))()
        step("提醒规则（待办、通知）与提效统计")(lambda: run_async(seed_rollups()))()
    finally:
        db.close()
    failed = [r for r in RESULTS if r[1] == "✗"]
    print("-" * 60)
    print(f"完成：{len(RESULTS) - len(failed)} 项成功，{len(failed)} 项失败" + ("（原因见上面的 ✗）" if failed else ""))
    print(f"演示账号密码都是 {PASSWORD}：负责人 demo_head（销售）、demo_proc_head（采购）、demo_hr_head（人事）、demo_fin2（财务）、"
          "demo_it_head（IT）、demo_owner（企业管理员）；员工 demo_emp、demo_sales01～06、demo_buyer、demo_buy01～03、"
          "demo_hr / demo_hr01～02、demo_fin / demo_fin01～02、demo_it / demo_it01～02")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
