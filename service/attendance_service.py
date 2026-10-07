"""考勤异常发现：导入考勤文件 → 对齐员工 → 按规则和已批准的请假、工作日历判断异常 → 员工说明 → 人事/负责人认定。

数据来源贴合实际工作：
- 打卡记录来自考勤设备或钉钉/企业微信等导出的文件（见 attendance_import.py），不是系统里凭空造的；
- 请假来自业务系统里**已批准**的请假单（Java 的 /oa/leave/requests/approved），请假当天缺卡不算旷工；
- 工作日历（法定节假日、调休上班）每年不同，由人事按国务院安排录入，没录入的日期按周一到周五上班；
- 导出文件里的名字常常和平台账号对不上，人事确认一次就记成“名字对应”，下次自动识别。
权限：导入、分析、改规则和日历只有人事；员工看自己的并写说明；部门负责人看本部门并认定（不能认定自己的）；
人事认定任何人的（不能认定自己的）。全程不使用模型，考勤数据不会发给任何模型。
"""
import asyncio
import json
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError

from models.init_db import (AttendanceAlias, AttendanceAnomaly, AttendanceCalendar, AttendanceImport, AttendancePunch, AttendanceRule,
                            OrganizationMember, TeamMember, Team, User)
from service import attendance_import, attendance_rules as rules, audit_service, hr_service
from service.exceptions import Conflict, InvalidInput, NotFound, PermissionDenied
from service.hub_gateway import call_hub
from utils.timeutil import utcnow

MAX_ANALYZE_DAYS = 93
STATUS_LABELS = {"open": "待说明", "explained": "已说明待认定", "confirmed": "已认定为异常", "dismissed": "认定为正常", "cleared": "系统核对后已消除"}
OPEN_STATUSES = ("open", "explained")


async def _actor(db, user_id: int, team_id: int) -> Dict[str, Any]:
    return await hr_service.actor_for(db, user_id, team_id)


def _is_hr(actor) -> bool:
    return "HR" in actor["roles"] or actor["org_admin"]


async def _require_hr(db, user_id: int, team_id: int) -> Dict[str, Any]:
    actor = await _actor(db, user_id, team_id)
    if not _is_hr(actor):
        raise PermissionDenied("只有人事部门成员或企业管理员能做这项操作")
    return actor


async def _org_members(db, organization_id: int) -> Dict[int, str]:
    rows = (await db.execute(select(User.id, User.name).join(OrganizationMember, OrganizationMember.user_id == User.id)
                             .where(OrganizationMember.organization_id == organization_id, OrganizationMember.status == "active"))).all()
    return {int(i): n for i, n in rows}


async def _primary_teams(db, organization_id: int, user_ids: List[int]) -> Dict[int, int]:
    """员工所在的部门（有多个取编号最小的有效部门）。"""
    if not user_ids:
        return {}
    rows = (await db.execute(select(TeamMember.user_id, func.min(TeamMember.team_id)).join(Team, Team.id == TeamMember.team_id)
                             .where(TeamMember.user_id.in_(user_ids), TeamMember.status == "active", Team.status == "active",
                                    Team.organization_id == organization_id).group_by(TeamMember.user_id))).all()
    return {int(u): int(t) for u, t in rows}


async def me(db, user_id: int, team_id: int) -> Dict[str, Any]:
    """页面用：我是不是人事/部门负责人，以及我自己有几条待处理的异常。"""
    actor = await _actor(db, user_id, team_id)
    mine_open = (await db.execute(select(func.count()).where(AttendanceAnomaly.organization_id == actor["organization_id"], AttendanceAnomaly.user_id == user_id,
                                                              AttendanceAnomaly.status == "open"))).scalar() or 0
    scope = [AttendanceAnomaly.organization_id == actor["organization_id"], AttendanceAnomaly.status == "explained", AttendanceAnomaly.user_id != user_id]
    if not _is_hr(actor):
        scope.append(AttendanceAnomaly.team_id.in_(actor["heads"] or [0]))
    to_decide = (await db.execute(select(func.count()).where(*scope))).scalar() or 0 if (_is_hr(actor) or actor["heads"]) else 0
    return {"user_id": user_id, "is_hr": _is_hr(actor), "is_head": bool(actor["heads"]), "open_mine": int(mine_open), "to_decide": int(to_decide)}


