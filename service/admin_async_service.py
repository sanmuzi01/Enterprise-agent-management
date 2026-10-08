"""管理员后台异步统计服务。"""

from utils.timeutil import utcnow
from datetime import timedelta
from typing import Dict, List

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload
from starlette.concurrency import run_in_threadpool

from service.exceptions import Conflict, InvalidInput, NotFound

from models.init_db import (
    Agent,
    AgentRun,
    BackgroundTask,
    Chat,
    Conversation,
    Knowledge,
    KnowledgeChunk,
    LLMConfig,
    Memory,
    Message,
    Role,
    Skill,
    User,
)
from models.user_async_dao import bump_auth_version_async, update_user_password_async
from service.admin_service import (
    ADMIN_ROLE_NAMES,
    ONLINE_WINDOW_SECONDS,
    _format_dt,
    _user_admin_payload,
    current_user_payload,
)
from service.auth_service import hash_password, password_matches
from service.password_policy import PasswordPolicyError, check_not_same_as_old, check_password_policy


async def _count(db, model) -> int:
    result = await db.execute(select(func.count(model.id)))
    return int(result.scalar() or 0)


async def _count_by_user(db, model, user_ids: List[int] = None) -> Dict[int, int]:
    """按 user_id 分组计数。传 user_ids 时只统计这些用户（分页场景下不用全表扫）。"""
    stmt = select(model.user_id, func.count(model.id))
    if user_ids is not None:
        if not user_ids:
            return {}
        stmt = stmt.where(model.user_id.in_(user_ids))
    result = await db.execute(stmt.group_by(model.user_id))
    return {user_id: int(count or 0) for user_id, count in result.all()}


async def overview(db) -> Dict:
    online_cutoff = utcnow() - timedelta(seconds=ONLINE_WINDOW_SECONDS)
    task_status_rows = await db.execute(
        select(BackgroundTask.status, func.count(BackgroundTask.id)).group_by(BackgroundTask.status)
    )
    knowledge_status_rows = await db.execute(
        select(Knowledge.status, func.count(Knowledge.id)).group_by(Knowledge.status)
    )
    online_users = await db.execute(
        select(func.count(User.id)).where(
            or_(User.is_disabled == 0, User.is_disabled.is_(None)),
            User.last_seen_at >= online_cutoff,
        )
    )
    return {
        "counts": {
            "users": await _count(db, User),
            "online_users": int(online_users.scalar() or 0),
            "agents": await _count(db, Agent),
            "skills": await _count(db, Skill),
            "llm_configs": await _count(db, LLMConfig),
            "knowledge_docs": await _count(db, Knowledge),
            "knowledge_chunks": await _count(db, KnowledgeChunk),
            "conversations": await _count(db, Conversation),
            "messages": await _count(db, Message),
            "runs": await _count(db, AgentRun),
            "background_tasks": await _count(db, BackgroundTask),
            "memories": await _count(db, Memory),
            "legacy_chats": await _count(db, Chat),
        },
        "task_status": {status or "unknown": int(count or 0) for status, count in task_status_rows.all()},
        "knowledge_status": {status or "unknown": int(count or 0) for status, count in knowledge_status_rows.all()},
    }


