"""试点开始前的预检：不是演示就绪（见 readiness.py），而是“这套环境能不能拿来做真实试点”。

和演示的区别：试点必须用真实模型（离线演示模型是规则抽取，会让采纳率和节省工时都失真）；要有备份；
没有堆积的死信和严重问题；参与的人都连了模型；部门业务类型、负责人、人事成员齐全；考勤的工作日历录过。
每项独立检查、只读、不改数据，返回 {ok, level, message, fix}；error 会阻止开始，warn 是提醒你知道这个风险。
"""
import os
import pathlib
from datetime import timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select, text

from utils.timeutil import utcnow

ROOT = pathlib.Path(__file__).resolve().parent.parent
MIN_PEOPLE = 5            # 与 pilot-plan.md、试点报告的“样本不足”口径一致
BACKUP_MAX_AGE_HOURS = 36


def _item(key: str, label: str, level: str, message: str, fix: str = "") -> Dict[str, Any]:
    return {"key": key, "label": label, "level": level, "ok": level != "error", "message": message, "fix": fix}


def check_environment() -> List[Dict[str, Any]]:
    from service import readiness
    items = []
    for fn in (readiness._database, readiness._migration, readiness._enterprise_hub):
        raw = fn()
        items.append(_item(raw["key"], raw["label"], "ok" if raw["ok"] else "error", raw["message"], raw["fix"]))
    from service.llm import offline_demo
    if offline_demo.enabled():
        items.append(_item("real_model", "真实模型", "error", "当前启用的是离线演示模型（规则抽取）：采纳率、节省工时和满意度都不能代表真实效果",
                           "去掉 OFFLINE_DEMO_MODEL=1，让试点员工连接真实模型"))
    else:
        items.append(_item("real_model", "真实模型", "ok", "未启用离线演示模型"))
    app_env = (os.getenv("APP_ENV") or "").lower()
    items.append(_item("app_env", "运行环境", "ok" if app_env in ("production", "prod") else "warn",
                       f"APP_ENV={app_env or '未设置'}" + ("" if app_env in ("production", "prod") else "：不是生产配置，登录限流、HTTPS、受信主机等生产校验没有启用"),
                       "" if app_env in ("production", "prod") else "试点面向真实员工时设置 APP_ENV=production（见 docs/deployment.md）"))
    reminders = os.getenv("REMINDERS_ENABLED", "1").strip().lower() not in ("0", "false", "no")
    items.append(_item("reminders", "主动提醒", "ok" if reminders else "warn", "已启用" if reminders else "已关闭：员工不会收到待说明、待验收等提醒",
                       "" if reminders else "去掉 REMINDERS_ENABLED=0"))
    runner = os.getenv("OUTBOX_RUNNER", "1").strip().lower() not in ("0", "false", "no")
    items.append(_item("outbox_runner", "事件发布器", "ok" if runner else "error", "已启用" if runner else "已关闭：批量整理和问题通知不会被处理，事件只会堆积",
                       "" if runner else "去掉 OUTBOX_RUNNER=0"))
    return items


