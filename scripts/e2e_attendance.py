"""考勤异常发现的真实端到端验收：FastAPI（含权限层）+ MySQL + Java 业务服务全部是真的，没有任何模型参与。

故事线：
  员工在系统里提交请假、负责人批准（一份未批准）→ 人事导入一份钉钉风格的考勤 Excel（带标题行、日期带星期、缺卡字样；
  其中一个人用的是考勤系统里的昵称）→ 昵称对不上，人事选择对应的人后重新导入 → 分析：已批准请假的人不算旷工，
  未批准的算；迟到、缺卡、休息日出勤被发现 → 员工说明 → 负责人认定（不能认定自己的、别的部门不能认定）→
  补录打卡后重新分析，原异常自动消除 → 提醒、部门首页卡片、Agent 工具都读到同一份事实。

前置：MySQL 已启动；Java 业务服务在 ENTERPRISE_HUB_BASE_URL（默认 127.0.0.1:8090）运行（jar 必须含 /oa/leave/requests/approved）。
用法：.venv\\Scripts\\python.exe scripts\\e2e_attendance.py [--keep | --purge]
  --keep   验收通过后保留数据并打印登录账号（kp_at_ 前缀），供浏览器里继续验证；--purge 清理保留的数据。
"""
import io
import json
import os
import pathlib
import sys
import uuid
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests import _route_client as rc  # noqa: E402  先导入：设置非生产环境变量

from dotenv import load_dotenv  # noqa: E402
from openpyxl import Workbook  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

load_dotenv(ROOT / ".env")

from models.init_db import SessionLocal  # noqa: E402
from tests._async_helpers import run_async  # noqa: E402
from tests.test_enterprise_access import _add_org_member, _add_team_member, _create_org, _create_team  # noqa: E402

SUFFIX = uuid.uuid4().hex[:6].upper()
KEEP = "--keep" in sys.argv
BEIJING = timezone(timedelta(hours=8))
TODAY = datetime.now(BEIJING).date()
MON = TODAY - timedelta(days=TODAY.weekday() + 7)          # 上周一
TUE, WED, SAT = MON + timedelta(days=1), MON + timedelta(days=2), MON - timedelta(days=2)


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
        good = response.status_code in expected if isinstance(expected, tuple) else response.status_code == expected
        self.ok(good, f"{message}（{response.status_code}）" if good else f"{message}：期望 {expected}，实际 {response.status_code} {response.text[:300]}")


def run_db(fn):
    from models.async_db import AsyncSessionLocal

    async def go():
        async with AsyncSessionLocal() as session:
            return await fn(session)
    return run_async(go())