async def list_users(db, limit: int = 50, offset: int = 0, search: str = None) -> Dict:
    """用户管控列表，支持分页 + 按用户名/手机号搜索。

    之前是一次性 SELECT * FROM User（无 limit）+ 4 条全表 GROUP BY 统计所有用户
    的 agent/skill/knowledge/task 数量——用户量一大，这条路由会越来越慢。现在
    分页查用户本身，4 条计数也只统计当前页这些用户，不再扫全表。
    """
    limit = max(1, min(limit, 200))
    offset = max(0, offset)

    conditions = []
    if search:
        like = f"%{search.strip()}%"
        conditions.append(or_(User.name.like(like), User.phone.like(like)))

    count_stmt = select(func.count(User.id))
    if conditions:
        count_stmt = count_stmt.where(*conditions)
    total = int((await db.execute(count_stmt)).scalar() or 0)

    query = select(User).options(selectinload(User.roles))
    if conditions:
        query = query.where(*conditions)
    query = query.order_by(User.id.desc()).limit(limit).offset(offset)
    users = list((await db.execute(query)).scalars().all())

    user_ids = [u.id for u in users]
    agent_counts = await _count_by_user(db, Agent, user_ids)
    skill_counts = await _count_by_user(db, Skill, user_ids)
    knowledge_counts = await _count_by_user(db, Knowledge, user_ids)
    task_counts = await _count_by_user(db, BackgroundTask, user_ids)
    plan_names = await _plan_names_by_user(db, user_ids)
    items = []
    for user in users:
        item = _user_admin_payload(
            user,
            agent_count=agent_counts.get(user.id, 0),
            skill_count=skill_counts.get(user.id, 0),
            knowledge_count=knowledge_counts.get(user.id, 0),
            task_count=task_counts.get(user.id, 0),
        )
        item["plan_name"] = plan_names.get(user.id)
        items.append(item)
    return {"items": items, "total": total, "limit": limit, "offset": offset}


async def get_user_detail(db, user_id: int) -> Dict:
    result = await db.execute(
        select(User).where(User.id == user_id).options(selectinload(User.roles))
    )
    user = result.scalars().first()
    if not user:
        return {}
    data = _user_admin_payload(
        user,
        agent_count=await _count_for_user(db, Agent, user_id),
        skill_count=await _count_for_user(db, Skill, user_id),
        knowledge_count=await _count_for_user(db, Knowledge, user_id),
        task_count=await _count_for_user(db, BackgroundTask, user_id),
    )
    messages = await db.execute(
        select(func.count(Message.id))
        .join(Conversation, Message.conversation_id == Conversation.id)
        .where(Conversation.user_id == user_id)
    )
    data["counts"] = {
        "agents": data["agent_count"],
        "skills": data["skill_count"],
        "knowledge_docs": data["knowledge_count"],
        "background_tasks": data["task_count"],
        "llm_configs": await _count_for_user(db, LLMConfig, user_id),
        "conversations": await _count_for_user(db, Conversation, user_id),
        "messages": int(messages.scalar() or 0),
        "runs": await _count_for_user(db, AgentRun, user_id),
        "memories": await _count_for_user(db, Memory, user_id),
        "legacy_chats": await _count_for_user(db, Chat, user_id),
    }
    return data


async def _count_for_user(db, model, user_id: int) -> int:
    result = await db.execute(select(func.count(model.id)).where(model.user_id == user_id))
    return int(result.scalar() or 0)


async def list_recent_tasks(db, limit: int = 50) -> List[Dict]:
    result = await db.execute(
        select(BackgroundTask).order_by(BackgroundTask.created_at.desc()).limit(limit)
    )
    return [
        {
            "id": task.id,
            "user_id": task.user_id,
            "agent_id": task.agent_id,
            "task_type": task.task_type,
            "status": task.status,
            "title": task.title,
            "target_type": task.target_type,
            "target_id": task.target_id,
            "progress": task.progress,
            "error_msg": task.error_msg,
            "created_at": _format_dt(task.created_at),
            "started_at": _format_dt(task.started_at),
            "finished_at": _format_dt(task.finished_at),
            "next_run_at": _format_dt(getattr(task, "next_run_at", None)),
        }
        for task in result.scalars().all()
    ]