def check_operations(db) -> List[Dict[str, Any]]:
    from models.init_db import DeadLetter, OutboxEvent, SystemIssue
    items = []
    backups = sorted((ROOT / "backups").glob("db_*.sql"), key=lambda p: p.stat().st_mtime) if (ROOT / "backups").is_dir() else []
    if not backups:
        items.append(_item("backup", "数据库备份", "error", "没有找到任何备份", "运行 .venv\\Scripts\\python.exe scripts\\backup.py，并配置每天定时执行"))
    else:
        age = (utcnow().timestamp() - backups[-1].stat().st_mtime) / 3600
        items.append(_item("backup", "数据库备份", "ok" if age <= BACKUP_MAX_AGE_HOURS else "warn", f"最近一次备份在 {age:.0f} 小时前（{backups[-1].name}）",
                           "" if age <= BACKUP_MAX_AGE_HOURS else "再备份一次，并配置每天定时执行 scripts\\backup.py"))
    dead = db.execute(select(func.count()).select_from(DeadLetter).where(DeadLetter.status == "pending")).scalar() or 0
    items.append(_item("dead_letters", "死信队列", "ok" if not dead else "error", "没有待处理的死信" if not dead else f"有 {dead} 条重试用尽的事件等待处理",
                       "" if not dead else "管理后台 → 问题中心 → 事件与死信：修复后重新投递，或写明原因丢弃"))
    stuck = db.execute(select(func.count()).select_from(OutboxEvent).where(OutboxEvent.published_at.is_(None), OutboxEvent.created_at < utcnow() - timedelta(minutes=10))).scalar() or 0
    items.append(_item("outbox_backlog", "发件箱堆积", "ok" if not stuck else "warn", "没有堆积" if not stuck else f"有 {stuck} 条事件超过 10 分钟还没发布",
                       "" if not stuck else "确认事件发布器在运行（OUTBOX_RUNNER）"))
    active = [SystemIssue.status.in_(("OPEN", "ACKNOWLEDGED", "INVESTIGATING", "MITIGATED", "REGRESSED"))]
    serious = db.execute(select(func.count()).select_from(SystemIssue).where(*active, SystemIssue.severity.in_(("high", "critical")))).scalar() or 0
    items.append(_item("open_issues", "未关闭的严重问题", "ok" if not serious else "warn", "没有" if not serious else f"有 {serious} 个高/严重级别的问题还没解决",
                       "" if not serious else "试点前在问题中心处理或明确接受这些风险"))
    return items