# ---------------------------------------------------------------- 导入

async def import_file(db, user_id: int, team_id: int, file_name: str, content: bytes, aliases: Optional[Dict[str, int]] = None) -> Dict[str, Any]:
    actor = await _require_hr(db, user_id, team_id)
    org = actor["organization_id"]
    parsed = await asyncio.to_thread(attendance_import.parse, file_name, content)
    members = await _org_members(db, org)
    by_name = {name: uid for uid, name in members.items()}
    for alias, target in (aliases or {}).items():            # 人事这次手工指定的对应：先校验再保存
        if int(target) not in members:
            raise InvalidInput(f"「{alias}」对应的账号不是本企业的有效成员")
        existing = (await db.execute(select(AttendanceAlias).where(AttendanceAlias.organization_id == org, AttendanceAlias.alias == alias.strip()))).scalar_one_or_none()
        if existing:
            existing.user_id = int(target)
        else:
            db.add(AttendanceAlias(organization_id=org, alias=alias.strip()[:80], user_id=int(target), created_by=user_id))
    await db.flush()
    saved = {a: u for a, u in (await db.execute(select(AttendanceAlias.alias, AttendanceAlias.user_id).where(AttendanceAlias.organization_id == org))).all()}

    def resolve(record) -> Optional[int]:
        for key in (record["name"], record.get("alt")):
            if key and key in saved and saved[key] in members:
                return saved[key]
        for key in (record["name"], record.get("alt")):
            if key and key in by_name:
                return by_name[key]
        return None

    unmatched: Dict[str, int] = {}
    wanted: Dict[int, set] = {}
    matched_rows = 0
    for record in parsed["records"]:
        uid = resolve(record)
        if uid is None:
            unmatched[record["name"]] = unmatched.get(record["name"], 0) + 1
            continue
        matched_rows += 1
        wanted.setdefault(uid, set()).update(record["punches"])
    total_punches = sum(len(v) for v in wanted.values())
    new_count = 0
    if wanted:
        lows, highs = min(p for v in wanted.values() for p in v), max(p for v in wanted.values() for p in v)
        have = {(u, p) for u, p in (await db.execute(select(AttendancePunch.user_id, AttendancePunch.punch_at).where(
            AttendancePunch.organization_id == org, AttendancePunch.punch_at >= lows, AttendancePunch.punch_at <= highs,
            AttendancePunch.user_id.in_(list(wanted))))).all()}
    start, end = parsed["period"]
    record = AttendanceImport(organization_id=org, uploaded_by=user_id, file_name=file_name[:255], source_format=parsed["format"],
                              period_start=datetime.combine(start, datetime.min.time()) if start else None,
                              period_end=datetime.combine(end, datetime.min.time()) if end else None, row_count=parsed["row_count"],
                              punch_count=total_punches, unmatched_json=json.dumps(
                                  [{"name": n, "rows": c} for n, c in sorted(unmatched.items(), key=lambda x: -x[1])], ensure_ascii=False),
                              skipped_json=json.dumps(parsed["skipped"], ensure_ascii=False))
    db.add(record)
    await db.flush()
    for uid, stamps in wanted.items():
        for stamp in sorted(stamps):
            if (uid, stamp) not in have:
                db.add(AttendancePunch(organization_id=org, user_id=uid, punch_at=stamp, import_id=record.id))
                new_count += 1
    record.new_punch_count = new_count
    await db.commit()
    await audit_service.record_async(user_id, "attendance.import", resource_type="attendance_import", resource_id=record.id,
                                     detail={"format": parsed["format"], "rows": parsed["row_count"], "new_punches": new_count})
    return {"import_id": record.id, "format": parsed["format"], "format_label": "逐条打卡" if parsed["format"] == "punch_rows" else "每日汇总",
            "period": [start.isoformat() if start else None, end.isoformat() if end else None], "rows": parsed["row_count"],
            "matched_people": len(wanted), "matched_rows": matched_rows,
            "no_records": sorted(n for u, n in members.items() if u not in wanted)[:100], "no_records_total": sum(1 for u in members if u not in wanted), "punches": total_punches, "new_punches": new_count,
            "duplicate_punches": total_punches - new_count, "unmatched": [{"name": n, "rows": c} for n, c in sorted(unmatched.items(), key=lambda x: -x[1])],
            "skipped": parsed["skipped"][:50], "skipped_total": parsed["skipped_total"]}