async def usage_stats(db, days: int = 14, top_limit: int = 8) -> Dict:
    days = max(1, min(days, 90))
    top_limit = max(1, min(top_limit, 20))
    start_dt = utcnow() - timedelta(days=days - 1)
    start_day = start_dt.date()

    run_rows = await db.execute(
        select(
            func.date(AgentRun.started_at),
            func.count(AgentRun.id),
            func.coalesce(func.sum(AgentRun.total_tokens), 0),
        )
        .where(AgentRun.started_at >= start_dt)
        .group_by(func.date(AgentRun.started_at))
    )
    message_rows = await db.execute(
        select(func.date(Message.create_time), func.count(Message.id))
        .where(Message.create_time >= start_dt)
        .group_by(func.date(Message.create_time))
    )
    task_rows = await db.execute(
        select(BackgroundTask.status, func.count(BackgroundTask.id)).group_by(BackgroundTask.status)
    )
    top_run_rows = await db.execute(
        select(
            User.id,
            User.name,
            func.count(AgentRun.id),
            func.coalesce(func.sum(AgentRun.total_tokens), 0),
        )
        .join(AgentRun, AgentRun.user_id == User.id)
        .group_by(User.id, User.name)
        .order_by(func.count(AgentRun.id).desc())
        .limit(top_limit)
    )

    run_map = {str(day): {"runs": int(count or 0), "tokens": int(tokens or 0)} for day, count, tokens in run_rows.all()}
    message_map = {str(day): int(count or 0) for day, count in message_rows.all()}
    daily = []
    for offset in range(days):
        day = start_day + timedelta(days=offset)
        key = day.isoformat()
        run_item = run_map.get(key, {"runs": 0, "tokens": 0})
        daily.append({
            "date": key,
            "runs": run_item["runs"],
            "tokens": run_item["tokens"],
            "messages": message_map.get(key, 0),
        })

    total_runs = await _count(db, AgentRun)
    finished_runs = await _count_by_status(db, AgentRun, "finished")
    failed_runs = await _count_by_status(db, AgentRun, "failed")
    total_tokens = await db.execute(select(func.coalesce(func.sum(AgentRun.total_tokens), 0)))
    total_messages = await _count(db, Message)

    return {
        "summary": {
            "total_runs": total_runs,
            "finished_runs": finished_runs,
            "failed_runs": failed_runs,
            "success_rate": round((finished_runs / total_runs * 100), 1) if total_runs else 0,
            "total_tokens": int(total_tokens.scalar() or 0),
            "total_messages": total_messages,
        },
        "daily": daily,
        "task_status": {status or "unknown": int(count or 0) for status, count in task_rows.all()},
        "top_users": [
            {
                "user_id": user_id,
                "name": name,
                "run_count": int(run_count or 0),
                "tokens": int(tokens or 0),
            }
            for user_id, name, run_count, tokens in top_run_rows.all()
        ],
    }


async def _count_by_status(db, model, status: str) -> int:
    result = await db.execute(select(func.count(model.id)).where(model.status == status))
    return int(result.scalar() or 0)


async def set_user_roles(db, user_id: int, roles: List[str], operator_id: int = None) -> Dict:
    result = await db.execute(
        select(User).where(User.id == user_id).options(selectinload(User.roles))
    )
    user = result.scalars().first()
    if not user:
        return {}
    clean_names = []
    for role in roles:
        name = (role or "").strip()
        if name and name not in clean_names:
            clean_names.append(name)
    role_models = []
    for name in clean_names:
        role_result = await db.execute(select(Role).where(Role.role_name == name))
        role_model = role_result.scalars().first()
        if not role_model:
            role_model = Role(role_name=name, description=f"{name} role")
            db.add(role_model)
            await db.flush()
        role_models.append(role_model)
    if user.name == "admin" and not any(role.role_name == "admin" for role in role_models):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="不能移除内置管理员账号的 admin 角色")
    if operator_id == user.id and not any(role.role_name in ADMIN_ROLE_NAMES for role in role_models):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="不能移除当前登录管理员自己的管理员角色")
    user.roles = role_models
    await db.flush()
    await db.commit()
    return current_user_payload(user)


async def set_user_disabled(db, user_id: int, disabled: bool, operator_id: int) -> Dict:
    result = await db.execute(select(User).where(User.id == user_id).options(selectinload(User.roles)))
    user = result.scalars().first()
    if not user:
        return {}
    if user.id == operator_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="不能禁用当前登录的管理员账号")
    if user.name == "admin" and disabled:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="不能禁用内置管理员账号")
    user.is_disabled = 1 if disabled else 0
    await db.flush()
    await db.commit()
    return current_user_payload(user)


