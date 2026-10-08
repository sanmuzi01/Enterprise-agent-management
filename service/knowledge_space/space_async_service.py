"""知识库空间：路由编排层（async）。

对外返回面向普通用户的结构；技术字段（legacy_agent_id / vector_migrated）不下发。
"""

import json
from typing import Any, Dict, List, Optional

from models import enterprise_dao, kb_audit_dao, knowledge_space_async_dao as dao
from service.access_control import get_owned_space_async, get_space_role_async
from service.exceptions import InvalidInput, NotFound, PermissionDenied
from service.knowledge_space import membership

PURPOSES = [
    "customer_service", "policy", "product", "sales", "legal", "tech", "research", "other",
]
PURPOSE_LABELS = {
    "customer_service": "客服知识库", "policy": "企业制度知识库", "product": "产品文档知识库",
    "sales": "销售资料知识库", "legal": "合同/法务知识库", "tech": "技术文档知识库",
    "research": "投研资料知识库", "other": "其他",
}


SENSITIVITIES = ("public", "internal", "confidential", "restricted")
SENSITIVITY_LABELS = {
    "public": "公开", "internal": "内部", "confidential": "机密", "restricted": "绝密",
}


def _tags(raw) -> List[str]:
    if isinstance(raw, list):
        return [str(t).strip()[:40] for t in raw if str(t).strip()][:20]
    return []


def _dump_tags(raw) -> Optional[str]:
    tags = _tags(raw)
    return json.dumps(tags, ensure_ascii=False) if tags else None


def _to_dict(space, stats: Dict[str, int] = None, *, role: str = "owner", team_name: Optional[str] = None) -> Dict[str, Any]:
    try:
        tags = json.loads(space.tags_json) if space.tags_json else []
    except (TypeError, ValueError):
        tags = []
    d = {
        "id": space.id,
        "name": space.name,
        "description": space.description or "",
        "purpose": space.purpose,
        "purpose_label": PURPOSE_LABELS.get(space.purpose or "", ""),
        "tags": tags,
        "is_enabled": bool(space.is_enabled),
        "status": space.status,
        "doc_count": space.doc_count,
        "chunk_count": space.chunk_count,
        "health_score": space.health_score,
        "last_indexed_at": space.last_indexed_at.strftime("%Y-%m-%d %H:%M:%S") if space.last_indexed_at else None,
        "created_at": space.created_at.strftime("%Y-%m-%d %H:%M:%S") if space.created_at else None,
        "updated_at": space.updated_at.strftime("%Y-%m-%d %H:%M:%S") if space.updated_at else None,
        "scope": "department" if space.scope_type == "department" else ("personal" if role == "owner" else "shared"),
        "team_id": space.team_id if space.scope_type == "department" else None,
        "team_name": team_name if space.scope_type == "department" else None,
        "sensitivity": space.sensitivity or "internal",
        "sensitivity_label": SENSITIVITY_LABELS.get(space.sensitivity or "internal", "内部"),
        "my_role": role,
        "can_write_doc": membership.can_write_doc(role),
        "can_manage": membership.can_manage_space(role),
        "can_delete": membership.can_delete_space(role),
    }
    if stats:
        d.update(stats)
    return d


async def list_spaces(db, user_id: int) -> Dict[str, Any]:
    # 自己的 + 被加入的 + 本人是部门管理员的部门空间 + 所在部门的部门空间（restricted 除外）
    from service.access_control import user_space_ids_async

    spaces = await dao.list_spaces_by_ids_async(db, sorted(await user_space_ids_async(db, user_id), reverse=True))
    team_names = await enterprise_dao.get_team_names_async(db, [s.team_id for s in spaces if s.scope_type == "department"])
    items = []
    for s in spaces:
        stats = await dao.live_stats_async(db, s.id)
        role = "owner" if s.user_id == user_id else (await get_space_role_async(db, user_id, s.id) or "viewer")
        items.append(_to_dict(s, stats, role=role, team_name=team_names.get(s.team_id)))
    return {
        "items": items,
        "purposes": [{"key": k, "label": PURPOSE_LABELS[k]} for k in PURPOSES],
        # 本人担任部门管理员的部门：只有这些部门可以发布知识库空间
        "publishable_departments": [{"id": t["id"], "name": t["name"]} for t in await enterprise_dao.list_admin_teams_async(db, user_id)],
        "sensitivities": [{"key": k, "label": SENSITIVITY_LABELS[k]} for k in SENSITIVITIES],
    }