def check_roster(db, org_id: int) -> List[Dict[str, Any]]:
    """试点范围：企业里的成员、部门、模型连接、负责人、人事成员。"""
    from models.init_db import (Agent, AttendanceCalendar, AttendanceRule, LLMConfig, OrganizationMember, Team, TeamMember)
    items = []
    org = db.execute(text("SELECT name FROM organizations WHERE id=:o AND status='active'"), {"o": org_id}).first()
    if org is None:
        return [_item("org", "试点企业", "error", f"企业 {org_id} 不存在或已停用", "确认 --org 参数")]
    members = [r[0] for r in db.execute(select(OrganizationMember.user_id).where(OrganizationMember.organization_id == org_id, OrganizationMember.status == "active")).all()]
    items.append(_item("members", "参与人数", "ok" if len(members) >= MIN_PEOPLE else "warn", f"{org[0]}：{len(members)} 位有效成员"
                       + ("" if len(members) >= MIN_PEOPLE else f"，少于 {MIN_PEOPLE} 人，试点报告会提示样本不足"),
                       "" if len(members) >= MIN_PEOPLE else "试点建议 5–10 人、覆盖 2–3 个部门"))
    teams = db.execute(select(Team.id, Team.name, Team.department_code).where(Team.organization_id == org_id, Team.status == "active")).all()
    populated = []
    for team_id, name, code in teams:
        count = db.execute(select(func.count()).select_from(TeamMember).where(TeamMember.team_id == team_id, TeamMember.status == "active")).scalar() or 0
        if count:
            populated.append((team_id, name, code, count))
    items.append(_item("departments", "覆盖部门", "ok" if len(populated) >= 2 else "warn", f"{len(populated)} 个部门有成员：" + "、".join(f"{n}({c})" for _, n, _, c in populated),
                       "" if len(populated) >= 2 else "只有一个部门时看不出跨部门协同和权限边界的效果"))
    no_code = [n for _, n, code, _ in populated if not code]
    if no_code:
        items.append(_item("department_codes", "部门业务类型", "warn", "这些部门没有设置业务类型，只能用通用办公功能：" + "、".join(no_code), "管理后台 → 企业管理 → 部门，设置业务类型（人事/采购/销售/财务/IT）"))
    with_model = {r[0] for r in db.execute(select(LLMConfig.user_id).where(LLMConfig.user_id.in_(members or [0]), LLMConfig.is_active == 1)).all()}
    missing = len(members) - len(with_model)
    items.append(_item("member_models", "成员已连接模型", "ok" if not missing else "warn", "所有成员都已连接模型" if not missing else f"{missing} 位成员还没连接模型，他们无法使用 AI 整理",
                       "" if not missing else "让他们在「设置 → 模型连接」连接，或由管理员统一配置"))
    heads = db.execute(text("SELECT COUNT(DISTINCT tm.team_id) FROM team_members tm JOIN enterprise_role er ON er.id=tm.role_id JOIN teams t ON t.id=tm.team_id "
                            "WHERE t.organization_id=:o AND t.status='active' AND tm.status='active' AND er.code='admin'"), {"o": org_id}).scalar() or 0
    items.append(_item("heads", "部门负责人", "ok" if heads >= len(populated) else "warn", f"{heads}/{len(populated)} 个有成员的部门设置了负责人",
                       "" if heads >= len(populated) else "没有负责人的部门，审批和责任协同的发布、认定没人处理"))
    published = db.execute(select(func.count()).select_from(Agent).join(Team, Team.id == Agent.team_id).where(Team.organization_id == org_id, Agent.lifecycle_status == "published")).scalar() or 0
    items.append(_item("agents", "已发布的部门助手", "ok" if published else "warn", f"{published} 个", "" if published else "管理后台里按模板创建并发布部门助手"))
    # 考勤
    hr_team = next((t for t in populated if t[2] == "hr"), None)
    items.append(_item("attendance_hr", "考勤：人事成员", "ok" if hr_team else "warn", "有人事部门成员，可以导入考勤" if hr_team else "没有人事类型的部门成员，没人能导入考勤文件",
                       "" if hr_team else "给人事部门设置业务类型 hr，并加入成员"))
    this_year = utcnow().year
    calendar = db.execute(select(func.count()).select_from(AttendanceCalendar).where(AttendanceCalendar.organization_id == org_id,
                                                                                    AttendanceCalendar.day >= f"{this_year}-01-01")).scalar() or 0
    items.append(_item("attendance_calendar", "考勤：工作日历", "ok" if calendar else "warn", f"已录入 {calendar} 天" if calendar else "还没录入节假日和调休，节假日会被当成上班日，产生大量旷工误报",
                       "" if calendar else "「考勤 → 规则与日历」按国务院公布的安排录入"))
    rule = db.execute(select(func.count()).select_from(AttendanceRule).where(AttendanceRule.organization_id == org_id)).scalar() or 0
    items.append(_item("attendance_rule", "考勤：上下班规则", "ok" if rule else "warn", "已设置" if rule else "使用默认规则 09:00–18:00、宽限 5 分钟，请确认符合公司制度",
                       "" if rule else "「考勤 → 规则与日历」按部门设置"))
    return items


def run(db, org_id: Optional[int] = None) -> Dict[str, Any]:
    checks = check_environment() + check_operations(db) + (check_roster(db, org_id) if org_id else [])
    if not org_id:
        checks.append(_item("roster", "试点范围", "warn", "没有指定试点企业，跳过成员、部门、考勤的检查", "加上 --org <企业编号>"))
    return {"ok": all(c["ok"] for c in checks), "errors": sum(1 for c in checks if c["level"] == "error"),
            "warnings": sum(1 for c in checks if c["level"] == "warn"), "checks": checks}


def to_markdown(result: Dict[str, Any]) -> str:
    mark = {"ok": "✅", "warn": "⚠️", "error": "❌"}
    lines = ["| 状态 | 检查项 | 结果 | 怎么处理 |", "|---|---|---|---|"]
    lines += [f"| {mark[c['level']]} | {c['label']} | {c['message']} | {c['fix'] or '—'} |" for c in result["checks"]]
    verdict = "可以开始试点" if result["ok"] else f"不能开始：{result['errors']} 项必须先处理"
    return "\n".join([f"## 试点预检：{verdict}（{result['warnings']} 项提醒）", ""] + lines)
