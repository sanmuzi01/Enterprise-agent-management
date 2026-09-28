"""高风险操作的审批单（Phase 3D 阶段4，docs/enterprise-rbac-plan.md 9.5）。

平台审批（发布高权限Skill、改权限策略、导出限制级数据、删知识空间）由企业管理员
（`require_org_role("admin")`）决定，跟业务审批（请假/采购之类，属于未来 Phase 5 的
Spring Boot 业务中心）是两条不同的线，不混在一起。

只有异步版：目前唯一的调用方（知识库空间删除）是全异步模块，没有同步调用方就不建
一份同步双胞胎——等真的有同步调用方再补，不提前建不会被用到的代码。
"""
from datetime import timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import select, update

from models.init_db import ApprovalRequest
from service import audit_service
from service.exceptions import InvalidInput, NotFound, PermissionDenied
from utils.timeutil import utcnow

_ACTIVE_STATUSES = ("pending", "approved")
_DEFAULT_TTL_HOURS = 72


def _to_dict(row: ApprovalRequest) -> Dict[str, Any]:
    return {
        "id": row.id,
        "applicant_id": row.applicant_id,
        "approver_id": row.approver_id,
        "action": row.action,
        "resource_type": row.resource_type,
        "resource_id": row.resource_id,
        "reason": row.reason,
        "status": row.status,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
        "decided_at": row.decided_at.isoformat() if row.decided_at else None,
        "executed_at": row.executed_at.isoformat() if row.executed_at else None,
    }


def _is_expired(row: ApprovalRequest) -> bool:
    return bool(row.expires_at and row.expires_at < utcnow())


async def request_or_get_pending(
        db, applicant_id: int, action: str, resource_type: str, resource_id: int,
        reason: Optional[str] = None, ttl_hours: int = _DEFAULT_TTL_HOURS,
) -> Dict[str, Any]:
    """幂等：同一个 (action, resource_type, resource_id) 已经有未过期的 pending/approved
    审批单就直接返回那一条，不重复建单——避免用户连点几次删除按钮堆出一堆重复审批单。
    """
    existing = (await db.execute(
        select(ApprovalRequest).where(
            ApprovalRequest.action == action,
            ApprovalRequest.resource_type == resource_type,
            ApprovalRequest.resource_id == resource_id,
            ApprovalRequest.status.in_(_ACTIVE_STATUSES),
        ).order_by(ApprovalRequest.id.desc())
    )).scalars().first()
    if existing is not None and not _is_expired(existing):
        return _to_dict(existing)

    row = ApprovalRequest(
        applicant_id=applicant_id, action=action, resource_type=resource_type,
        resource_id=resource_id, reason=reason, status="pending",
        expires_at=utcnow() + timedelta(hours=ttl_hours),
    )
    db.add(row)
    await db.commit()
    await audit_service.record_async(
        applicant_id, f"{action}.approval_requested",
        resource_type=resource_type, resource_id=resource_id, detail={"approval_id": row.id},
    )
    return _to_dict(row)


async def list_pending(db, limit: int = 50) -> List[Dict[str, Any]]:
    rows = (await db.execute(
        select(ApprovalRequest).where(ApprovalRequest.status == "pending")
        .order_by(ApprovalRequest.id.desc()).limit(limit)
    )).scalars().all()
    return [_to_dict(r) for r in rows]


async def decide(db, approval_id: int, approver_id: int, approve: bool) -> Dict[str, Any]:
    row = (await db.execute(
        select(ApprovalRequest).where(ApprovalRequest.id == approval_id)
    )).scalars().first()
    if row is None:
        raise NotFound("审批单不存在")
    if approver_id == row.applicant_id:
        # 双人审批的底线：申请人是企业管理员时，`require_org_role("admin")` 本身
        # 挡不住他给自己的申请签字——这条必须在这里单独判断，不能只靠角色门槛。
        raise PermissionDenied("不能审批自己提交的申请")
    if row.status != "pending":
        raise InvalidInput(f"审批单已经是 {row.status} 状态，不能重复决定")
    if _is_expired(row):
        row.status = "expired"
        await db.commit()
        raise InvalidInput("审批单已过期，请重新提交")

    row.approver_id = approver_id
    row.status = "approved" if approve else "rejected"
    row.decided_at = utcnow()
    await db.commit()
    await audit_service.record_async(
        approver_id, f"{row.action}.approval_{row.status}",
        resource_type=row.resource_type, resource_id=row.resource_id,
        detail={"approval_id": row.id},
    )
    return _to_dict(row)


async def try_consume_approved(db, action: str, resource_type: str, resource_id: int) -> Optional[int]:
    """有一条 approved、未过期、还没被消费过的审批单就标 `executed_at` 并返回它的 id；
    否则返回 None。调用方拿到非 None 就放行真正的高风险操作，拿到 None 就转去调
    `request_or_get_pending` 建单/复用一条已有的 pending 单。

    "先查再改"两步分开做在并发下不安全——两个请求可能都读到 `executed_at IS NULL`，
    都以为自己抢到了这条审批单，都去执行了一遍高风险操作。改成一条条件 UPDATE
    （`WHERE id=:id AND executed_at IS NULL`），受影响行数为 0 就说明被别的并发请求
    抢先消费了：UPDATE 语句本身对目标行是加锁的"当前读"，不是普通 SELECT 那种快照读，
    两个并发事务里只有一个能真的把 `executed_at` 从 NULL 改成非 NULL，这是数据库
    保证的原子性，不需要应用层自己加锁。
    """
    row = (await db.execute(
        select(ApprovalRequest).where(
            ApprovalRequest.action == action,
            ApprovalRequest.resource_type == resource_type,
            ApprovalRequest.resource_id == resource_id,
            ApprovalRequest.status == "approved",
            ApprovalRequest.executed_at.is_(None),
        ).order_by(ApprovalRequest.id.desc())
    )).scalars().first()
    if row is None or _is_expired(row):
        return None

    result = await db.execute(
        update(ApprovalRequest)
        .where(ApprovalRequest.id == row.id, ApprovalRequest.executed_at.is_(None))
        .values(executed_at=utcnow())
    )
    await db.commit()
    if result.rowcount == 0:
        return None
    return row.id