async def list_imports(db, user_id: int, team_id: int, limit: int = 20) -> List[Dict[str, Any]]:
    actor = await _require_hr(db, user_id, team_id)
    rows = (await db.execute(select(AttendanceImport).where(AttendanceImport.organization_id == actor["organization_id"])
                             .order_by(AttendanceImport.id.desc()).limit(limit))).scalars().all()
    return [{"id": r.id, "file_name": r.file_name, "format": r.source_format, "rows": r.row_count, "punches": r.punch_count, "new_punches": r.new_punch_count,
             "unmatched": len(json.loads(r.unmatched_json or "[]")), "period": [r.period_start and r.period_start.date().isoformat(), r.period_end and r.period_end.date().isoformat()],
             "created_at": r.created_at.isoformat() + "Z"} for r in rows]


async def members_for_mapping(db, user_id: int, team_id: int) -> List[Dict[str, Any]]:
    actor = await _require_hr(db, user_id, team_id)
    return [{"user_id": u, "name": n} for u, n in sorted((await _org_members(db, actor["organization_id"])).items(), key=lambda x: x[1])]


# ---------------------------------------------------------------- 规则与日历

async def get_rules(db, user_id: int, team_id: int) -> Dict[str, Any]:
    actor = await _require_hr(db, user_id, team_id)
    rows = (await db.execute(select(AttendanceRule).where(AttendanceRule.organization_id == actor["organization_id"]))).scalars().all()
    shape = lambda r: {"team_id": r.team_id, "work_start": r.work_start, "work_end": r.work_end, "grace_minutes": r.grace_minutes}   # noqa: E731
    default = next((shape(r) for r in rows if r.team_key == 0), {"team_id": None, **rules.DEFAULT_RULE})
    from service.name_lookup import team_names
    names = await team_names(db, actor["scope"])
    return {"default": default, "teams": [shape(r) for r in rows if r.team_key != 0],
            "org_teams": [{"id": t, "name": names.get(t, str(t))} for t in actor["scope"]]}       # 人事要能给任何部门设规则，不只是自己所在的部门


async def set_rule(db, user_id: int, team_id: int, for_team: Optional[int], work_start: str, work_end: str, grace_minutes: int) -> Dict[str, Any]:
    actor = await _require_hr(db, user_id, team_id)
    try:
        start, end, grace = rules.validate_rule(work_start, work_end, grace_minutes)
    except ValueError as exc:
        raise InvalidInput(str(exc)) from None
    if for_team is not None and for_team not in actor["scope"]:
        raise NotFound("部门不存在")
    key = for_team or 0
    row = (await db.execute(select(AttendanceRule).where(AttendanceRule.organization_id == actor["organization_id"], AttendanceRule.team_key == key))).scalar_one_or_none()
    if row is None:
        row = AttendanceRule(organization_id=actor["organization_id"], team_id=for_team, team_key=key)
        db.add(row)
    row.work_start, row.work_end, row.grace_minutes, row.updated_by = start, end, grace, user_id
    await db.commit()
    await audit_service.record_async(user_id, "attendance.rule_changed", resource_type="team", resource_id=for_team or 0, detail={"start": start, "end": end, "grace": grace})
    return await get_rules(db, user_id, team_id)


KIND_ALIASES = {"上班": "workday", "调休上班": "workday", "补班": "workday", "workday": "workday", "休息": "rest", "休息日": "rest", "rest": "rest",
                "节假日": "holiday", "法定节假日": "holiday", "放假": "holiday", "holiday": "holiday"}


