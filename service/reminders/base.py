"""提醒规则的公共部分：规则定义、组织成员查询、把"此刻应存在的提醒"同步成待办与通知。"""
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Dict, List, Optional, Tuple

from sqlalchemy import select

from models.init_db import EnterpriseRole, Organization, OrganizationMember, Team, TeamMember
from service import notification_center, work_item_service

BEIJING = timedelta(hours=8)


def env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, default)))
    except ValueError:
        return default


def parse_instant(value) -> Optional[datetime]:
    """Java 的 Instant（ISO 字符串）→ naive UTC。"""
    if not value:
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.astimezone(timezone.utc).replace(tzinfo=None) if parsed.tzinfo else parsed


def beijing_day_start(day: str) -> datetime:
    """'2026-10-12' → 当天北京时间 00:00 对应的 UTC 时间。"""
    return datetime.fromisoformat(day) - BEIJING


@dataclass(frozen=True)
class Reminder:
    user_id: int
    key: str
    title: str
    detail: Optional[str] = None
    link: Optional[str] = "/department"
    priority: str = "normal"
    due_at: Optional[datetime] = None
    team_id: Optional[int] = None
    notify: bool = True


@dataclass(frozen=True)
class ReminderRule:
    name: str
    label: str
    category: str
    interval_minutes: int
    run: Callable[..., Awaitable[Tuple[int, int]]]   # (db) -> (新建数, 自动关闭数)
    description: str = ""
    extra: Dict[str, object] = field(default_factory=dict)


async def sync_reminders(db, rule_name: str, category: str, reminders: List[Reminder]) -> Tuple[int, int]:
    created = 0
    keys = []
    for r in reminders:
        keys.append((r.user_id, r.key))
        is_new = await work_item_service.upsert(
            db, r.user_id, "reminder", r.key, title=r.title, rule=rule_name, team_id=r.team_id,
            detail=r.detail, link=r.link, priority=r.priority, due_at=r.due_at, reopen=True)
        created += int(is_new)
        if r.notify and (is_new or r.priority == "high"):
            # 升级为高优先级时再提醒一次（dedupe_key 带上优先级）。
            await notification_center.notify(db, r.user_id, category, r.title, body=r.detail, link=r.link,
                                             dedupe_key=f"{rule_name}:{r.key}:{r.priority}")
    resolved = await work_item_service.resolve_missing(db, rule_name, keys)
    return created, resolved


# ---- 组织成员查询（都只看启用状态的企业/部门/成员） ----

async def active_teams(db, department_code: Optional[str] = None) -> List[Team]:
    query = (select(Team).join(Organization, Organization.id == Team.organization_id)
             .where(Team.status == "active", Organization.status == "active"))
    if department_code:
        query = query.where(Team.department_code == department_code)
    return list((await db.execute(query.order_by(Team.id))).scalars().all())


async def team_members(db, team_id: int, role_code: Optional[str] = None) -> List[int]:
    query = (select(TeamMember.user_id).join(EnterpriseRole, EnterpriseRole.id == TeamMember.role_id)
             .where(TeamMember.team_id == team_id, TeamMember.status == "active"))
    if role_code:
        query = query.where(EnterpriseRole.code == role_code)
    return [row[0] for row in (await db.execute(query.order_by(TeamMember.user_id))).all()]


async def org_admins(db, organization_id: int) -> List[int]:
    rows = (await db.execute(
        select(OrganizationMember.user_id).join(EnterpriseRole, EnterpriseRole.id == OrganizationMember.role_id)
        .where(OrganizationMember.organization_id == organization_id, OrganizationMember.status == "active",
               EnterpriseRole.scope == "organization", EnterpriseRole.code.in_(("owner", "admin")))
        .order_by(OrganizationMember.user_id))).all()
    return [row[0] for row in rows]


async def approvers(db, team: Team) -> List[int]:
    """部门负责人；部门没有负责人时由企业所有者/管理员审批。"""
    return await team_members(db, team.id, "admin") or await org_admins(db, team.organization_id)