def dingtalk_workbook(rows):
    """钉钉“每日统计”风格：标题和统计区间在上面，表头在第 4 行，日期带星期，缺卡写“缺卡”。"""
    book = Workbook()
    sheet = book.active
    sheet.title = "每日统计"
    sheet.append(["考勤报表"])
    sheet.append([f"统计时间：{SAT.isoformat()} 至 {WED.isoformat()}"])
    sheet.append([])
    sheet.append(["姓名", "部门", "日期", "上班1打卡时间", "下班1打卡时间"])
    weekday = "一二三四五六日"
    for name, day, first, last in rows:
        sheet.append([name, "销售部", f"{day.isoformat()} 星期{weekday[day.weekday()]}", first, last])
    sheet.append(["合计"])
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def main():
    from service.reminders import attendance_reminders as ar
    check = Checker()
    ent, db, client = enterprise_engine(), SessionLocal(), rc.make_client()
    owner = rc.create_user("at-own")
    names = ("hr", "head", "zh", "li", "wa", "oh", "oe", "out")
    u = {n: rc.create_user(n) for n in names}
    org = _create_org(db, f"e2e-at-{SUFFIX}", owner["id"])
    hr_team = _create_team(db, org, f"e2e-at-hr-{SUFFIX}", owner["id"])
    sales = _create_team(db, org, f"e2e-at-sales-{SUFFIX}", owner["id"])
    ops = _create_team(db, org, f"e2e-at-ops-{SUFFIX}", owner["id"])
    team_ids = [hr_team, sales, ops]
    db.execute(text("UPDATE teams SET department_code='hr' WHERE id=:t"), {"t": hr_team})
    db.commit()
    for n in names:
        _add_org_member(db, org, u[n]["id"], "member")
    _add_team_member(db, hr_team, u["hr"]["id"], "member")
    _add_team_member(db, sales, u["head"]["id"], "admin")
    for n in ("zh", "li", "wa"):
        _add_team_member(db, sales, u[n]["id"], "member")
    _add_team_member(db, ops, u["oh"]["id"], "admin")
    _add_team_member(db, ops, u["oe"]["id"], "member")
    team_of = {"hr": hr_team, "head": sales, "zh": sales, "li": sales, "wa": sales, "oh": ops, "oe": ops}

    def h(n):
        return u[n]["headers"]

    def nm(n):
        return u[n]["name"]

    def uid(n):
        return u[n]["id"]

    def get(path, who, **params):
        q = "&".join([f"team_id={team_of[who]}"] + [f"{k}={v}" for k, v in params.items()])
        return client.get(f"/enterprise/attendance{path}?{q}", headers=h(who))

    def post(path, who, **body):
        return client.post(f"/enterprise/attendance{path}", json={"team_id": team_of[who], **body}, headers=h(who))

    def upload(who, content, aliases=None, name="钉钉考勤.xlsx"):
        return client.post("/enterprise/attendance/import", headers=h(who), data={"team_id": team_of[who], "aliases": json.dumps(aliases or {})},
                           files={"file": (name, io.BytesIO(content), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})

    def mine(who, **params):
        return get("/anomalies", who, view="mine", **params).json()

    def find(who, day, kind):
        return next((a for a in mine(who) if a["work_date"] == day.isoformat() and a["type"] == kind), None)

    try:
        print("== 1. 真实请假：李四请假周一周二（负责人批准），王五请周三但没人批准 ==")
        with ent.begin() as conn:          # 年假余额：真实环境里由人事按制度发放，这里给两位员工各 10 天
            for who in ("li", "wa"):
                conn.execute(text("INSERT INTO leave_balance (user_id, leave_type_id, year, remaining_days) "
                                  "SELECT :u, id, :y, 10 FROM leave_type WHERE code='annual'"), {"u": uid(who), "y": MON.year})
        for who, start, end in (("li", MON, TUE), ("wa", WED, WED)):
            draft = client.post("/enterprise/oa/leave/mine", headers=h(who), json={
                "team_id": sales, "leave_type_code": "annual", "start_date": start.isoformat(), "end_date": end.isoformat(), "reason": "家里有事"})
            check.status(draft, 200, f"{who} 创建请假草稿")
            request_id = draft.json()["id"]
            check.status(client.post(f"/enterprise/oa/leave/{request_id}/submit", headers=h(who)), 200, f"{who} 提交请假")
            if who == "li":
                decided = client.post(f"/enterprise/oa/leave/{request_id}/decide", headers=h("head"), json={"team_id": sales, "action": "approve", "note": "同意"})
                check.status(decided, 200, "负责人批准李四的请假")

        print("\n== 2. 人事导入钉钉风格的考勤 Excel ==")
        rows = []
        rows += [(nm("zh"), MON, "08:55", "18:05"), (nm("zh"), TUE, "09:40", "18:00"), (nm("zh"), WED, "08:58", None)]      # 周二迟到 40 分钟、周三缺下班卡
        rows += [(nm("zh"), SAT, "10:00", "15:00")]                                                                   # 休息日来加班
        rows += [(nm("li"), WED, "08:50", "18:10")]                                                                   # 周一二请假：没有打卡
        rows += [("小王", MON, "08:55", "18:00"), ("小王", TUE, "08:57", "18:02")]                                     # 王五用昵称；周三没有记录
        for n in ("head", "hr", "oh", "oe"):
            rows += [(nm(n), d, "08:50", "18:10") for d in (MON, TUE, WED)]
        content = dingtalk_workbook(rows)
        denied = upload("zh", content)
        check.status(denied, 403, "普通员工不能导入考勤")
        check.status(upload("head", content), 403, "部门负责人也不能导入考勤（只有人事）")
        first = upload("hr", content)
        check.status(first, 200, "人事导入")
        body = first.json()
        check.ok(body["format"] == "daily_summary", "识别为每日汇总格式（自动跳过标题行，合计行不报错）")
        check.ok([x["name"] for x in body["unmatched"]] == ["小王"], "考勤里的昵称“小王”对不上平台账号，被单独列出")
        check.ok(body["skipped_total"] == 0, "标题行和合计行没有被当成数据行")
        again = upload("hr", content, aliases={"小王": uid("wa")})
        check.status(again, 200, "人事选择“小王”对应王五后重新导入")
        check.ok(again.json()["unmatched"] == [] and again.json()["new_punches"] == 4, "只新增昵称那 4 条打卡，其余不重复")
        check.ok(upload("hr", content).json()["new_punches"] == 0, "对应已记住：再次导入不再需要选择，也不产生重复记录")
        check.status(upload("hr", content, aliases={"小王": 99999999}), 400, "对应到企业外的账号被拒绝")

        print("\n== 3. 分析：规则 + 业务系统里已批准的请假 ==")
        check.status(post("/analyze", "zh", start=SAT.isoformat(), end=WED.isoformat()), 403, "员工不能触发分析")
        result = post("/analyze", "hr", start=SAT.isoformat(), end=WED.isoformat())
        check.status(result, 200, "人事分析上周")
        check.ok(result.json()["created"] > 0, f"发现 {result.json()['total_anomalies']} 条异常")
        zh_late = find("zh", TUE, "late")
        check.ok(zh_late and zh_late["detail"]["minutes"] == 40 and zh_late["severity"] == "medium", "张三周二迟到 40 分钟（中）")
        check.ok(find("zh", WED, "missing_out") is not None, "张三周三只有上班卡：缺下班卡")
        check.ok(find("zh", SAT, "rest_day_work") is not None, "张三周六有打卡：休息日出勤")
        check.ok(find("li", MON, "absent") is None and find("li", TUE, "absent") is None, "李四周一周二已批准请假：不算旷工（来自 Java 业务系统）")
        wa_absent = find("wa", WED, "absent")
        check.ok(wa_absent is not None, "王五周三的请假没有批准：算旷工")
        check.ok(find("wa", MON, "late") is None, "昵称对应后王五周一周二的打卡正常使用")
        check.ok(all(not mine(n) for n in ("head", "hr")), "每天正常出勤的人没有异常")
        check.ok(post("/analyze", "hr", start=SAT.isoformat(), end=WED.isoformat()).json()["created"] == 0, "重复分析不会产生重复异常")

        print("\n== 4. 员工说明，负责人 / 人事认定 ==")
        others = get("/anomalies", "zh", view="mine").json()
        check.ok({a["user_id"] for a in others} == {uid("zh")}, "员工只看到自己的异常")
        check.status(client.post(f"/enterprise/attendance/anomalies/{wa_absent['id']}/explain", headers=h("zh"),
                                 json={"team_id": sales, "text": "替他说明"}), 404, "不能替别人说明")
        check.status(client.post(f"/enterprise/attendance/anomalies/{wa_absent['id']}/explain", headers=h("wa"),
                                 json={"team_id": sales, "text": "家里临时有事，已口头请假，忘了补批"}), 200, "王五说明原因")
        check.ok(find("wa", WED, "absent")["status"] == "explained", "状态变为已说明待认定")
        check.status(client.post(f"/enterprise/attendance/anomalies/{wa_absent['id']}/decide", headers=h("oh"),
                                 json={"team_id": ops, "action": "dismiss", "note": "别的部门"}), 404, "别的部门负责人不能认定")
        check.status(client.post(f"/enterprise/attendance/anomalies/{wa_absent['id']}/decide", headers=h("wa"),
                                 json={"team_id": sales, "action": "dismiss", "note": "自己认定"}), (403, 404), "员工不能认定（更不能认定自己的）")
        team_view = get("/anomalies", "head", view="team")
        check.ok(team_view.status_code == 200 and {a["team_id"] for a in team_view.json()} == {sales}, "负责人只看到本部门的异常")
        check.status(get("/anomalies", "zh", view="team"), 403, "普通员工看不到团队视图")
        check.status(get("/anomalies", "oh", view="team"), 200, "别的部门负责人的团队视图是空的")
        check.ok(get("/anomalies", "oh", view="team").json() == [], "别的部门负责人看不到销售部的异常")

        print("\n== 5. 提醒、部门首页、Agent 工具读到同一份事实 ==")
        run_db(ar.run_attendance_explain)
        run_db(ar.run_attendance_decide)
        from service import work_item_service
        zh_items = run_db(lambda s: work_item_service.list_items(s, uid("zh"), "all"))["items"]
        check.ok(any("考勤异常待说明" in i["title"] for i in zh_items), "张三收到“有考勤异常待说明”的提醒")
        head_items = run_db(lambda s: work_item_service.list_items(s, uid("head"), "all"))["items"]
        check.ok(any("等你认定" in i["title"] for i in head_items), "负责人收到“有考勤异常等你认定”的提醒（王五已说明）")
        cards = {c["key"] for c in client.get(f"/enterprise/home?team_id={sales}", headers=h("zh")).json()["cards"]}
        check.ok("att_explain" in cards, "张三的部门首页出现“待说明的考勤异常”卡片")
        head_cards = {c["key"] for c in client.get(f"/enterprise/home?team_id={sales}", headers=h("head")).json()["cards"]}
        check.ok("att_decide" in head_cards, "负责人的首页出现“待认定的考勤异常”卡片")
        from service.tools import attendance as tools
        from service.tools.base import ToolContext
        from unittest.mock import patch

        def tool(cls, who, **kw):
            instance = cls()
            instance.set_context(ToolContext(user_id=uid(who)))
            with patch("service.tools.attendance.hub.resolve_caller_context", return_value={"team_id": team_of[who]}):
                return json.loads(instance.execute(**kw))
        mine_tool = tool(tools.GetMyAttendanceAnomaliesTool, "zh")
        check.ok(len(mine_tool["anomalies"]) == len(mine("zh", status="open,explained")), "Agent 工具里张三的待处理异常与页面一致")
        summary_tool = tool(tools.GetAttendanceSummaryTool, "head", start=SAT.isoformat(), end=WED.isoformat())
        check.ok(summary_tool["by_type"] == get("/summary", "head", start=SAT.isoformat(), end=WED.isoformat()).json()["by_type"], "Agent 汇总与页面汇总一致")
        check.ok(nm("zh") not in json.dumps(summary_tool, ensure_ascii=False), "Agent 汇总不含人名")
        check.ok("只有人事" in tool(tools.GetAttendanceSummaryTool, "zh")["error"], "普通员工用不了汇总工具")

        print("\n== 6. 认定后补录打卡，重新分析 ==")
        confirm = client.post(f"/enterprise/attendance/anomalies/{wa_absent['id']}/decide", headers=h("head"),
                              json={"team_id": sales, "action": "dismiss", "note": "核实确有口头请假，视为正常"})
        check.status(confirm, 200, "负责人认定王五的缺勤为正常")
        check.status(client.post(f"/enterprise/attendance/anomalies/{wa_absent['id']}/decide", headers=h("hr"),
                                 json={"team_id": hr_team, "action": "confirm", "note": "改主意"}), 409, "认定后不能再改")
        zh_missing = find("zh", WED, "missing_out")
        fix = dingtalk_workbook([(nm("zh"), WED, "08:58", "18:03")])
        check.status(upload("hr", fix, name="补录.xlsx"), 200, "人事导入补录的打卡")
        again = post("/analyze", "hr", start=SAT.isoformat(), end=WED.isoformat()).json()
        check.ok(again["cleared"] >= 1, "重新分析：张三周三的缺下班卡已不存在，自动消除")
        check.ok(find("zh", WED, "missing_out")["status"] == "cleared", "该异常变为“系统核对后已消除”")
        check.ok(find("wa", WED, "absent")["status"] == "dismissed", "已认定的异常不受重新分析影响")
        _ = zh_missing

        print("\n== 7. 不属于该部门的人拿不到任何数据 ==")
        check.status(client.get(f"/enterprise/attendance/anomalies?team_id={sales}&view=mine", headers=h("out")), 403, "不属于该部门的人被拒绝")

        print(f"\n全部 {len(check.passed)} 项检查通过")
        if KEEP:
            owner_name = f"kp_at_own_{SUFFIX[:3]}"
            db.execute(text("UPDATE `user` SET name=:n WHERE id=:i"), {"n": owner_name, "i": owner["id"]})
            for n, user in u.items():
                user["name"] = f"kp_at_{n}_{SUFFIX[:3]}"
                db.execute(text("UPDATE `user` SET name=:n WHERE id=:i"), {"n": user["name"], "i": user["id"]})
            db.commit()
            rc._created_user_ids.clear()
            print("\n--keep：保留数据，可在浏览器登录（密码 Passw0rd!Secure）：")
            for n, user in u.items():
                print(f"  {n}: {user['name']}")
    finally:
        if KEEP:
            db.close()
            return
        ids = ",".join(str(x["id"]) for x in u.values())
        with ent.begin() as conn:
            conn.execute(text(f"DELETE FROM leave_request WHERE applicant_user_id IN ({ids})"))
            conn.execute(text(f"DELETE FROM leave_balance WHERE user_id IN ({ids})"))
            conn.execute(text(f"DELETE FROM audit_event WHERE user_id IN ({ids})"))
        db.commit()
        for table in ("attendance_anomaly", "attendance_punch", "attendance_import", "attendance_alias", "attendance_calendar", "attendance_rule"):
            db.execute(text(f"DELETE FROM {table} WHERE organization_id=:o"), {"o": org})
        db.execute(text(f"DELETE FROM work_item WHERE user_id IN ({ids})"))
        db.execute(text(f"DELETE FROM notification WHERE user_id IN ({ids})"))
        db.commit()
        rc.cleanup()
        tids = ",".join(str(t) for t in team_ids)
        db.execute(text(f"DELETE FROM team_members WHERE team_id IN ({tids})"))
        db.execute(text(f"DELETE FROM teams WHERE id IN ({tids})"))
        db.execute(text("DELETE FROM organization_members WHERE organization_id=:o"), {"o": org})
        db.execute(text("DELETE FROM organizations WHERE id=:o"), {"o": org})
        db.commit()
        db.close()


def purge_kept():
    ent, db = enterprise_engine(), SessionLocal()
    try:
        orgs = [r[0] for r in db.execute(text("SELECT id FROM organizations WHERE name LIKE 'e2e-at-%'")).all()]
        teams = [r[0] for r in db.execute(text("SELECT t.id FROM teams t JOIN organizations o ON o.id=t.organization_id WHERE o.name LIKE 'e2e-at-%'")).all()]
        users = [r[0] for r in db.execute(text("SELECT id FROM `user` WHERE name LIKE 'kp_at_%'")).all()]
        if users:
            ids = ",".join(str(x) for x in users)
            with ent.begin() as conn:
                conn.execute(text(f"DELETE FROM leave_request WHERE applicant_user_id IN ({ids})"))
                conn.execute(text(f"DELETE FROM leave_balance WHERE user_id IN ({ids})"))
                conn.execute(text(f"DELETE FROM audit_event WHERE user_id IN ({ids})"))
            db.execute(text(f"DELETE FROM work_item WHERE user_id IN ({ids})"))
            db.execute(text(f"DELETE FROM notification WHERE user_id IN ({ids})"))
            db.commit()
            rc._purge_users("id IN (" + ids + ")")
        for o in orgs:
            for table in ("attendance_anomaly", "attendance_punch", "attendance_import", "attendance_alias", "attendance_calendar", "attendance_rule"):
                db.execute(text(f"DELETE FROM {table} WHERE organization_id=:o"), {"o": o})
        if teams:
            tids = ",".join(str(t) for t in teams)
            db.execute(text(f"DELETE FROM team_members WHERE team_id IN ({tids})"))
            db.execute(text(f"DELETE FROM teams WHERE id IN ({tids})"))
        for o in orgs:
            db.execute(text("DELETE FROM organization_members WHERE organization_id=:o"), {"o": o})
            db.execute(text("DELETE FROM organizations WHERE id=:o"), {"o": o})
        db.commit()
        print(f"已清理：{len(orgs)} 个企业、{len(teams)} 个部门、{len(users)} 个用户")
    finally:
        db.close()


if __name__ == "__main__":
    if "--purge" in sys.argv:
        purge_kept()
    else:
        main()