def parse_calendar_text(text: str) -> List[Dict[str, Any]]:
    """每行“日期,类型,备注”：类型写 上班（调休补班）/ 休息 / 节假日。支持逗号、制表符分隔，可有标题行。"""
    items, errors = [], []
    for number, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.replace("\t", ",").replace("，", ",").split(",")]
        day = attendance_import.parse_datetime(parts[0])
        if day is None:
            if number == 1:
                continue                    # 标题行
            errors.append(f"第 {number} 行日期无法识别：{parts[0]}")
            continue
        kind = KIND_ALIASES.get(parts[1].strip()) if len(parts) > 1 else None
        if kind is None:
            errors.append(f"第 {number} 行类型应为 上班 / 休息 / 节假日：{parts[1] if len(parts) > 1 else '（空）'}")
            continue
        items.append({"day": day.date(), "kind": kind, "note": (parts[2] if len(parts) > 2 else "")[:60] or None})
    if errors:
        raise InvalidInput("；".join(errors[:5]) + (f"（共 {len(errors)} 处）" if len(errors) > 5 else ""))
    if not items:
        raise InvalidInput("没有可导入的日期")
    if len(items) > 400:
        raise InvalidInput("一次最多导入 400 天")
    return items


async def import_calendar(db, user_id: int, team_id: int, text: str) -> Dict[str, Any]:
    actor = await _require_hr(db, user_id, team_id)
    items = parse_calendar_text(text)
    for item in items:
        stamp = datetime.combine(item["day"], datetime.min.time())
        row = (await db.execute(select(AttendanceCalendar).where(AttendanceCalendar.organization_id == actor["organization_id"], AttendanceCalendar.day == stamp))).scalar_one_or_none()
        if row is None:
            db.add(AttendanceCalendar(organization_id=actor["organization_id"], day=stamp, kind=item["kind"], note=item["note"]))
        else:
            row.kind, row.note = item["kind"], item["note"]
    await db.commit()
    await audit_service.record_async(user_id, "attendance.calendar_import", resource_type="organization", resource_id=actor["organization_id"], detail={"days": len(items)})
    return {"imported": len(items)}


async def get_calendar(db, user_id: int, team_id: int, start: date, end: date) -> List[Dict[str, Any]]:
    actor = await _require_hr(db, user_id, team_id)
    rows = (await db.execute(select(AttendanceCalendar).where(AttendanceCalendar.organization_id == actor["organization_id"],
                                                              AttendanceCalendar.day >= datetime.combine(start, datetime.min.time()),
                                                              AttendanceCalendar.day <= datetime.combine(end, datetime.min.time())).order_by(AttendanceCalendar.day))).scalars().all()
    return [{"day": r.day.date().isoformat(), "kind": r.kind, "note": r.note} for r in rows]


# ---------------------------------------------------------------- 分析

async def _approved_leaves(db, user_id: int, team_id: int, actor, start: date, end: date) -> Dict[int, Dict[date, Dict[str, Any]]]:
    """已批准请假：{用户: {日期: 请假信息}}。读不到业务系统时直接报错——宁可不分析，也不能在不知道谁请假的情况下把请假的人判成旷工。"""
    path = hr_service.scoped("/oa/leave/requests/approved", {"scope": actor["scope"], "roles": [], "heads": []}, **{"from": start.isoformat(), "to": end.isoformat()})
    rows = await call_hub("GET", path, user_id, team_id, ["oa.leave.read"], "attendance_approved_leaves")
    result: Dict[int, Dict[date, Dict[str, Any]]] = {}
    for row in rows:
        s, e = date.fromisoformat(row["startDate"]), date.fromisoformat(row["endDate"])
        day = max(s, start)
        while day <= min(e, end):
            result.setdefault(int(row["applicantUserId"]), {})[day] = {"type": row["leaveTypeCode"], "from": row["startDate"], "to": row["endDate"]}
            day += timedelta(days=1)
    return result