async def get_space(db, user_id: int, space_id: int) -> Dict[str, Any]:
    space = await get_owned_space_async(db, user_id, space_id)
    if not space:
        raise NotFound("知识库空间不存在或无权限")
    role = await get_space_role_async(db, user_id, space_id) or "viewer"
    stats = await dao.live_stats_async(db, space_id)
    names = await enterprise_dao.get_team_names_async(db, [space.team_id])
    return _to_dict(space, stats, role=role, team_name=names.get(space.team_id))


def _clean_create(payload: Dict[str, Any]) -> Dict[str, Any]:
    name = str(payload.get("name") or "").strip()[:120]
    if not name:
        raise InvalidInput("知识库空间名称不能为空")
    purpose = payload.get("purpose")
    if purpose is not None and purpose not in PURPOSES:
        purpose = "other"
    return {
        "name": name,
        "description": str(payload.get("description") or "").strip()[:500] or None,
        "purpose": purpose,
        "tags_json": _dump_tags(payload.get("tags")),
    }


def _clean_sensitivity(value) -> str:
    if value not in SENSITIVITIES:
        raise InvalidInput(f"密级只能是 {'、'.join(SENSITIVITY_LABELS[k] for k in SENSITIVITIES)} 之一")
    return value


async def _department_scope_fields(db, user_id: int, team_id: int) -> Dict[str, Any]:
    """把空间发布到某个部门：只有该部门的部门管理员可以。返回要写入的归属字段。"""
    admin_teams = {t["id"]: t for t in await enterprise_dao.list_admin_teams_async(db, user_id)}
    team = admin_teams.get(int(team_id))
    if team is None:
        raise PermissionDenied("只有该部门的部门管理员可以把知识库空间发布到这个部门")
    return {"team_id": team["id"], "organization_id": team["organization_id"], "scope_type": "department"}


async def create_space(db, user_id: int, payload: Dict[str, Any]) -> Dict[str, Any]:
    fields = _clean_create(payload)
    if payload.get("sensitivity") is not None:
        fields["sensitivity"] = _clean_sensitivity(payload["sensitivity"])
    team_name = None
    if payload.get("team_id") is not None:
        fields.update(await _department_scope_fields(db, user_id, payload["team_id"]))
        team_name = (await enterprise_dao.get_team_names_async(db, [fields["team_id"]])).get(fields["team_id"])
    space = await dao.create_space_async(db, user_id, fields)
    if space.scope_type == "department":
        await _audit(db, user_id, "space.publish_to_department", space_id=space.id, target_type="space",
                    target_id=space.id, detail={"team_id": space.team_id, "sensitivity": space.sensitivity})
    return _to_dict(space, {"doc_count": 0, "chunk_count": 0, "bound_agent_count": 0}, team_name=team_name)


async def update_space(db, user_id: int, space_id: int, patch: Dict[str, Any]) -> Dict[str, Any]:
    space = await get_owned_space_async(db, user_id, space_id)
    if not space:
        raise NotFound("知识库空间不存在或无权限")
    role = await get_space_role_async(db, user_id, space_id)
    if not membership.can_manage_space(role):
        raise PermissionDenied("只有空间管理员或所有者能修改空间设置")
    fields: Dict[str, Any] = {}
    if "name" in patch and patch["name"] is not None:
        name = str(patch["name"]).strip()[:120]
        if not name:
            raise InvalidInput("名称不能为空")
        fields["name"] = name
    if "description" in patch and patch["description"] is not None:
        fields["description"] = str(patch["description"]).strip()[:500] or None
    if "purpose" in patch and patch["purpose"] is not None:
        fields["purpose"] = patch["purpose"] if patch["purpose"] in PURPOSES else "other"
    if "tags" in patch and patch["tags"] is not None:
        fields["tags_json"] = _dump_tags(patch["tags"])
    if "is_enabled" in patch and patch["is_enabled"] is not None:
        fields["is_enabled"] = 1 if patch["is_enabled"] else 0
    if "status" in patch and patch["status"] in ("active", "archived"):
        fields["status"] = patch["status"]
    if "sensitivity" in patch and patch["sensitivity"] is not None:
        fields["sensitivity"] = _clean_sensitivity(patch["sensitivity"])
        if fields["sensitivity"] != space.sensitivity and space.scope_type == "department":
            admin_team_ids = {t["id"] for t in await enterprise_dao.list_admin_teams_async(db, user_id)}
            if space.team_id not in admin_team_ids:
                raise PermissionDenied("部门空间的密级只能由该部门的部门管理员调整")
    if "team_id" in patch:
        fields.update(await _change_department(db, user_id, space, patch["team_id"]))
    if not fields:
        raise InvalidInput("没有需要更新的内容")
    old_team_id = space.team_id
    space = await dao.update_space_async(db, space, fields)
    await _audit(db, user_id, "space.update", space_id=space_id, target_type="space",
                target_id=space_id, detail={"fields": sorted(fields.keys()), "team_id_before": old_team_id,
                                            "team_id_after": space.team_id, "sensitivity": space.sensitivity})
    stats = await dao.live_stats_async(db, space_id)
    names = await enterprise_dao.get_team_names_async(db, [space.team_id])
    return _to_dict(space, stats, role=role, team_name=names.get(space.team_id))


