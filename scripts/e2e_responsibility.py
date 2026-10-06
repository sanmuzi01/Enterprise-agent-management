"""部门责任执行的真实端到端验收：FastAPI（含权限层）+ MySQL + Java 业务服务全部是真的，AI 整理用离线演示模型（规则抽取）。

故事线（对应产品演示）：
  粘贴一份会议纪要 → AI 提取 5 项责任 → 发现 2 项没有责任人、1 项没有验收标准 → 负责人补充并发布 →
  员工接受（一人提出异议，负责人调整后重新接受）→ 报告阻塞 → 负责人协调 → 提交成果 → 验收人退回一次 → 重新提交 → 验收通过 →
  部门周报显示按时闭环；同时验证提醒、Agent 工具，以及越权（指派人替员工接受、主责人自验、离开部门、其他企业）全部被拦住。

前置：MySQL 已启动；Java 业务服务在 ENTERPRISE_HUB_BASE_URL（默认 127.0.0.1:8090）运行（jar 必须是含 V9 的最新构建）。
用法：.venv\\Scripts\\python.exe scripts\\e2e_responsibility.py [--keep | --purge]
  --keep   验收通过后保留数据并打印登录账号（kp_rs_ 前缀），供浏览器里继续验证；--purge 清理保留的数据。
"""
import os
import pathlib
import sys
import uuid
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("OFFLINE_DEMO_MODEL", "1")   # 必须在导入 service 之前：用离线演示模型代替真实大模型

from tests import _route_client as rc  # noqa: E402  先导入：设置非生产环境变量

from dotenv import load_dotenv  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

load_dotenv(ROOT / ".env")

from models.init_db import SessionLocal  # noqa: E402
from tests._async_helpers import run_async  # noqa: E402
from tests.test_enterprise_access import _add_org_member, _add_team_member, _create_org, _create_team  # noqa: E402

SUFFIX = uuid.uuid4().hex[:6].upper()
KEEP = "--keep" in sys.argv
BEIJING = timezone(timedelta(hours=8))
TODAY = datetime.now(BEIJING).date()
MODEL = "demo-offline"


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
        got = response.status_code
        self.ok(got in expected if isinstance(expected, tuple) else got == expected,
                f"{message}（{got}）" if (got in expected if isinstance(expected, tuple) else got == expected)
                else f"{message}：期望 {expected}，实际 {got} {response.text[:300]}")


def clean_business_data(ent, team_ids, user_ids):
    ids = ",".join(str(t) for t in team_ids) or "0"
    users = ",".join(str(u) for u in user_ids) or "0"
    with ent.begin() as conn:
        plans = f"SELECT id FROM responsibility_plan WHERE team_id IN ({ids})"
        tasks = f"SELECT id FROM responsibility_task WHERE team_id IN ({ids})"
        conn.execute(text(f"DELETE FROM responsibility_event WHERE plan_id IN ({plans})"))
        conn.execute(text(f"DELETE FROM responsibility_deliverable WHERE task_id IN ({tasks})"))
        conn.execute(text(f"DELETE FROM responsibility_dependency WHERE task_id IN ({tasks})"))
        conn.execute(text(f"DELETE FROM responsibility_collaborator WHERE task_id IN ({tasks})"))
        conn.execute(text(f"DELETE FROM responsibility_task WHERE team_id IN ({ids})"))
        conn.execute(text(f"DELETE FROM responsibility_plan WHERE team_id IN ({ids})"))
        conn.execute(text(f"DELETE FROM audit_event WHERE user_id IN ({users})"))


def purge_kept():
    ent, db = enterprise_engine(), SessionLocal()
    try:
        orgs = [r[0] for r in db.execute(text("SELECT id FROM organizations WHERE name LIKE 'e2e-rs%'")).all()]
        teams = [r[0] for r in db.execute(text("SELECT t.id FROM teams t JOIN organizations o ON o.id=t.organization_id WHERE o.name LIKE 'e2e-rs%'")).all()]
        users = [r[0] for r in db.execute(text("SELECT id FROM `user` WHERE name LIKE 'kp_rs_%'")).all()]
        clean_business_data(ent, teams, users)
        if users:
            ids = ",".join(str(u) for u in users)
            db.execute(text(f"DELETE FROM automation_work WHERE user_id IN ({ids})"))
            db.execute(text(f"DELETE FROM work_item WHERE user_id IN ({ids})"))
            db.execute(text(f"DELETE FROM notification WHERE user_id IN ({ids})"))
            db.commit()
            rc._purge_users("id IN (" + ids + ")")
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