async def reset_user_password(db, user_id: int, new_password: str) -> Dict:
    """管理员强制重置用户密码：同样走统一密码策略，成功后该用户旧 token 全部失效。"""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalars().first()
    if not user:
        return {}
    try:
        check_password_policy(new_password, username=user.name, phone=getattr(user, "phone", "") or "")
        await run_in_threadpool(check_not_same_as_old, new_password, user.password, password_matches)
    except PasswordPolicyError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e))
    hashed = await run_in_threadpool(hash_password, new_password)
    await update_user_password_async(db, user.id, hashed)
    return {"message": "密码已重置", "user_id": user.id}


async def force_logout_user(db, user_id: int) -> bool:
    """管理员强制下线：不改密码，只让该用户已签发的所有 token 立即失效。返回用户是否存在。"""
    return await bump_auth_version_async(db, user_id)


def _space_scope_filter(scope: str | None):
    """"personal" = 个人空间；"team:<id>" = 某个部门；其余（含 None / "all"）= 不过滤。"""
    from models.init_db import KnowledgeSpace

    if scope == "personal":
        return KnowledgeSpace.scope_type != "department"
    if scope and scope.startswith("team:"):
        try:
            return (KnowledgeSpace.scope_type == "department") & (KnowledgeSpace.team_id == int(scope[5:]))
        except ValueError:
            raise InvalidInput("部门筛选条件不正确") from None
    return None


async def _space_department_overview(db) -> Dict:
    """每个部门下有多少知识库空间（含 0 个的部门）、个人空间有多少、一共多少。"""
    from models.init_db import KnowledgeSpace, Organization, Team

    teams = (await db.execute(
        select(Team.id, Team.name, Team.organization_id, Organization.name)
        .join(Organization, Organization.id == Team.organization_id)
        .where(Team.status == "active").order_by(Team.organization_id, Team.id))).all()
    counts = {row[0]: int(row[1]) for row in (await db.execute(
        select(KnowledgeSpace.team_id, func.count(KnowledgeSpace.id))
        .where(KnowledgeSpace.scope_type == "department").group_by(KnowledgeSpace.team_id))).all()}
    total = int((await db.execute(select(func.count(KnowledgeSpace.id)))).scalar() or 0)
    personal = int((await db.execute(
        select(func.count(KnowledgeSpace.id)).where(KnowledgeSpace.scope_type != "department"))).scalar() or 0)
    return {
        "departments": [
            {"id": t[0], "name": t[1], "organization_id": t[2], "organization_name": t[3], "space_count": counts.get(t[0], 0)}
            for t in teams
        ],
        "personal_count": personal,
        "all_count": total,
    }


async def list_knowledge_spaces(db, limit: int = 500, offset: int = 0, scope: str | None = None) -> Dict:
    """企业知识库视角：所有知识库空间 + 归属 / 规模 / 成员数 / 健康分，可按部门筛选。"""
    from models.init_db import AgentKnowledgeSpace, KnowledgeSpace, SpaceMember

    limit = max(1, min(limit, 1000))
    offset = max(0, offset)
    scope_filter = _space_scope_filter(scope)
    count_stmt = select(func.count(KnowledgeSpace.id))
    list_stmt = select(KnowledgeSpace)
    if scope_filter is not None:
        count_stmt, list_stmt = count_stmt.where(scope_filter), list_stmt.where(scope_filter)
    total = int((await db.execute(count_stmt)).scalar() or 0)
    overview = await _space_department_overview(db)

    res = await db.execute(list_stmt.order_by(KnowledgeSpace.id.desc()).limit(limit).offset(offset))
    spaces = list(res.scalars().all())
    if not spaces:
        return {"items": [], "total": total, "limit": limit, "offset": offset, **overview}

    owner_ids = {s.user_id for s in spaces}
    owners_res = await db.execute(select(User.id, User.name).where(User.id.in_(owner_ids)))
    owner_names = {row[0]: row[1] for row in owners_res.all()}

    space_ids = [s.id for s in spaces]
    mem_res = await db.execute(
        select(SpaceMember.space_id, func.count(SpaceMember.id))
        .where(SpaceMember.space_id.in_(space_ids)).group_by(SpaceMember.space_id)
    )
    member_counts = {row[0]: int(row[1]) for row in mem_res.all()}
    bind_res = await db.execute(
        select(AgentKnowledgeSpace.space_id, func.count(AgentKnowledgeSpace.id))
        .where(AgentKnowledgeSpace.space_id.in_(space_ids)).group_by(AgentKnowledgeSpace.space_id)
    )
    bind_counts = {row[0]: int(row[1]) for row in bind_res.all()}

    from models.enterprise_dao import get_team_names_async
    team_names = await get_team_names_async(db, [s.team_id for s in spaces])
    sensitivity_labels = {"public": "公开", "internal": "内部", "confidential": "机密", "restricted": "绝密"}

    items = [
        {
            "id": s.id,
            "name": s.name,
            "owner_user_id": s.user_id,
            "owner_name": owner_names.get(s.user_id, ""),
            "organization_id": s.organization_id,
            "team_id": s.team_id,
            "team_name": team_names.get(s.team_id),
            "scope_type": s.scope_type,
            "sensitivity": s.sensitivity,
            "sensitivity_label": sensitivity_labels.get(s.sensitivity, "内部"),
            "status": s.status,
            "is_enabled": bool(s.is_enabled),
            "purpose": s.purpose,
            "doc_count": s.doc_count,
            "chunk_count": s.chunk_count,
            "member_count": member_counts.get(s.id, 0),
            "bound_agent_count": bind_counts.get(s.id, 0),
            "health_score": s.health_score,
            "created_at": _format_dt(s.created_at),
            "updated_at": _format_dt(s.updated_at),
        }
        for s in spaces
    ]
    return {"items": items, "total": total, "limit": limit, "offset": offset, **overview}


