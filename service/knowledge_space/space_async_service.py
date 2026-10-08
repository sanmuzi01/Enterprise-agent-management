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


def _scope_of(space, role: str) -> str:
    """personal = 我自己的；shared = 别人分享给我的；department = 划分给部门的；enterprise = 划分给全企业的。"""
    if space.scope_type in ("department", "enterprise"):
        return space.scope_type
    return "personal" if role == "owner" else "shared"


def _to_dict(space, stats: Dict[str, int] = None, *, role: str = "owner", departments: Optional[list] = None) -> Dict[str, Any]:
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
        "scope": _scope_of(space, role),
        "departments": departments or [],        # 被划分给了哪些部门（scope 为 department 时）
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
    # 自己的 + 被加入的 + 划分给本人所在部门 / 全企业的（绝密除外）
    from service.access_control import user_space_ids_async

    spaces = await dao.list_spaces_by_ids_async(db, sorted(await user_space_ids_async(db, user_id), reverse=True))
    departments = await enterprise_dao.get_space_departments_async(db, [s.id for s in spaces if s.scope_type == "department"])
    items = []
    for s in spaces:
        stats = await dao.live_stats_async(db, s.id)
        role = "owner" if s.user_id == user_id else (await get_space_role_async(db, user_id, s.id) or "viewer")
        items.append(_to_dict(s, stats, role=role, departments=departments.get(s.id)))
    return {
        "items": items,
        "purposes": [{"key": k, "label": PURPOSE_LABELS[k]} for k in PURPOSES],
        "sensitivities": [{"key": k, "label": SENSITIVITY_LABELS[k]} for k in SENSITIVITIES],
    }


async def get_space(db, user_id: int, space_id: int) -> Dict[str, Any]:
    space = await get_owned_space_async(db, user_id, space_id)
    if not space:
        raise NotFound("知识库空间不存在或无权限")
    role = await get_space_role_async(db, user_id, space_id) or "viewer"
    stats = await dao.live_stats_async(db, space_id)
    departments = await enterprise_dao.get_space_departments_async(db, [space_id])
    return _to_dict(space, stats, role=role, departments=departments.get(space_id))


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


async def create_space(db, user_id: int, payload: Dict[str, Any]) -> Dict[str, Any]:
    """创建个人知识库空间。划分给部门 / 全企业是管理员在「企业知识库」里统一做的事，这里不接受。"""
    fields = _clean_create(payload)
    if payload.get("sensitivity") is not None:
        fields["sensitivity"] = _clean_sensitivity(payload["sensitivity"])
    space = await dao.create_space_async(db, user_id, fields)
    return _to_dict(space, {"doc_count": 0, "chunk_count": 0, "bound_agent_count": 0})


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
        if fields["sensitivity"] != space.sensitivity and space.scope_type != "personal":
            raise PermissionDenied("已经划分给部门或全企业的知识库，密级由管理员在「企业知识库」里调整")
    if not fields:
        raise InvalidInput("没有需要更新的内容")
    space = await dao.update_space_async(db, space, fields)
    await _audit(db, user_id, "space.update", space_id=space_id, target_type="space",
                target_id=space_id, detail={"fields": sorted(fields.keys()), "sensitivity": space.sensitivity})
    stats = await dao.live_stats_async(db, space_id)
    departments = await enterprise_dao.get_space_departments_async(db, [space_id])
    return _to_dict(space, stats, role=role, departments=departments.get(space_id))


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