async def analyze(db, user_id: int, team_id: int, start: date, end: date) -> Dict[str, Any]:
    actor = await _require_hr(db, user_id, team_id)
    org = actor["organization_id"]
    today = (utcnow() + timedelta(hours=8)).date()
    end = min(end, today - timedelta(days=1))          # 当天还没结束，不判断
    if end < start:
        raise InvalidInput("没有可分析的日期（当天及以后的日期不判断）")
    if (end - start).days + 1 > MAX_ANALYZE_DAYS:
        raise InvalidInput(f"一次最多分析 {MAX_ANALYZE_DAYS} 天")
    low, high = datetime.combine(start, datetime.min.time()), datetime.combine(end + timedelta(days=1), datetime.min.time())
    punch_rows = (await db.execute(select(AttendancePunch.user_id, AttendancePunch.punch_at).where(
        AttendancePunch.organization_id == org, AttendancePunch.punch_at >= low, AttendancePunch.punch_at < high))).all()
    punches: Dict[int, Dict[date, List[datetime]]] = {}
    for uid, stamp in punch_rows:
        punches.setdefault(int(uid), {}).setdefault(stamp.date(), []).append(stamp)
    covered = sorted(punches)
    if not covered:
        raise InvalidInput("这个区间里没有打卡记录，请先导入考勤文件")
    members = await _org_members(db, org)
    covered = [u for u in covered if u in members]           # 已离职/停用的人不再产生异常
    leaves = await _approved_leaves(db, user_id, team_id, actor, start, end)
    calendar = {r.day.date(): r.kind for r in (await db.execute(select(AttendanceCalendar).where(
        AttendanceCalendar.organization_id == org, AttendanceCalendar.day >= low, AttendanceCalendar.day < high))).scalars().all()}
    rule_rows = (await db.execute(select(AttendanceRule).where(AttendanceRule.organization_id == org))).scalars().all()
    default_rule = next(({"work_start": r.work_start, "work_end": r.work_end, "grace_minutes": r.grace_minutes} for r in rule_rows if r.team_key == 0), rules.DEFAULT_RULE)
    team_rule = {r.team_key: {"work_start": r.work_start, "work_end": r.work_end, "grace_minutes": r.grace_minutes} for r in rule_rows if r.team_key != 0}
    teams = await _primary_teams(db, org, covered)

    produced: Dict[tuple, Dict[str, Any]] = {}
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    for uid in covered:
        rule = team_rule.get(teams.get(uid, 0), default_rule)
        for day in days:
            kind = calendar.get(day) or rules.default_day_kind(day)
            leave = leaves.get(uid, {}).get(day)
            for item in rules.evaluate_day(day, punches.get(uid, {}).get(day, []), rule, kind, leave is not None, leave):
                produced[(uid, day, item["type"])] = item
    existing = (await db.execute(select(AttendanceAnomaly).where(
        AttendanceAnomaly.organization_id == org, AttendanceAnomaly.user_id.in_(covered),
        AttendanceAnomaly.work_date >= low, AttendanceAnomaly.work_date < high))).scalars().all()
    existing_by = {(a.user_id, a.work_date.date(), a.type): a for a in existing}
    created = updated = cleared = 0
    for key, item in produced.items():
        uid, day, kind_ = key
        row = existing_by.get(key)
        detail = json.dumps(item["detail"], ensure_ascii=False)
        if row is None:
            db.add(AttendanceAnomaly(organization_id=org, team_id=teams.get(uid), user_id=uid, work_date=datetime.combine(day, datetime.min.time()),
                                     type=kind_, severity=item["severity"], detail_json=detail, status="open"))
            created += 1
        elif row.status == "cleared":                    # 之前消除了，现在又出现（比如撤销了补录）：重新打开
            row.status, row.detail_json, row.severity, row.team_id = "open", detail, item["severity"], teams.get(uid)
            created += 1
        elif row.status in OPEN_STATUSES and row.detail_json != detail:
            row.detail_json, row.severity = detail, item["severity"]
            updated += 1
    for key, row in existing_by.items():                 # 补录了打卡、请假批准了，原来的异常不再成立：系统核对后消除（已人工认定的不动）
        if key not in produced and row.status in OPEN_STATUSES:
            row.status, row.decision_note, row.decided_at = "cleared", "重新分析后异常已不存在（已补录打卡或请假已批准）", utcnow()
            cleared += 1
    await db.commit()
    await audit_service.record_async(user_id, "attendance.analyze", resource_type="organization", resource_id=org,
                                     detail={"from": start.isoformat(), "to": end.isoformat(), "created": created, "cleared": cleared})
    covered_set = set(covered)
    uncovered = sorted(n for u, n in members.items() if u not in covered_set)      # 整个区间一条打卡都没有：可能是文件没导全，不判旷工，单独列出让人事核对
    return {"from": start.isoformat(), "to": end.isoformat(), "people": len(covered), "days": len(days), "created": created, "updated": updated,
            "uncovered": uncovered[:100], "uncovered_total": len(uncovered),
            "cleared": cleared, "total_anomalies": len(produced)}