async def _change_department(db, user_id: int, space, new_team_id) -> Dict[str, Any]:
    """发布到部门 / 换部门 / 收回成个人空间。

    谁能动：目标部门和（如果已经在某个部门）当前部门的部门管理员。
    只是空间所有者、不是部门管理员的人，不能把空间发布出去，也不能把已发布的部门空间私自收回。"""
    if new_team_id == space.team_id:
        return {}
    admin_team_ids = {t["id"] for t in await enterprise_dao.list_admin_teams_async(db, user_id)}
    if space.team_id is not None and space.team_id not in admin_team_ids:
        raise PermissionDenied("这个空间已经发布在部门里，只有该部门的部门管理员可以调整它的归属")
    if new_team_id is None:
        return {"team_id": None, "scope_type": "personal"}
    return await _department_scope_fields(db, user_id, new_team_id)


async def delete_space(db, user_id: int, space_id: int) -> Dict[str, Any]:
    """删除知识库空间是 Phase 3D 阶段4 列的平台高风险操作之一，需要企业管理员审批
    （docs/enterprise-rbac-plan.md 9.5）：第一次调用只建审批单并返回"待审批"，
    企业管理员通过 `POST /approvals/{id}/decide` 批准后，用户重新调一次这个接口
    才真正执行删除（`approval_service.try_consume_approved` 认领那条 approved 单）。

    第五轮审计 P1-5：`try_consume_approved` 认领审批（标 `executed_at`）之后，不能
    中途 `commit`——必须跟下面真正的删除操作在同一个事务里，靠 `dao.delete_space_async`
    最后那一次 commit 一起提交，删除失败时（DB 故障等）这次 session 不提交就被
    回滚/关闭，认领动作也会跟着回滚，审批单恢复成"approved、未消费"，下次重试
    能重新走到这里，不会出现"审批用掉了但空间没删掉，还申请不了新审批"的卡死状态。
    """
    space = await get_owned_space_async(db, user_id, space_id)
    if not space:
        raise NotFound("知识库空间不存在或无权限")
    role = await get_space_role_async(db, user_id, space_id)
    if not membership.can_delete_space(role):
        raise PermissionDenied("只有空间所有者能删除空间")
    stats = await dao.live_stats_async(db, space_id)
    if stats["doc_count"] > 0:
        raise InvalidInput("空间下还有文档，请先清空文档或改为归档")

    from service import approval_service

    approval_id = await approval_service.try_consume_approved(db, "space.delete", "space", space_id)
    if approval_id is None:
        pending = await approval_service.request_or_get_pending(
            db, user_id, "space.delete", "space", space_id,
            reason=f"删除知识库空间「{space.name}」",
        )
        return {"message": "删除知识库空间需要企业管理员审批，已提交申请，审批通过后请再次删除",
                "approval": pending}

    space_name = space.name  # 下面 delete 之后再取，图省事直接用对象属性容易被读成"删完还能读"的偶然行为
    from sqlalchemy import delete as sa_delete
    from models.init_db import SpaceMember

    await db.execute(sa_delete(SpaceMember).where(SpaceMember.space_id == space_id))
    await dao.delete_space_async(db, space)
    # 审计放在真正删除、真正 commit 之后：kb_audit_dao.record_async 自己会 commit，
    # 挪到前面的话会把 try_consume_approved 那条还没提交的"标已消费" UPDATE 提前
    # 冲掉，跟这次修复要保证的"消费和真正删除同一个事务"正好相反。放在这里，
    # 就算审计这次失败（_audit 自己吞掉异常），空间也已经真的删掉、真的提交过了，
    # 不影响主流程。
    await _audit(db, user_id, "space.delete", space_id=space_id, target_type="space",
                target_id=space_id, detail={"name": space_name})
    return {"message": "已删除", "id": space_id}