async def _department_fields(db, team_id) -> Dict:
    """管理员把空间放进某个部门（或放回个人空间）。平台管理员可以操作任何部门。"""
    from models.init_db import Team

    if team_id is None:
        return {"team_id": None, "scope_type": "personal"}
    team = (await db.execute(select(Team).where(Team.id == int(team_id)))).scalar_one_or_none()
    if team is None or team.status != "active":
        raise InvalidInput("部门不存在或已停用")
    return {"team_id": team.id, "organization_id": team.organization_id, "scope_type": "department"}


async def admin_create_space(db, admin_user_id: int, payload: Dict) -> Dict:
    """管理员直接为某个部门建知识库空间（所有者是这位管理员；部门成员自动只读）。"""
    from service.knowledge_space import space_async_service as spaces
    from models.knowledge_space_async_dao import create_space_async
    from models import kb_audit_dao

    fields = spaces._clean_create(payload)
    if payload.get("sensitivity") is not None:
        fields["sensitivity"] = spaces._clean_sensitivity(payload["sensitivity"])
    if payload.get("team_id") is not None:
        fields.update(await _department_fields(db, payload["team_id"]))
    space = await create_space_async(db, admin_user_id, fields)
    try:
        await kb_audit_dao.record_async(
            db, admin_user_id, "admin.space.create", space_id=space.id, target_type="space", target_id=space.id,
            detail={"team_id": space.team_id, "sensitivity": space.sensitivity},
        )
    except Exception:  # noqa: BLE001 —— 审计失败不影响主流程
        pass
    return {"id": space.id, "name": space.name, "team_id": space.team_id, "sensitivity": space.sensitivity}


