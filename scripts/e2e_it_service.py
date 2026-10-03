"""IT 服务台的真实端到端验收：FastAPI（含权限层）+ MySQL + Java 业务服务全部是真的，
只有"AI 整理工单"里的模型调用换成确定性替身。

链路：员工自助建议 → 提工单（账号/设备申请先由部门负责人批准）→ IT 接单、等待用户、解决 → 申请人确认/重开；
IT 人员不能处理自己的工单；设备入库 → 随工单发放 → 收回 → 报废；AI 整理的工单草稿经人工核对后保存；
同时验证越权（其他部门、其他企业、被停用的成员）都被拦住。

前置：MySQL 已启动；Java 业务服务在 ENTERPRISE_HUB_BASE_URL（默认 127.0.0.1:8090）运行（jar 必须是最新构建）。
用法：.venv\\Scripts\\python.exe scripts\\e2e_it_service.py [--keep | --purge]
  --keep   验收通过后保留测试数据并打印登录账号（kp_ 前缀），供浏览器里继续手工验证。
  --purge  清理 --keep 留下的全部数据，然后退出。
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
from sqlalchemy import create_engine, text  # noqa: E402

load_dotenv(ROOT / ".env")

from FasdtApi.automation_work import GenerateRequest  # noqa: E402
from models.async_db import AsyncSessionLocal  # noqa: E402
from models.init_db import SessionLocal  # noqa: E402
from service import automation_work_service as work_svc  # noqa: E402
from tests.test_enterprise_access import _add_org_member, _add_team_member, _create_org, _create_team  # noqa: E402

SUFFIX = uuid.uuid4().hex[:6].upper()
KEEP = "--keep" in sys.argv
PREFIX = "e2e-it"
LOOP = asyncio.new_event_loop()


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
        self.ok(response.status_code == expected, f"{message}（{response.status_code}）" if response.status_code == expected
                else f"{message}：期望 {expected}，实际 {response.status_code} {response.text[:200]}")


def purge_kept():
    ent, db = enterprise_engine(), SessionLocal()
    try:
        orgs = [r[0] for r in db.execute(text("SELECT id FROM organizations WHERE name LIKE 'e2e-it%'")).all()]
        team_ids = [r[0] for r in db.execute(text(
            "SELECT t.id FROM teams t JOIN organizations o ON t.organization_id=o.id WHERE o.name LIKE 'e2e-it%'")).all()]
        user_ids = [r[0] for r in db.execute(text("SELECT id FROM `user` WHERE name LIKE 'kp_it_%'")).all()]
        if team_ids:
            ids = ",".join(str(t) for t in team_ids)
            with ent.begin() as conn:
                conn.execute(text(f"DELETE FROM it_ticket_comment WHERE ticket_id IN (SELECT id FROM it_ticket WHERE team_id IN ({ids}))"))
                conn.execute(text(f"DELETE FROM it_ticket WHERE team_id IN ({ids})"))
                conn.execute(text(f"DELETE FROM it_device_event WHERE device_id IN (SELECT id FROM it_device WHERE managing_team_id IN ({ids}))"))
                conn.execute(text(f"DELETE FROM it_device WHERE managing_team_id IN ({ids})"))
        if user_ids:
            rc._purge_users("id IN (" + ",".join(str(u) for u in user_ids) + ")")
        if team_ids:
            ids = ",".join(str(t) for t in team_ids)
            db.execute(text(f"DELETE FROM team_members WHERE team_id IN ({ids})"))
            db.execute(text(f"DELETE FROM teams WHERE id IN ({ids})"))
        for org in orgs:
            db.execute(text("DELETE FROM organization_members WHERE organization_id=:o"), {"o": org})
            db.execute(text("DELETE FROM organizations WHERE id=:o"), {"o": org})
        db.commit()
        print(f"已清理：{len(orgs)} 个企业、{len(team_ids)} 个部门、{len(user_ids)} 个用户")
    finally:
        db.close()


def fake_model(*args, **kwargs):
    source = args[5]
    return json.dumps({
        "category": "INCIDENT", "priority": "NORMAL", "title": "三楼打印机脱机",
        "description": "三楼打印机卡纸后一直脱机，整个部门无法打印", "evidence": source[:30], "warnings": []}, ensure_ascii=False), {"total_tokens": 99}


def main():
    check = Checker()
    ent = enterprise_engine()
    db = SessionLocal()
    client = rc.make_client()

    owner = rc.create_user(f"{PREFIX}-own")
    users = {name: rc.create_user(f"{PREFIX}-{name}") for name in ("employee", "head", "it1", "it2", "hr", "outsider")}
    org = _create_org(db, f"{PREFIX}-{SUFFIX}", owner["id"])
    other_org = _create_org(db, f"{PREFIX}2-{SUFFIX}", owner["id"])
    teams = {code: _create_team(db, org, f"{PREFIX}-{code}-{SUFFIX}", owner["id"]) for code in ("it", "sales", "hr")}
    other_it = _create_team(db, other_org, f"{PREFIX}-oit-{SUFFIX}", owner["id"])
    for code, team in {**teams, "it2": other_it}.items():
        db.execute(text("UPDATE teams SET department_code=:c WHERE id=:t"), {"c": code.rstrip("2"), "t": team})
    db.commit()
    for name in ("employee", "head", "it1", "it2", "hr"):
        _add_org_member(db, org, users[name]["id"], "member")
    _add_org_member(db, other_org, users["outsider"]["id"], "member")
    _add_team_member(db, teams["sales"], users["employee"]["id"], "member")
    _add_team_member(db, teams["sales"], users["head"]["id"], "admin")
    _add_team_member(db, teams["it"], users["it1"]["id"], "member")
    _add_team_member(db, teams["it"], users["it2"]["id"], "member")
    _add_team_member(db, teams["hr"], users["hr"]["id"], "admin")
    _add_team_member(db, other_it, users["outsider"]["id"], "member")

    def h(name):
        return users[name]["headers"]

    def desk(path="/tickets", team="it", **params):
        query = "&".join([f"team_id={teams[team]}"] + [f"{k}={v}" for k, v in params.items()])
        return f"/enterprise/it/desk{path}?{query}"

    def employee_post(path, body=None, who="employee"):
        return client.post(f"/enterprise/it/tickets{path}", json=body, headers=h(who)) if body is not None \
            else client.post(f"/enterprise/it/tickets{path}", headers=h(who))

    def new_ticket(category, title, description, who="employee", team="sales", priority=None):
        body = {"team_id": teams[team], "category": category, "title": title, "description": description}
        if priority:
            body["priority"] = priority
        response = client.post("/enterprise/it/tickets", json=body, headers=h(who))
        check.status(response, 200, f"{who} 提交工单「{title}」")
        return response.json()

    def desk_post(path, body, who="it1", team="it"):
        return client.post(f"/enterprise/it/desk{path}", json={"team_id": teams[team], **body}, headers=h(who))

    async def run_session(fn):
        async with AsyncSessionLocal() as s:
            return await fn(s)

    team_ids = [*teams.values(), other_it]
    try:
        print("== 自助建议与提交 ==")
        suggest = client.get(f"/enterprise/it/suggest?text=打印机卡纸一直脱机无法打印&team_id={teams['sales']}", headers=h("employee"))
        check.status(suggest, 200, "提交前获取自助建议")
        check.ok(suggest.json()["classification"]["category"] == "INCIDENT" and "打印机" in suggest.json()["articles"][0]["title"],
                 "建议里有“打印机”自助排查和故障分类")
        incident = new_ticket("INCIDENT", f"打印机坏了{SUFFIX}", "三楼打印机卡纸后一直脱机，无法打印")
        check.ok(incident["status"] == "OPEN" and incident["slaStatus"] == "OK", "故障工单直接进入 IT 队列，SLA 开始计时")
        check.ok(incident["categoryLabel"] == "故障" and incident["requesterName"] == users["employee"]["name"], "返回带分类名称和申请人姓名")
        dup = client.post("/enterprise/it/tickets", headers=h("employee"), json={
            "team_id": teams["sales"], "category": "INCIDENT", "title": f"打印机坏了{SUFFIX}", "description": "同一个问题再提一次"})
        check.ok(dup.status_code == 400 and "标题相同" in dup.text, "同标题未结束的工单不能重复提交")

        print("== 越权 ==")
        for who, team in (("employee", "sales"), ("head", "sales"), ("hr", "hr")):
            check.status(client.get(desk(team=team), headers=h(who)), 403, f"{who} 以自己部门身份进 IT 台被拒绝")
        for who in ("employee", "head", "hr"):
            check.status(client.get(desk(), headers=h(who)), 403, f"{who} 借 IT 部门 id 进 IT 台被拒绝（不是成员）")
        check.status(client.get(desk(), headers=h("outsider")), 403, "其他企业的 IT 不能进本企业 IT 台")
        other_view = client.get(f"/enterprise/it/desk/tickets/{incident['id']}?team_id={other_it}", headers=h("outsider"))
        check.status(other_view, 404, "其他企业 IT 用自己的部门读本企业工单 id → 按不存在处理")
        check.status(client.get(f"/enterprise/it/tickets/{incident['id']}", headers=h("hr")), 404, "无关员工看不到别人的工单")
        check.status(client.get(f"/enterprise/it/tickets/team-pending?team_id={teams['sales']}", headers=h("employee")),
                     403, "普通员工不能看待批准列表")

        print("== 账号申请需先经部门负责人批准 ==")
        account = new_ticket("ACCOUNT", f"新员工开通账号{SUFFIX}", "新同事下周入职，需要开通 OA 账号")
        check.ok(account["status"] == "PENDING_APPROVAL", "账号申请进入待批准")
        queue = client.get(desk(), headers=h("it1")).json()
        check.ok(account["id"] not in [t["id"] for t in queue] and incident["id"] in [t["id"] for t in queue], "待批准的不在 IT 队列里，故障工单在")
        check.status(client.post(f"/enterprise/it/desk/tickets/{account['id']}/assign", json={"team_id": teams["it"], "take": True}, headers=h("it1")),
                     400, "IT 不能处理还没批准的工单")
        check.status(client.post(f"/enterprise/it/tickets/{account['id']}/decide", json={"team_id": teams["hr"], "action": "approve"}, headers=h("hr")),
                     404, "别的部门的负责人不能批准（按不存在处理）")
        check.status(client.post(f"/enterprise/it/tickets/{account['id']}/decide", json={"team_id": teams["sales"], "action": "approve"}, headers=h("employee")),
                     403, "普通员工不能批准")
        pending = client.get(f"/enterprise/it/tickets/team-pending?team_id={teams['sales']}", headers=h("head")).json()
        check.ok([t["id"] for t in pending] == [account["id"]] and pending[0]["requesterName"], "部门负责人在待批准列表看到该申请")
        approved = client.post(f"/enterprise/it/tickets/{account['id']}/decide", json={"team_id": teams["sales"], "action": "approve", "note": "同意"},
                               headers=h("head"))
        check.ok(approved.status_code == 200 and approved.json()["status"] == "OPEN", "负责人批准后进入 IT 队列")

        print("== IT 接单 → 等待用户 → 解决 → 确认 ==")
        bad_assign = desk_post(f"/tickets/{incident['id']}/assign", {"assignee_user_id": users["employee"]["id"]})
        check.status(bad_assign, 400, "不能指派给非 IT 成员")
        assign = desk_post(f"/tickets/{incident['id']}/assign", {"take": True})
        check.ok(assign.status_code == 200 and assign.json()["status"] == "IN_PROGRESS" and assign.json()["assigneeName"] == users["it1"]["name"],
                 "IT 接单后处理中，处理人是自己")
        check.status(desk_post(f"/tickets/{incident['id']}/status", {"status": "WAITING_USER"}), 400, "等待用户必须写明要补充什么")
        wait = desk_post(f"/tickets/{incident['id']}/status", {"status": "WAITING_USER", "note": "请告诉我打印机型号"})
        check.ok(wait.json()["status"] == "WAITING_USER" and wait.json()["slaStatus"] == "PAUSED", "等待用户期间 SLA 暂停")
        mine = client.get("/enterprise/it/tickets/mine", headers=h("employee")).json()
        check.ok(any(t["id"] == incident["id"] and t["statusLabel"] == "等待你补充" for t in mine), "申请人看到“等待你补充”")
        reply = employee_post(f"/{incident['id']}/comments", {"body": "惠普 M227"})
        check.ok(reply.status_code == 200 and reply.json()["status"] == "IN_PROGRESS", "申请人回复后工单回到处理中")
        desk_post(f"/tickets/{incident['id']}/comments", {"body": "怀疑是驱动问题", "internal": True})
        visible = client.get(f"/enterprise/it/tickets/{incident['id']}", headers=h("employee")).json()
        check.ok(not any("驱动问题" in c["body"] for c in visible["comments"]), "内部备注申请人看不到")
        internal = client.get(desk(f"/tickets/{incident['id']}"), headers=h("it2")).json()
        check.ok(any("驱动问题" in c["body"] and c["internal"] for c in internal["comments"]), "IT 同事能看到内部备注")
        resolved = desk_post(f"/tickets/{incident['id']}/resolve", {"resolution": "重装驱动后恢复打印"}, "it2")
        check.ok(resolved.status_code == 200 and resolved.json()["status"] == "RESOLVED", "另一位 IT 同事解决工单")
        closed = employee_post(f"/{incident['id']}/confirm")
        check.ok(closed.status_code == 200 and closed.json()["status"] == "CLOSED", "申请人确认后关闭")

        print("== 制单与处理分离、重新打开 ==")
        own = new_ticket("INCIDENT", f"IT 同事自己的问题{SUFFIX}", "我的笔记本无法开机", who="it1", team="it")
        check.ok(desk_post(f"/tickets/{own['id']}/assign", {"take": True}, "it1").status_code == 400, "IT 不能接自己提交的工单")
        check.ok(desk_post(f"/tickets/{own['id']}/assign", {"take": True}, "it2").status_code == 200, "其他 IT 同事可以接")
        again = new_ticket("INCIDENT", f"邮箱收不到邮件{SUFFIX}", "邮箱从早上起就收不到邮件", priority="HIGH")
        desk_post(f"/tickets/{again['id']}/assign", {"take": True})
        desk_post(f"/tickets/{again['id']}/resolve", {"resolution": "重新添加了邮箱账号"})
        reopened = employee_post(f"/{again['id']}/reopen", {"reason": "还是收不到"})
        check.ok(reopened.json()["status"] == "IN_PROGRESS" and reopened.json()["reopenCount"] == 1, "没解决可以重新打开，记录重开次数")
        upgrade = desk_post(f"/tickets/{again['id']}/reclassify", {"category": "INCIDENT", "priority": "URGENT", "reason": "影响整个部门"})
        check.ok(upgrade.status_code == 200 and upgrade.json()["priorityLabel"] == "紧急", "IT 可调整优先级并留痕")
        check.ok(desk_post(f"/tickets/{again['id']}/reclassify", {"category": "ACCOUNT", "priority": "NORMAL", "reason": "改成账号"}).status_code == 400,
                 "不能把故障改成需要审批的类型来绕过流程")

        print("== 设备台账与生命周期 ==")
        asset = f"NB-{SUFFIX}"
        created = desk_post("/devices", {"asset_no": asset, "device_type": "LAPTOP", "model": "ThinkPad T14", "warranty_until": "2029-01-01"})
        check.ok(created.status_code == 200 and created.json()["status"] == "IN_STOCK", "设备入库")
        check.ok(desk_post("/devices", {"asset_no": asset, "device_type": "LAPTOP", "model": "x"}).status_code == 400, "资产编号重复被拒绝")
        device_ticket = new_ticket("DEVICE", f"申请笔记本{SUFFIX}", "新岗位需要一台笔记本电脑")
        check.ok(device_ticket["status"] == "PENDING_APPROVAL", "设备申请先待批准")
        client.post(f"/enterprise/it/tickets/{device_ticket['id']}/decide", json={"team_id": teams["sales"], "action": "approve"}, headers=h("head"))
        bad_holder = desk_post(f"/devices/{created.json()['id']}/assign", {"user_id": users["outsider"]["id"]})
        check.status(bad_holder, 400, "设备不能发给其他企业的人")
        issued = desk_post(f"/devices/{created.json()['id']}/assign", {"user_id": users["employee"]["id"], "ticket_id": device_ticket["id"]})
        check.ok(issued.status_code == 200 and issued.json()["assigneeName"] == users["employee"]["name"], "随设备申请工单发放给申请人")
        mine_devices = client.get("/enterprise/it/devices/mine", headers=h("employee")).json()
        check.ok([d["assetNo"] for d in mine_devices] == [asset], "申请人在“我名下的设备”里看到它")
        check.ok(desk_post(f"/devices/{created.json()['id']}/retire", {}).status_code == 400, "还在员工名下不能报废")
        for action in ("repair", "repair-done", "return", "retire"):
            step = desk_post(f"/devices/{created.json()['id']}/{action}", {"note": action})
            check.status(step, 200, f"设备 {action}")
        history = client.get(desk(f"/devices/{created.json()['id']}"), headers=h("it1")).json()
        check.ok([e["eventType"] for e in history["events"]] == ["CREATED", "ASSIGNED", "REPAIR", "REPAIRED", "RETURNED", "RETIRED"],
                 "设备事件历史完整")

        print("== AI 整理的工单草稿（模型为确定性替身）→ 人工核对 → 保存 ==")
        source = "三楼打印机卡纸后一直脱机，整个部门都没法打印，下午客户会议要用资料，比较着急。"
        with patch.object(work_svc, "async_chat_with_usage", AsyncMock(side_effect=fake_model)), \
             patch.object(work_svc, "async_get_api_config", AsyncMock(return_value={"api_key": "stub"})):
            req = GenerateRequest(request_key=uuid.uuid4(), team_id=teams["sales"], kind="ticket", model_name="glm-4", source_text=source)
            work = LOOP.run_until_complete(run_session(lambda s: work_svc.generate(s, users["employee"]["id"], req)))
            check.ok(work["status"] == "ready", "工单整理完成，待人工核对")
            texts = [c["text"] for c in work["business_checks"]]
            check.ok(any("可先自助排查" in t and "打印机" in t for t in texts), "核对结果带自助排查建议（来自真实业务库）")
            check.ok(any("建议把优先级调成紧急" in t for t in texts), "描述里“整个部门”被识别为影响范围大，提示调成紧急")
            proposal = {**work["proposal"], "priority": "URGENT", "title": f"三楼打印机脱机{SUFFIX}"}
            applied = LOOP.run_until_complete(run_session(lambda s: work_svc.apply_work(s, users["employee"]["id"], work["id"], proposal)))
            check.ok(applied["status"] == "applied" and applied["business_result"]["priority"] == "URGENT", "核对后保存为真实工单（紧急）")
            again_apply = LOOP.run_until_complete(run_session(lambda s: work_svc.apply_work(s, users["employee"]["id"], work["id"], proposal)))
            with ent.connect() as conn:
                count = conn.execute(text("SELECT COUNT(*) FROM it_ticket WHERE requester_user_id=:u AND title=:t"),
                                     {"u": users["employee"]["id"], "t": proposal["title"]}).scalar()
            check.ok(again_apply["business_result"]["id"] == applied["business_result"]["id"] and count == 1, "重复保存不产生第二张工单")

        print("== 停用成员、汇总 ==")
        db.execute(text("UPDATE organization_members SET status='disabled' WHERE organization_id=:o AND user_id=:u"), {"o": org, "u": users["it2"]["id"]})
        db.commit()
        check.status(client.get(desk(), headers=h("it2")), 403, "企业成员被停用后立即失去 IT 台权限")
        staff = client.get(desk("/staff"), headers=h("it1")).json()
        check.ok({s["id"] for s in staff} == {users["it1"]["id"]}, "指派候选里不再有被停用的成员")
        summary = client.get(desk("/summary"), headers=h("it1"))
        check.status(summary, 200, "服务台汇总可读")
        data = summary.json()
        check.ok(data["effect"]["resolved"] >= 1 and data["effect"]["slaMetRate"] == 100.0, "汇总里有已解决工单且 SLA 达成")
        check.ok("近 30 天解决" in data["narrative"] and "SLA 达成率" in data["narrative"], "服务台小结文字与数字一致")
        print(f"\n全部 {len(check.passed)} 项检查通过")

        if KEEP:
            db.execute(text("UPDATE organization_members SET status='active' WHERE organization_id=:o AND user_id=:u"), {"o": org, "u": users["it2"]["id"]})
            for name, user in users.items():
                user["name"] = f"kp_it_{name}_{SUFFIX[:3]}"  # 登录名最多 20 个字符
                db.execute(text("UPDATE `user` SET name=:n WHERE id=:i"), {"n": user["name"], "i": user["id"]})
            db.commit()
            rc._created_user_ids.clear()   # 否则测试辅助模块退出时会把这些用户当作自己建的测试用户删掉
            print("\n--keep：保留数据，可在浏览器登录（密码 Passw0rd!Secure）：")
            for name, user in users.items():
                print(f"  {name}: {user['name']}")
            print(f"  销售部门 team_id={teams['sales']}，IT 部门 team_id={teams['it']}")
    finally:
        if not KEEP:
            ids = ",".join(str(t) for t in team_ids)
            with ent.begin() as conn:
                conn.execute(text(f"DELETE FROM it_ticket_comment WHERE ticket_id IN (SELECT id FROM it_ticket WHERE team_id IN ({ids}))"))
                conn.execute(text(f"DELETE FROM it_ticket WHERE team_id IN ({ids})"))
                conn.execute(text(f"DELETE FROM it_device_event WHERE device_id IN (SELECT id FROM it_device WHERE managing_team_id IN ({ids}))"))
                conn.execute(text(f"DELETE FROM it_device WHERE managing_team_id IN ({ids})"))
                conn.execute(text("DELETE FROM audit_event WHERE user_id IN (" + ",".join(str(u["id"]) for u in users.values()) + ")"))
            db.execute(text("DELETE FROM automation_work WHERE team_id IN (" + ids + ")"))
            db.commit()
            rc.cleanup()
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