async def _audit(db, user_id, action, **kw):
    try:
        await kb_audit_dao.record_async(db, user_id, action, **kw)
    except Exception:  # noqa: BLE001 —— 审计失败不影响主流程
        pass


# ============================ 成员管理 ============================

_MEMBER_ROLES = ("admin", "editor", "viewer")


def _member_dict(m, name: str = "") -> Dict[str, Any]:
    return {
        "user_id": m.user_id,
        "user_name": name,
        "role": m.role,
        "created_at": m.created_at.strftime("%Y-%m-%d %H:%M:%S") if m.created_at else None,
    }


async def _require_manage(db, user_id: int, space_id: int):
    space = await get_owned_space_async(db, user_id, space_id)
    if not space:
        raise NotFound("知识库空间不存在或无权限")
    role = await get_space_role_async(db, user_id, space_id)
    if not membership.can_manage_members(role):
        raise PermissionDenied("只有空间管理员或所有者能管理成员")
    return space, role


async def list_members(db, user_id: int, space_id: int) -> Dict[str, Any]:
    # 任意可访问该空间的成员都能看名单
    space = await get_owned_space_async(db, user_id, space_id)
    if not space:
        raise NotFound("知识库空间不存在或无权限")
    from models.space_member_dao import list_members_async
    from models.user_async_dao import get_users_by_ids_async

    rows = await list_members_async(db, space_id)
    names = await get_users_by_ids_async(db, [space.user_id] + [r.user_id for r in rows])
    my_role = await get_space_role_async(db, user_id, space_id)
    return {
        "owner": {"user_id": space.user_id, "user_name": names.get(space.user_id, ""), "role": "owner"},
        "members": [_member_dict(r, names.get(r.user_id, "")) for r in rows],
        "my_role": my_role,
        "assignable_roles": list(_MEMBER_ROLES),
    }


async def set_member(db, user_id: int, space_id: int, target_user_name: str, role: str) -> Dict[str, Any]:
    space, _ = await _require_manage(db, user_id, space_id)
    if role not in _MEMBER_ROLES:
        raise InvalidInput("角色只能是 admin / editor / viewer")
    from models.space_member_dao import upsert_member_async
    from models.user_async_dao import get_user_by_name_async

    target = await get_user_by_name_async(db, (target_user_name or "").strip())
    if not target:
        raise NotFound(f"用户不存在：{target_user_name}")
    if target.id == space.user_id:
        raise InvalidInput("空间所有者的角色不可更改")
    if target.id == user_id:
        raise InvalidInput("不能修改自己的角色")
    m = await upsert_member_async(db, space_id, target.id, role)
    await _audit(db, user_id, "member.set", space_id=space_id, target_type="member",
                target_id=target.id, detail={"user_name": target.name, "role": role})
    return _member_dict(m, target.name)


async def remove_member(db, user_id: int, space_id: int, target_user_id: int) -> Dict[str, Any]:
    space, _ = await _require_manage(db, user_id, space_id)
    if target_user_id == space.user_id:
        raise InvalidInput("不能移除空间所有者")
    from models.space_member_dao import remove_member_async

    n = await remove_member_async(db, space_id, target_user_id)
    if n:
        await _audit(db, user_id, "member.remove", space_id=space_id, target_type="member",
                    target_id=target_user_id)
    return {"message": "已移除" if n else "该用户不是成员", "user_id": target_user_id}


async def list_audit(db, user_id: int, space_id: int) -> Dict[str, Any]:
    await _require_manage(db, user_id, space_id)
    from models.kb_audit_dao import list_by_space_async
    from models.user_async_dao import get_users_by_ids_async

    rows = await list_by_space_async(db, space_id)
    names = await get_users_by_ids_async(db, [r.user_id for r in rows])
    return {
        "items": [
            {
                "id": r.id,
                "user_id": r.user_id,
                "user_name": names.get(r.user_id, ""),
                "action": r.action,
                "target_type": r.target_type,
                "target_id": r.target_id,
                "detail": r.detail,
                "created_at": r.created_at.strftime("%Y-%m-%d %H:%M:%S") if r.created_at else None,
            }
            for r in rows
        ],
        "total": len(rows),
    }