# ---------------------------------------------------------------- 查看、说明、认定

def _row(a: AttendanceAnomaly, names: Dict[int, str], team_names: Dict[int, str]) -> Dict[str, Any]:
    return {"id": a.id, "user_id": a.user_id, "user_name": names.get(a.user_id), "team_id": a.team_id, "team_name": team_names.get(a.team_id),
            "work_date": a.work_date.date().isoformat(), "type": a.type, "type_label": rules.TYPE_LABELS.get(a.type, a.type), "severity": a.severity,
            "severity_label": rules.SEVERITY_LABELS.get(a.severity, a.severity), "detail": json.loads(a.detail_json), "status": a.status,
            "status_label": STATUS_LABELS.get(a.status, a.status), "explanation": a.explanation, "decision_note": a.decision_note,
            "decided_by": a.decided_by, "explained_at": a.explained_at and a.explained_at.isoformat() + "Z",
            "decided_at": a.decided_at and a.decided_at.isoformat() + "Z"}


async def _decorate(db, rows: List[AttendanceAnomaly]) -> List[Dict[str, Any]]:
    from service.name_lookup import team_names, user_names
    names = await user_names(db, [r.user_id for r in rows] + [r.decided_by for r in rows if r.decided_by])
    teams = await team_names(db, [r.team_id for r in rows])
    result = [_row(r, names, teams) for r in rows]
    for item in result:
        item["decided_by_name"] = names.get(item["decided_by"])
    return result


async def list_anomalies(db, user_id: int, team_id: int, view: str = "mine", status: Optional[str] = None, start: Optional[date] = None,
                         end: Optional[date] = None, limit: int = 200) -> List[Dict[str, Any]]:
    actor = await _actor(db, user_id, team_id)
    query = select(AttendanceAnomaly).where(AttendanceAnomaly.organization_id == actor["organization_id"])
    if view == "mine":
        query = query.where(AttendanceAnomaly.user_id == user_id)
    elif view == "team":
        if _is_hr(actor):
            pass
        elif actor["heads"]:
            query = query.where(AttendanceAnomaly.team_id.in_(actor["heads"]))
        else:
            raise PermissionDenied("只有人事或部门负责人能查看团队的考勤异常")
    else:
        raise InvalidInput("未知的视图")
    if status:
        query = query.where(AttendanceAnomaly.status.in_([s for s in status.split(",") if s]))
    if start:
        query = query.where(AttendanceAnomaly.work_date >= datetime.combine(start, datetime.min.time()))
    if end:
        query = query.where(AttendanceAnomaly.work_date <= datetime.combine(end, datetime.min.time()))
    rows = (await db.execute(query.order_by(AttendanceAnomaly.work_date.desc(), AttendanceAnomaly.id.desc()).limit(max(1, min(limit, 500))))).scalars().all()
    return await _decorate(db, rows)


async def explain(db, user_id: int, team_id: int, anomaly_id: int, text: str) -> Dict[str, Any]:
    actor = await _actor(db, user_id, team_id)
    row = (await db.execute(select(AttendanceAnomaly).where(AttendanceAnomaly.id == anomaly_id,
                                                            AttendanceAnomaly.organization_id == actor["organization_id"]).with_for_update())).scalar_one_or_none()
    if row is None or row.user_id != user_id:
        raise NotFound("异常记录不存在")          # 只能说明自己的；别人的按不存在处理
    if row.status not in OPEN_STATUSES:
        raise Conflict("这条异常已经处理完了，不能再修改说明")
    text = (text or "").strip()
    if len(text) < 2:
        raise InvalidInput("请写明原因（比如忘打卡、外出办事、设备故障）")
    row.explanation, row.explained_at, row.status = text[:500], utcnow(), "explained"
    await db.commit()
    return (await _decorate(db, [row]))[0]


