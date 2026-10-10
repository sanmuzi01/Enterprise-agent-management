"""考勤异常 Agent 工具（只读）：员工查自己的考勤异常；人事/部门负责人查汇总。

刻意没有：导入考勤文件、分析、改规则和日历、写说明、认定——这些必须由人在工作台里操作（尤其是认定，
会影响员工，必须有人负责）。汇总只给数量和类型，不给具体人名；Agent 不评价员工态度，也不据此推测绩效。
"""
import json
from datetime import date, datetime, timedelta

from sqlalchemy import func, select

from service import attendance_rules as rules
from service import attendance_service as svc
from service import enterprise_hub_client as hub
from service import hr_service as hr
from service.exceptions import AppError
from service.tools.base import BaseTool, ToolRegistry
from utils.timeutil import utcnow


def _fail(message) -> str:
    return json.dumps({"error": message}, ensure_ascii=False)


def _identity(tool):
    ctx = tool._ctx
    if not ctx or not ctx.user_id:
        raise ValueError("缺少用户上下文，无法查询考勤")
    auth = hub.resolve_caller_context(ctx.user_id, ctx.agent_id)
    if auth["team_id"] is None:
        raise ValueError("当前用户不属于任何部门，无法查询考勤")
    return ctx.user_id, auth["team_id"]


def _day(value, fallback: date) -> date:
    try:
        return date.fromisoformat(str(value)) if value else fallback
    except ValueError:
        raise ValueError(f"日期格式应为 YYYY-MM-DD：{value}") from None


@ToolRegistry.register
class GetMyAttendanceAnomaliesTool(BaseTool):
    requires_context = True
    risk_level = "read"

    def get_name(self) -> str:
        return "get_my_attendance_anomalies"

    def get_description(self) -> str:
        return ("查看我自己的考勤异常（迟到、早退、缺卡、旷工、休息日出勤等）：日期、类型、打卡时间、当前状态和我写过的说明。"
                "异常由系统按打卡文件、请假和工作日历判断；要说明原因请到工作台「考勤」里写，Agent 不能代写说明，也不能认定。")

    def get_parameters(self) -> dict:
        return {"type": "object", "properties": {"only_pending": {"type": "boolean", "description": "只看还没处理完的（默认 true）"}}}

    def execute(self, **kwargs) -> str:
        from models.init_db import AttendanceAnomaly, SessionLocal
        try:
            user_id, team_id = _identity(self)
            hr.actor_for_sync(user_id, team_id)                  # 校验成员身份与企业范围
            pending_only = kwargs.get("only_pending", True) is not False
            db = SessionLocal()
            try:
                query = select(AttendanceAnomaly).where(AttendanceAnomaly.user_id == user_id)
                if pending_only:
                    query = query.where(AttendanceAnomaly.status.in_(svc.OPEN_STATUSES))
                rows = db.execute(query.order_by(AttendanceAnomaly.work_date.desc()).limit(60)).scalars().all()
                result = [{"id": r.id, "date": r.work_date.date().isoformat(), "type": rules.TYPE_LABELS.get(r.type, r.type),
                           "severity": rules.SEVERITY_LABELS.get(r.severity, r.severity), "detail": json.loads(r.detail_json),
                           "status": svc.STATUS_LABELS.get(r.status, r.status), "my_explanation": r.explanation, "decision_note": r.decision_note}
                          for r in rows]
            finally:
                db.close()
        except ValueError as exc:
            return _fail(str(exc))
        except AppError as exc:
            return _fail(exc.message)
        if not result:
            return json.dumps({"anomalies": [], "note": "没有需要处理的考勤异常" if pending_only else "没有考勤异常记录"}, ensure_ascii=False)
        return json.dumps({"anomalies": result, "note": "要说明原因请到「部门工作台 → 考勤」填写"}, ensure_ascii=False)


@ToolRegistry.register
class GetAttendanceSummaryTool(BaseTool):
    requires_context = True
    risk_level = "read"

    def get_name(self) -> str:
        return "get_attendance_summary"

    def get_description(self) -> str:
        return ("人事或部门负责人查看考勤异常汇总：各类异常数量、多少超过 2 天员工没说明、多少在等认定。人事看全企业，负责人看自己的部门。"
                "只给数量不给人名；具体的人和认定请到工作台「考勤」里处理。")

    def get_parameters(self) -> dict:
        return {"type": "object", "properties": {"start": {"type": "string", "description": "开始日期 YYYY-MM-DD，默认 30 天前"},
                                                 "end": {"type": "string", "description": "结束日期 YYYY-MM-DD，默认今天"}}}

    def execute(self, **kwargs) -> str:
        from models.init_db import AttendanceAnomaly, SessionLocal
        try:
            user_id, team_id = _identity(self)
            actor = hr.actor_for_sync(user_id, team_id)
            hr_role = "HR" in actor["roles"] or actor["org_admin"]
            if not hr_role and not actor["heads"]:
                return _fail("只有人事或部门负责人能查看考勤汇总")
            today = (utcnow() + timedelta(hours=8)).date()
            start, end = _day(kwargs.get("start"), today - timedelta(days=30)), _day(kwargs.get("end"), today)
            if end < start or (end - start).days > 366:
                return _fail("日期区间无效（最长一年）")
            low, high = datetime.combine(start, datetime.min.time()), datetime.combine(end, datetime.min.time())
            scope = [AttendanceAnomaly.organization_id == actor["organization_id"], AttendanceAnomaly.work_date >= low, AttendanceAnomaly.work_date <= high]
            if not hr_role:
                scope.append(AttendanceAnomaly.team_id.in_(actor["heads"]))
            db = SessionLocal()
            try:
                by_type = {t: int(n) for t, n in db.execute(select(AttendanceAnomaly.type, func.count()).where(*scope, AttendanceAnomaly.status != "cleared")
                                                           .group_by(AttendanceAnomaly.type)).all()}
                by_status = {s: int(n) for s, n in db.execute(select(AttendanceAnomaly.status, func.count()).where(*scope).group_by(AttendanceAnomaly.status)).all()}
                people = db.execute(select(func.count(func.distinct(AttendanceAnomaly.user_id))).where(*scope, AttendanceAnomaly.status != "cleared")).scalar() or 0
                stale = db.execute(select(func.count()).where(*scope, AttendanceAnomaly.status == "open",
                                                              AttendanceAnomaly.created_at < utcnow() - timedelta(days=2))).scalar() or 0
            finally:
                db.close()
        except ValueError as exc:
            return _fail(str(exc))
        except AppError as exc:
            return _fail(exc.message)
        data = {"from": start.isoformat(), "to": end.isoformat(), "by_type": by_type, "by_status": by_status, "people_affected": int(people),
                "unexplained_over_2_days": int(stale), "waiting_decision": by_status.get("explained", 0)}
        data["type_labels"] = {t: rules.TYPE_LABELS.get(t, t) for t in by_type}
        data["narrative"] = svc.narrative(data)
        return json.dumps(data, ensure_ascii=False)