async def admin_update_space(db, admin_user_id: int, space_id: int, patch: Dict) -> Dict:
    """管理员直接改一个知识库空间的启停/归档状态——不要求管理员是该空间成员。

    普通的 update_space（service/knowledge_space/space_async_service.py）要求调用者
    是空间的所有者/管理员成员；企业知识库总览页面向的是平台管理员，本来就该能管
    任何空间，不应该被"你不是这个空间的成员"挡住。复用同一个字段校验规则和同一张
    审计日志（kb_audit_dao），只是跳过成员权限校验，用 action 前缀区分是管理员操作。
    """
    from models import kb_audit_dao
    from models.knowledge_space_async_dao import get_space_by_id_async, update_space_async

    space = await get_space_by_id_async(db, space_id)
    if not space:
        raise NotFound("知识库空间不存在")

    fields: Dict = {}
    if "is_enabled" in patch and patch["is_enabled"] is not None:
        fields["is_enabled"] = 1 if patch["is_enabled"] else 0
    if "status" in patch and patch["status"] in ("active", "archived"):
        fields["status"] = patch["status"]
    if "sensitivity" in patch and patch["sensitivity"] is not None:
        from service.knowledge_space.space_async_service import _clean_sensitivity
        fields["sensitivity"] = _clean_sensitivity(patch["sensitivity"])
    if "team_id" in patch:
        fields.update(await _department_fields(db, patch["team_id"]))
    if not fields:
        raise InvalidInput("没有需要更新的内容")

    before = {"team_id": space.team_id, "sensitivity": space.sensitivity}
    space = await update_space_async(db, space, fields)
    try:
        await kb_audit_dao.record_async(
            db, admin_user_id, "admin.space.update", space_id=space_id,
            target_type="space", target_id=space_id,
            detail={"fields": sorted(fields.keys()), "before": before,
                    "after": {"team_id": space.team_id, "sensitivity": space.sensitivity}},
        )
    except Exception:  # noqa: BLE001 —— 审计失败不影响主流程
        pass

    return {
        "id": space.id, "name": space.name, "is_enabled": bool(space.is_enabled),
        "status": space.status, "team_id": space.team_id, "scope_type": space.scope_type,
        "sensitivity": space.sensitivity,
    }


def _plan_payload(plan) -> Dict:
    return {
        "id": plan.id,
        "name": plan.name,
        "display_name": plan.display_name,
        "monthly_token_limit": plan.monthly_token_limit,
        "price_desc": plan.price_desc,
        "is_default": bool(plan.is_default),
        "is_enabled": bool(plan.is_enabled),
        "created_at": _format_dt(plan.created_at),
        "updated_at": _format_dt(plan.updated_at),
    }


async def _plan_names_by_user(db, user_ids: List[int]) -> Dict[int, str]:
    """批量查一批用户当前生效的套餐名——有订阅用订阅的，没有则算默认套餐。"""
    from models.init_db import Plan, UserSubscription

    if not user_ids:
        return {}
    sub_rows = await db.execute(
        select(UserSubscription.user_id, Plan.name)
        .join(Plan, UserSubscription.plan_id == Plan.id)
        .where(UserSubscription.user_id.in_(user_ids))
    )
    names = {uid: name for uid, name in sub_rows.all()}
    missing = [uid for uid in user_ids if uid not in names]
    if missing:
        from models.plan_async_dao import get_default_plan_async
        default_plan = await get_default_plan_async(db)
        if default_plan:
            for uid in missing:
                names[uid] = default_plan.name
    return names


async def list_plans(db) -> List[Dict]:
    from models.plan_async_dao import list_plans_async
    plans = await list_plans_async(db)
    return [_plan_payload(p) for p in plans]


async def create_plan(db, admin_user_id: int, data: Dict) -> Dict:
    from models.init_db import Plan

    name = (data.get("name") or "").strip()
    display_name = (data.get("display_name") or "").strip()
    if not name or not display_name:
        raise InvalidInput("套餐标识和展示名不能为空")
    existing = await db.execute(select(Plan).where(Plan.name == name))
    if existing.scalars().first():
        raise InvalidInput(f"套餐标识「{name}」已存在")

    monthly_token_limit = int(data.get("monthly_token_limit") or 0)
    is_default = bool(data.get("is_default"))
    plan = Plan(
        name=name, display_name=display_name,
        monthly_token_limit=max(0, monthly_token_limit),
        price_desc=(data.get("price_desc") or None),
        is_default=1 if is_default else 0,
        is_enabled=1,
    )
    db.add(plan)
    await db.flush()
    if is_default:
        await db.execute(
            Plan.__table__.update().where(Plan.id != plan.id).values(is_default=0)
        )
    await db.commit()
    try:
        from models import kb_audit_dao
        await kb_audit_dao.record_async(
            db, admin_user_id, "admin.plan.create",
            target_type="plan", target_id=plan.id, detail={"name": name},
        )
    except Exception:  # noqa: BLE001
        pass
    return _plan_payload(plan)