async def decide(db, user_id: int, team_id: int, anomaly_id: int, action: str, note: str) -> Dict[str, Any]:
    if action not in ("confirm", "dismiss"):
        raise InvalidInput("操作应为 confirm（认定为异常）或 dismiss（认定为正常）")
    note = (note or "").strip()
    if len(note) < 2:
        raise InvalidInput("请写明认定理由")
    actor = await _actor(db, user_id, team_id)
    row = (await db.execute(select(AttendanceAnomaly).where(AttendanceAnomaly.id == anomaly_id,
                                                            AttendanceAnomaly.organization_id == actor["organization_id"]).with_for_update())).scalar_one_or_none()
    if row is None:
        raise NotFound("异常记录不存在")
    if not _is_hr(actor) and row.team_id not in actor["heads"]:
        raise NotFound("异常记录不存在")
    if row.user_id == user_id:
        raise PermissionDenied("不能认定自己的考勤异常，请由人事或你的部门负责人处理")
    if row.status not in OPEN_STATUSES:
        raise Conflict("这条异常已经处理完了")
    row.status = "confirmed" if action == "confirm" else "dismissed"
    row.decided_by, row.decision_note, row.decided_at = user_id, note[:500], utcnow()
    await db.commit()
    await audit_service.record_async(user_id, f"attendance.{action}", resource_type="attendance_anomaly", resource_id=anomaly_id)
    return (await _decorate(db, [row]))[0]


async def summary(db, user_id: int, team_id: int, start: date, end: date) -> Dict[str, Any]:
    actor = await _actor(db, user_id, team_id)
    if not _is_hr(actor) and not actor["heads"]:
        raise PermissionDenied("只有人事或部门负责人能查看考勤汇总")
    low, high = datetime.combine(start, datetime.min.time()), datetime.combine(end, datetime.min.time())
    scope = [AttendanceAnomaly.organization_id == actor["organization_id"], AttendanceAnomaly.work_date >= low, AttendanceAnomaly.work_date <= high]
    if not _is_hr(actor):
        scope.append(AttendanceAnomaly.team_id.in_(actor["heads"]))
    by_type = {t: int(n) for t, n in (await db.execute(select(AttendanceAnomaly.type, func.count()).where(*scope, AttendanceAnomaly.status != "cleared")
                                                       .group_by(AttendanceAnomaly.type))).all()}
    by_status = {s: int(n) for s, n in (await db.execute(select(AttendanceAnomaly.status, func.count()).where(*scope).group_by(AttendanceAnomaly.status))).all()}
    cutoff = utcnow() - timedelta(days=2)
    stale = (await db.execute(select(func.count()).where(*scope, AttendanceAnomaly.status == "open", AttendanceAnomaly.created_at < cutoff))).scalar() or 0
    waiting = (await db.execute(select(func.count()).where(*scope, AttendanceAnomaly.status == "explained"))).scalar() or 0
    people = (await db.execute(select(func.count(func.distinct(AttendanceAnomaly.user_id))).where(*scope, AttendanceAnomaly.status != "cleared"))).scalar() or 0
    data = {"from": start.isoformat(), "to": end.isoformat(), "by_type": by_type, "by_status": by_status, "people_affected": int(people),
            "unexplained_over_2_days": int(stale), "waiting_decision": int(waiting)}
    data["narrative"] = narrative(data)
    return data


def narrative(data: Dict[str, Any]) -> str:
    """只陈述事实：多少条、哪几类、多少在等处理；不点名、不评价。"""
    total = sum(data["by_type"].values())
    if not total:
        return f"{data['from']} 至 {data['to']} 没有考勤异常。"
    kinds = "、".join(f"{rules.TYPE_LABELS.get(t, t)} {n} 条" for t, n in sorted(data["by_type"].items(), key=lambda x: -x[1]))
    parts = [f"{data['from']} 至 {data['to']} 共 {total} 条考勤异常，涉及 {data['people_affected']} 人：{kinds}。"]
    if data["unexplained_over_2_days"]:
        parts.append(f"{data['unexplained_over_2_days']} 条超过 2 天员工还没有说明。")
    if data["waiting_decision"]:
        parts.append(f"{data['waiting_decision']} 条员工已说明，等待人事或部门负责人认定。")
    return "".join(parts)