def run_db(fn):
    from models.async_db import AsyncSessionLocal

    async def go():
        async with AsyncSessionLocal() as session:
            return await fn(session)
    return run_async(go())


def main():
    from service.llm.llm_config_service import save_config
    from service.reminders import responsibility_rules as rr
    from service import work_item_service
    check = Checker()
    ent, db, client = enterprise_engine(), SessionLocal(), rc.make_client()
    owner = rc.create_user("rs-own")
    names = ("head", "zh", "li", "wa", "boss", "out")
    u = {n: rc.create_user(n) for n in names}
    org, other_org = _create_org(db, f"e2e-rs-{SUFFIX}", owner["id"]), _create_org(db, f"e2e-rs2-{SUFFIX}", owner["id"])
    ops = _create_team(db, org, f"e2e-rs-ops-{SUFFIX}", owner["id"])          # 运营部：没有专属业务类型（办公助手标杆）
    mgmt = _create_team(db, org, f"e2e-rs-mgmt-{SUFFIX}", owner["id"])        # 管理层：验收人 boss 在这里
    other_team = _create_team(db, other_org, f"e2e-rs-other-{SUFFIX}", owner["id"])
    team_ids = [ops, mgmt, other_team]
    for n in ("head", "zh", "li", "wa"):
        _add_org_member(db, org, u[n]["id"], "member")
    _add_org_member(db, org, u["boss"]["id"], "admin")
    _add_org_member(db, other_org, u["out"]["id"], "member")
    _add_team_member(db, ops, u["head"]["id"], "admin")
    for n in ("zh", "li", "wa"):
        _add_team_member(db, ops, u[n]["id"], "member")
    _add_team_member(db, mgmt, u["boss"]["id"], "admin")
    _add_team_member(db, mgmt, u["li"]["id"], "member")        # li 同时在管理层：离开运营部后用来验证"换个部门身份也看不到"
    _add_team_member(db, other_team, u["out"]["id"], "member")

    class U:
        def __init__(self, uid):
            self.id = uid
    for n in names:
        save_config(db, U(u[n]["id"]), MODEL, "offline-demo")

    def h(n):
        return u[n]["headers"]

    def uid(n):
        return u[n]["id"]

    def nm(n):
        return u[n]["name"]

    team_of = {"head": ops, "zh": ops, "li": ops, "wa": ops, "boss": mgmt, "out": other_team}

    def rs_get(path, who, team=None, **params):
        q = "&".join([f"team_id={team or team_of[who]}"] + [f"{k}={v}" for k, v in params.items()])
        return client.get(f"/enterprise/responsibility{path}?{q}", headers=h(who))

    def rs_post(path, who, team=None, **body):
        return client.post(f"/enterprise/responsibility{path}", json={"team_id": team or team_of[who], **body}, headers=h(who))

    def act(task, action, who, **body):
        return rs_post(f"/tasks/{task}/{action}", who, **body)

    def status_of(task, who="head"):
        return rs_get(f"/tasks/{task}", who).json()["task"]["status"]

    def due(days):
        return (TODAY + timedelta(days=days)).isoformat()

    team_ids_all = team_ids
    try:
        print("== 1. 粘贴会议纪要，AI（离线规则模型）提取责任 ==")
        meeting = (f"10月12日例会纪要：会议决定新版首页本月发布。"
                   f"{nm('zh')}负责新版首页联调，下周五前提交可部署的前端构建包，验收标准是测试环境回归通过且无阻断问题，由{nm('boss')}验收。"
                   f"{nm('li')}负责整理客户反馈清单，月底前提交反馈汇总表，由{nm('boss')}验收。"
                   f"{nm('wa')}负责更新发布说明文档，下周三前提交最新版文档，验收标准是覆盖全部新功能，由{nm('boss')}验收。"
                   f"供应商对账需要在下周三前完成。"
                   f"新版首页的埋点方案要在下周五前确定。"
                   f"关于是否增加会员页的问题，大家还在讨论，暂不决定。")
        catalog = client.get(f"/enterprise/automation/workflows?team_id={ops}", headers=h("head")).json()
        check.ok(any(w["id"] == "responsibility" and w["available"] for w in catalog["workflows"]), "责任计划整理在任何部门都可用（运营部没有专属业务类型）")
        generated = client.post("/enterprise/automation", headers=h("head"), json={
            "request_key": str(uuid.uuid4()), "team_id": ops, "kind": "responsibility", "model_name": MODEL, "source_text": meeting})
        check.status(generated, 200, "AI 整理会议纪要")
        work = generated.json()
        check.ok(work["status"] == "ready", f"整理成功，等待人工核对（{work['status']} {work.get('error_message')}）")
        tasks = work["proposal"]["tasks"]
        check.ok(len(tasks) == 5, f"提取出 5 项责任（实际 {len(tasks)}）")
        owner_ids = [t["responsible_user_id"] for t in tasks]
        check.ok(owner_ids[:3] == [uid("zh"), uid("li"), uid("wa")], "姓名匹配到本部门有效成员")
        check.ok(owner_ids[3:] == [None, None], "原文没有点名的 2 项责任人留空待补充（不替人选）")
        check.ok(all(t["evidence"] in meeting for t in tasks), "每项责任的依据都是原文逐字片段")
        check.ok(tasks[0]["reviewer_user_id"] == uid("boss") and tasks[0]["acceptance_criteria"], "验收人来自企业管理员，验收标准取自原文")
        check.ok(tasks[1]["acceptance_criteria"] is None, "李四那项原文没有验收标准 → 留空")
        due_zh = tasks[0]["due_date"]
        check.ok(due_zh is not None and due_zh > TODAY.isoformat(), f"“下周五前”被换算成具体日期 {due_zh}")
        check.ok(tasks[1]["due_date"] is not None and tasks[1]["due_date"].endswith(tuple("0123456789")), "“月底前”被换算成具体日期")
        check.ok(any("没有点名责任人" in u_ for u_ in work["proposal"]["unresolved"]) and any("仅讨论" in u_ for u_ in work["proposal"]["unresolved"]),
                 "仅讨论的内容进入“未明确”，没有变成责任事项")
        checks = " ".join(c["text"] for c in work["business_checks"])
        check.ok("共整理出 5 项责任" in checks and "还没有主责员工" in checks and "没有验收标准" in checks, "业务系统核对指出了责任人与验收标准缺口")

        print("== 2. 保存为草稿计划（不通知任何人） ==")
        applied = client.post(f"/enterprise/automation/{work['id']}/apply", headers=h("head"), json={"proposal": work["proposal"]})
        check.status(applied, 200, "保存为责任计划草稿")
        plan_id = applied.json()["business_result"]["id"]
        again = client.post(f"/enterprise/automation/{work['id']}/apply", headers=h("head"), json={"proposal": work["proposal"]})
        check.ok(again.json()["business_result"]["id"] == plan_id, "重复点击保存不会产生第二份计划")
        with ent.begin() as conn:
            plans_n = conn.execute(text("SELECT COUNT(*) FROM responsibility_plan WHERE team_id=:t"), {"t": ops}).scalar()
        check.ok(plans_n == 1, "业务库里只有一份计划")
        plan = rs_get(f"/plans/{plan_id}", "head").json()
        check.ok(plan["status"] == "DRAFT" and not plan["canPublish"] and plan["blockerCount"] > 0, "草稿还有必填缺口，不能发布")
        check.ok(plan["missingAtCreation"] > 0, f"创建时发现 {plan['missingAtCreation']} 处缺失（效率指标：发布前发现的遗漏）")
        check.ok(rs_get("/tasks", "zh", view="mine").json() == [], "草稿阶段员工的“我的责任”里什么都没有（没有被通知）")
        blocked = rs_post(f"/plans/{plan_id}/publish", "head")
        check.status(blocked, 400, "缺口没补全，负责人也不能发布")
        check.ok("主责员工" in blocked.text and "验收标准" in blocked.text, "拒绝原因逐项说明缺什么")
        check.status(rs_post(f"/plans/{plan_id}/publish", "zh"), (403, 404), "被列为主责的员工在草稿阶段看不到计划，更不能正式指派")
        check.status(rs_get(f"/plans/{plan_id}", "wa"), 404, "与计划无关的同部门成员看不到草稿")

        print("== 3. 负责人补全缺口并发布 ==")
        task_ids = [t["id"] for t in plan["tasks"]]

        def edit(index, **over):
            t = plan["tasks"][index]
            body = {"title": t["title"], "responsible_user_id": t["responsibleUserId"], "collaborator_user_ids": t["collaboratorUserIds"],
                    "reviewer_user_id": t["reviewerUserId"], "due_date": t["dueDate"], "deliverable": t["deliverable"],
                    "acceptance_criteria": t["acceptanceCriteria"], "priority": t["priority"], "evidence": t["sourceEvidence"],
                    "depends_on_seq": t["dependsOnSeq"], **over}
            return rs_post(f"/tasks/{task_ids[index]}/edit", "head", task=body)

        check.status(edit(1, acceptance_criteria="汇总表覆盖本月全部客户反馈"), 200, "补上李四那项的验收标准")
        check.status(edit(3, responsible_user_id=uid("wa"), reviewer_user_id=uid("boss"), deliverable="对账差异清单",
                          acceptance_criteria="差异项全部有说明"), 200, "为“供应商对账”选定主责员工并补全")
        check.status(edit(4, responsible_user_id=uid("li"), reviewer_user_id=uid("boss"), deliverable="埋点方案文档",
                          acceptance_criteria="覆盖首页全部关键事件"), 200, "为“埋点方案”选定主责员工并补全")
        check.status(edit(0, responsible_user_id=uid("zh"), reviewer_user_id=uid("zh")), 200, "（草稿允许暂时不合规）主责人=验收人")
        bad = rs_post(f"/plans/{plan_id}/publish", "head")
        check.ok(bad.status_code == 400 and "不能验收自己的成果" in bad.text, "主责人与验收人是同一个人，发布被拦住")
        check.status(edit(0, responsible_user_id=uid("zh"), reviewer_user_id=uid("boss")), 200, "改回张三主责、boss 验收")
        check.status(edit(2, responsible_user_id=uid("out")), 400, "不能指派其他企业的员工")
        check.status(edit(2, responsible_user_id=uid("boss")), 400, "验收人 boss 不是本部门成员，不能做主责")
        plan = rs_get(f"/plans/{plan_id}", "head").json()
        check.ok(plan["canPublish"] and plan["blockerCount"] == 0, "缺口补全，可以发布")
        published = rs_post(f"/plans/{plan_id}/publish", "head", note="按例会决定指派")
        check.status(published, 200, "负责人正式发布")
        check.status(rs_post(f"/plans/{plan_id}/publish", "head"), 400, "重复发布被拒绝")
        check.ok(all(t["status"] == "PENDING_ACCEPT" for t in published.json()["tasks"]), "5 项都进入待员工接受")
        t_zh, t_li, t_wa, t_ownerless1, t_ownerless2 = task_ids

        print("== 3.5 提醒：发布后“等你接受”出现在员工待办里 ==")
        run_db(rr.run_resp_accept)
        items = run_db(lambda s_: work_item_service.list_items(s_, uid("zh"), "all"))["items"]
        check.ok(any("等你接受" in i["title"] and i["status"] == "open" for i in items), "发布后张三的待办里出现“等你接受”")

        print("== 4. 员工接受、异议、协商 ==")
        mine = rs_get("/tasks", "zh", view="mine").json()
        check.ok([t["id"] for t in mine] == [t_zh] and "accept" in mine[0]["myActions"], "张三的“我的责任”出现待接受事项")
        check.status(act(t_zh, "accept", "head"), 403, "指派人不能替员工接受")
        check.status(act(t_zh, "accept", "wa"), 403, "其他员工不能替他接受")
        check.ok(rs_get(f"/tasks/{t_zh}", "zh").json()["task"]["sourceEvidence"] in meeting, "接受前能看到原文依据")
        check.status(act(t_li, "object", "li", reason=""), 400, "提异议必须写内容")
        check.status(act(t_li, "object", "li", reason="月底前交汇总表和我手上的结账冲突，建议延后三天"), 200, "李四对期限提出异议")
        check.ok(status_of(t_li) == "NEGOTIATING", "进入待重新协商")
        check.status(act(t_li, "accept", "li"), 400, "异议未处理前不能直接接受")
        check.status(act(t_li, "revise", "li", reason="自己改", due_date=due(40)), 403, "员工不能自己改期限")
        revised = act(t_li, "revise", "head", reason="同意延后三天", due_date=due(35))
        check.status(revised, 200, "负责人调整期限（写明原因）")
        check.ok(revised.json()["task"]["status"] == "PENDING_ACCEPT", "调整后重新回到待员工接受")
        changes = [e for e in revised.json()["events"] if e["type"] == "REVISED"][-1]
        check.ok(changes["note"] == "同意延后三天" and changes["detailData"]["changes"][0]["fieldLabel"] == "截止日期", "期限变更留下记录（谁、为何、前后值）")
        for who, task in (("zh", t_zh), ("li", t_li), ("wa", t_wa), ("wa", t_ownerless1), ("li", t_ownerless2)):
            check.status(act(task, "accept", who), 200, f"{who} 接受责任 #{task}")
        check.status(act(t_zh, "accept", "zh"), 400, "重复点击接受不会重复生效")

        print("== 5. 提醒自动关闭 ==")
        run_db(rr.run_resp_accept)
        pending_items = run_db(lambda s_: work_item_service.list_items(s_, uid("zh"), "all"))["items"]
        check.ok(not [i for i in pending_items if "等你接受" in i["title"] and i["status"] == "open"], "全部接受后“等你接受”提醒已自动关闭")

        print("== 6. 阻塞、协调、提交、验收、退回 ==")
        check.status(act(t_zh, "block", "zh", reason=""), 400, "受阻必须写原因")
        check.status(act(t_zh, "block", "zh", reason="缺少测试环境账号", waiting_on_user_id=uid("boss")), 200, "张三报告受阻（等 boss）")
        board = rs_get("/summary", "head").json()
        check.ok(any(b["id"] == t_zh and b["reason"] == "缺少测试环境账号" for b in board["blocked"]), "负责人的看板能看到受阻原因")
        check.status(rs_get("/summary", "zh"), 403, "普通员工看不到部门汇总")
        check.status(act(t_zh, "unblock", "head", note="已找运维开通账号"), 200, "负责人协调后解除受阻")
        check.status(act(t_zh, "submit", "zh", summary="构建包已上传", link="javascript:alert(1)"), 400, "成果链接只允许 http/https")
        check.status(act(t_zh, "submit", "zh", summary="构建包已上传到制品库", link="https://example.com/build/1"), 200, "张三提交成果")
        check.status(act(t_zh, "verify", "zh"), 403, "主责人不能验收自己的成果")
        check.status(act(t_zh, "verify", "head"), 403, "指派人也不能替验收人确认完成")
        pending = rs_get("/tasks", "boss", view="review", status="PENDING_REVIEW").json()
        check.ok([t["id"] for t in pending] == [t_zh], "验收人 boss（在管理层部门）的“待我验收”里有这项")
        check.status(act(t_zh, "rework", "boss", reason=""), 400, "退回必须写原因")
        check.status(act(t_zh, "rework", "boss", reason="回归用例没有覆盖登录页"), 200, "验收人退回")
        check.status(act(t_zh, "submit", "zh", summary="补充了登录页回归用例"), 200, "重新提交")
        verified = act(t_zh, "verify", "boss", note="符合验收标准")
        check.status(verified, 200, "验收人验收通过")
        check.ok(verified.json()["task"]["reworkCount"] == 1 and len(verified.json()["deliverables"]) == 2, "记录了 1 次退回、2 次提交")
        types = [e["type"] for e in verified.json()["events"] if e["type"] != "TASK_EDITED"]
        check.ok(types == ["PUBLISHED", "ACCEPTED", "BLOCKED", "UNBLOCKED", "SUBMITTED", "REWORK", "SUBMITTED", "VERIFIED"], f"履责记录完整：{types}")
        check.status(act(t_zh, "verify", "boss"), 400, "已完成的责任不能重复验收")

        print("== 7. 延期、转交 ==")
        check.status(act(t_wa, "request-extension", "wa", proposed_date=due(1), reason="x"), 400, "申请的日期必须晚于现有截止日期")
        check.status(act(t_wa, "request-extension", "wa", proposed_date=due(30), reason="发布说明要等新功能冻结"), 200, "王五申请延期")
        check.status(act(t_wa, "decide-extension", "wa", approve=True), 403, "员工不能自己批准延期")
        check.status(act(t_wa, "decide-extension", "head", approve=True, note="同意"), 200, "负责人同意延期")
        check.ok(rs_get(f"/tasks/{t_wa}", "wa").json()["task"]["dueDate"] == due(30), "截止日期已更新")
        check.status(act(t_ownerless1, "request-transfer", "wa", reason="我下周休假，建议交给李四"), 200, "王五申请转交")
        moved = act(t_ownerless1, "decide-transfer", "head", approve=True, responsible_user_id=uid("li"), reason="同意转交给李四")
        check.status(moved, 200, "负责人同意转交")
        check.ok(moved.json()["task"]["status"] == "PENDING_ACCEPT" and moved.json()["task"]["responsibleName"] == nm("li"), "新主责人需要重新接受")
        check.status(act(t_ownerless1, "accept", "wa"), 403, "原主责人不能再接受、操作这项责任")
        check.status(act(t_ownerless1, "submit", "wa", summary="我来提交"), 403, "也不能替新主责人提交")
        check.status(act(t_ownerless1, "accept", "li"), 200, "李四接受转交的责任")

        print("== 8. 离开部门后失去访问 ==")
        check.status(rs_get(f"/tasks/{t_li}", "li", team=ops), 200, "李四在运营部时能看到自己的责任")
        db.execute(text("UPDATE team_members SET status='disabled' WHERE team_id=:t AND user_id=:u"), {"t": ops, "u": uid("li")})
        db.commit()
        check.status(rs_get(f"/tasks/{t_li}", "li", team=ops), 403, "离开运营部后，用运营部身份访问被拒绝")
        check.status(rs_get(f"/tasks/{t_li}", "li", team=mgmt), 404, "换用管理层部门身份访问，也看不到运营部的责任")
        check.status(act(t_li, "submit", "li", team=mgmt, summary="做完了"), 404, "也不能提交成果")
        check.ok(rs_get("/tasks", "li", team=mgmt, view="mine").json() == [], "“我的责任”里没有运营部的事项")
        db.execute(text("UPDATE team_members SET status='active' WHERE team_id=:t AND user_id=:u"), {"t": ops, "u": uid("li")})
        db.commit()
        check.status(rs_get(f"/tasks/{t_li}", "li", team=ops), 200, "恢复成员身份后恢复访问")

        print("== 9. 其他企业与取消 ==")
        check.status(rs_get(f"/tasks/{t_zh}", "out"), 404, "其他企业的人读不到本企业的责任")
        check.status(rs_get(f"/plans/{plan_id}", "out"), 404, "也读不到计划")
        check.status(rs_get("/tasks", "out", team=ops), 403, "不能用别的企业的部门身份查询")
        check.status(act(t_ownerless2, "cancel", "li", reason="不做了"), 403, "员工不能取消责任")
        check.status(act(t_ownerless2, "cancel", "head", reason=""), 400, "取消必须写原因")
        check.status(act(t_ownerless2, "cancel", "head", reason="方案并入首页联调"), 200, "负责人取消一项责任（写明原因）")

        print("== 10. Agent 工具（身份与工作台一致，决定要人确认） ==")
        from service.tools import responsibility as tools
        from service.tools.base import ToolContext
        from service.tools.langchain_adapter import adapt_tool

        def tool(cls, who):
            instance = cls()
            instance.set_context(ToolContext(user_id=uid(who)))
            return instance
        import json
        listed = json.loads(tool(tools.ListMyResponsibilitiesTool, "wa").execute())
        check.ok(isinstance(listed, list) and {t["title"][:4] for t in listed}, "员工的 Agent 能列出自己的责任")
        check.ok(all("internalField" not in t for t in listed), "只返回简要字段")
        denied = json.loads(tool(tools.GetResponsibilityDetailTool, "out").execute(task_id=t_zh))
        check.ok("error" in denied, "其他企业的人用 Agent 也读不到")
        risks = json.loads(tool(tools.GetDepartmentResponsibilityRisksTool, "head").execute())
        check.ok("overdueCount" in risks and "load" in risks, "负责人的 Agent 能查到风险摘要")
        no_risk = json.loads(tool(tools.GetDepartmentResponsibilityRisksTool, "zh").execute())
        check.ok("error" in no_risk, "普通员工的 Agent 查不到部门风险摘要")
        before = status_of(t_wa)
        lc = adapt_tool(tools.AcceptResponsibilityTool(), ToolContext(user_id=uid("wa")))
        reply = json.loads(lc.func(task_id=t_wa))
        check.ok(reply["status"] == "confirmation_required" and status_of(t_wa) == before, "接受责任是高风险工具：只生成待确认单，没有真正执行")
        extracted = json.loads(tool(tools.ExtractResponsibilityPlanTool, "head").execute(
            title="临时碰头会", source_type="MEETING", source_text=f"临时碰头会：{nm('wa')}负责联系物流供应商，周五前提交报价单。",
            tasks=[{"title": "联系物流供应商", "responsible_name": nm("wa"), "due_text": "周五", "deliverable": "报价单",
                    "evidence": f"{nm('wa')}负责联系物流供应商，周五前提交报价单"}]))
        check.ok(extracted.get("planId") and "没有通知任何人" in extracted["note"], "Agent 把聊天里的纪要整理成了草稿计划（没有通知任何人）")
        fake = json.loads(tool(tools.ExtractResponsibilityPlanTool, "head").execute(
            title="编造", source_type="MEETING", source_text="临时碰头会：大家随便聊了聊，没有任何结论，也没有任何安排。",
            tasks=[{"title": "完成全部工作", "responsible_name": nm("wa"), "evidence": "王五承诺本周完成全部工作"}]))
        check.ok("error" in fake, "Agent 编造的依据（原文里没有）被拒绝")

        print("== 11. 汇总、周报与效率指标 ==")
        for who, task in (("wa", t_wa), ("li", t_li), ("li", t_ownerless1)):
            act(task, "submit", who, summary="已完成并提交")
            check.status(act(task, "verify", "boss"), 200, f"验收 #{task}")
        summary = rs_get("/summary", "head").json()
        check.ok(summary["byStatus"]["DONE"] == 4 and summary["byStatus"]["CANCELLED"] == 1, "汇总：4 项完成、1 项取消")
        plan_now = rs_get(f"/plans/{plan_id}", "head").json()
        check.ok(plan_now["status"] == "COMPLETED", "全部结束后计划自动收尾为已完成")
        m = summary["metrics"]
        check.ok(m["firstPassRate"] is not None and 0 < m["firstPassRate"] < 100, f"首次验收通过率 {m['firstPassRate']}%（有一次退回）")
        check.ok(m["onTimeSubmitRate"] == 100.0, "按时提交率 100%")
        check.ok(m["aiMatchRate"] is not None and m["aiMatchSamples"] >= 3, f"AI 草稿一次匹配准确率 {m['aiMatchRate']}%（样本 {m['aiMatchSamples']}）")
        check.ok(m["missingFoundBeforePublish"] > 0 and m["changeCount"] >= 2, "发布前发现的遗漏、责任变更次数都被统计")
        check.ok(m["blockedResolveAvgHours"] is not None, "受阻解除时长被统计")
        check.ok("完成数量" not in str(summary.keys()), "指标里没有“完成任务数量”这类鼓励拆小任务的指标")
        check.ok("本周" in summary["narrative"] and "首次验收通过率" in summary["narrative"], "部门周报文字由系统事实生成")
        home = client.get(f"/enterprise/home?team_id={ops}", headers=h("head")).json()
        keys = {c["key"] for c in home["cards"]}
        check.ok({"resp_accept", "resp_doing", "resp_team_overdue", "resp_week"} <= keys, "部门首页出现责任协同卡片")
        stats = client.get(f"/enterprise/automation?team_id={ops}", headers=h("head")).json()["stats"]
        check.ok(stats["by_kind"]["responsibility"]["applied"] == 1, "AI 工作成果统计里记录了这次责任计划整理（采纳率口径）")

        print(f"\n全部 {len(check.passed)} 项检查通过")

        if KEEP:
            db.execute(text("UPDATE organization_members SET status='active' WHERE organization_id=:o"), {"o": org})
            for n, user in u.items():
                user["name"] = f"kp_rs_{n}_{SUFFIX[:3]}"
                db.execute(text("UPDATE `user` SET name=:n WHERE id=:i"), {"n": user["name"], "i": user["id"]})
            db.commit()
            rc._created_user_ids.clear()
            print("\n--keep：保留数据，可在浏览器登录（密码 Passw0rd!Secure）：")
            for n, user in u.items():
                print(f"  {n}: {user['name']}")
    finally:
        if not KEEP:
            clean_business_data(ent, team_ids_all, [x["id"] for x in u.values()])
            db.execute(text(f"DELETE FROM automation_work WHERE team_id IN ({','.join(str(t) for t in team_ids_all)})"))
            db.commit()
            rc.cleanup()
            ids = ",".join(str(x) for x in team_ids_all)
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