async def update_plan(db, admin_user_id: int, plan_id: int, patch: Dict) -> Dict:
    from models.init_db import Plan
    from models.plan_async_dao import get_plan_async

    plan = await get_plan_async(db, plan_id)
    if not plan:
        raise NotFound("套餐不存在")

    fields: Dict = {}
    if "display_name" in patch and (patch["display_name"] or "").strip():
        fields["display_name"] = patch["display_name"].strip()
    if "monthly_token_limit" in patch and patch["monthly_token_limit"] is not None:
        fields["monthly_token_limit"] = max(0, int(patch["monthly_token_limit"]))
    if "price_desc" in patch:
        fields["price_desc"] = patch["price_desc"] or None
    if "is_enabled" in patch and patch["is_enabled"] is not None:
        fields["is_enabled"] = 1 if patch["is_enabled"] else 0
    make_default = bool(patch.get("is_default"))
    if not fields and not make_default:
        raise InvalidInput("没有需要更新的内容")

    for key, value in fields.items():
        setattr(plan, key, value)
    if make_default:
        await db.execute(
            Plan.__table__.update().where(Plan.id != plan.id).values(is_default=0)
        )
        plan.is_default = 1
    await db.flush()
    await db.commit()
    try:
        from models import kb_audit_dao
        await kb_audit_dao.record_async(
            db, admin_user_id, "admin.plan.update",
            target_type="plan", target_id=plan.id,
            detail={"fields": sorted(fields.keys()), "is_default": make_default},
        )
    except Exception:  # noqa: BLE001
        pass
    return _plan_payload(plan)


async def delete_plan(db, admin_user_id: int, plan_id: int) -> Dict:
    from models.plan_async_dao import count_subscriptions_for_plan_async, get_plan_async

    plan = await get_plan_async(db, plan_id)
    if not plan:
        raise NotFound("套餐不存在")
    if plan.is_default:
        raise InvalidInput("不能删除默认套餐，请先把另一个套餐设为默认")
    in_use = await count_subscriptions_for_plan_async(db, plan_id)
    if in_use:
        raise Conflict(f"还有 {in_use} 个用户订阅了这个套餐，请先把他们迁移到其它套餐")

    await db.delete(plan)
    await db.commit()
    try:
        from models import kb_audit_dao
        await kb_audit_dao.record_async(
            db, admin_user_id, "admin.plan.delete",
            target_type="plan", target_id=plan_id, detail={"name": plan.name},
        )
    except Exception:  # noqa: BLE001
        pass
    return {"message": "套餐已删除", "id": plan_id}


async def assign_user_plan(db, admin_user_id: int, user_id: int, plan_id: int) -> Dict:
    from models.plan_async_dao import get_plan_async, set_user_plan_async

    user_result = await db.execute(select(User).where(User.id == user_id))
    if not user_result.scalars().first():
        raise NotFound("用户不存在")
    plan = await get_plan_async(db, plan_id)
    if not plan:
        raise NotFound("套餐不存在")
    if not plan.is_enabled:
        raise InvalidInput("这个套餐已停用，不能再分配给用户")

    await set_user_plan_async(db, user_id, plan_id)
    try:
        from models import kb_audit_dao
        await kb_audit_dao.record_async(
            db, admin_user_id, "admin.user.plan_assign",
            target_type="user", target_id=user_id, detail={"plan_id": plan_id, "plan_name": plan.name},
        )
    except Exception:  # noqa: BLE001
        pass
    return {"user_id": user_id, "plan_id": plan_id, "plan_name": plan.name}


async def delete_user(user_id: int, operator_id: int) -> Dict:
    """删除用户及其级联数据。

    级联删除逻辑（含逐个 agent 清理）较复杂且已在同步实现中验证过，
    这里用独立同步会话在线程池执行，避免重复维护两套删除逻辑。
    """
    from models.init_db import SessionLocal
    from service import admin_service

    def _run() -> Dict:
        session = SessionLocal()
        try:
            return admin_service.delete_user(session, user_id, operator_id)
        finally:
            session.close()

    return await run_in_threadpool(_run)
