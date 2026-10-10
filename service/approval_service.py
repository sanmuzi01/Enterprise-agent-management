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
from sqlalchemy.exc import IntegrityError

from models.init_db import ApprovalRequest
from service import audit_service
from service.exceptions import Conflict, InvalidInput, NotFound, PermissionDenied
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


def _dedupe_key(action: str, resource_type: str, resource_id: int) -> str:
    return f"{action}:{resource_type}:{resource_id}"


async def request_or_get_pending(
        db, applicant_id: int, action: str, resource_type: str, resource_id: int,
        reason: Optional[str] = None, ttl_hours: int = _DEFAULT_TTL_HOURS,
) -> Dict[str, Any]:
    """幂等：同一个 (action, resource_type, resource_id) 已经有未过期的 pending/approved
    审批单就直接返回那一条，不重复建单——避免用户连点几次删除按钮堆出一堆重复审批单。

    P1 并发修复：下面这段"先查一遍没有才插入"本身在两个并发请求同时打进来时不是
    原子的——都读到"没有活跃单"，都会各自往下建一条，谁都不知道对方也建了一条。
    真正兜底的是 `ApprovalRequest.active_dedupe_key` 上的数据库唯一约束
    （`models/init_db.py`）：insert 时把它设成这个 (action, resource_type,
    resource_id) 的固定字符串，两个并发 insert 里数据库只会让一个成功，另一个会
    因为唯一键冲突报 `IntegrityError`——这时不是把错误抛给用户，而是当成"对方已经
    帮我建好了"，回滚后重新查一遍，把那一条返回给调用方，跟单纯拿到它的语义完全
    一样，调用方无感知。
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
    if existing is not None:
        # 之前只是在 Python 里判断"过期了所以不用它"，从没真的把这条旧单标成
        # expired、也没释放它占着的 dedupe key——旧单一直挂着 pending/approved
        # 状态，加了唯一约束之后会挡住下面新单的 insert。这里顺手把它结清。
        await db.execute(
            update(ApprovalRequest)
            .where(ApprovalRequest.id == existing.id, ApprovalRequest.status.in_(_ACTIVE_STATUSES))
            .values(status="expired", active_dedupe_key=None)
        )
        await db.commit()

    row = ApprovalRequest(
        applicant_id=applicant_id, action=action, resource_type=resource_type,
        resource_id=resource_id, reason=reason, status="pending",
        expires_at=utcnow() + timedelta(hours=ttl_hours),
        active_dedupe_key=_dedupe_key(action, resource_type, resource_id),
    )
    db.add(row)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        winner = (await db.execute(
            select(ApprovalRequest).where(
                ApprovalRequest.action == action,
                ApprovalRequest.resource_type == resource_type,
                ApprovalRequest.resource_id == resource_id,
                ApprovalRequest.status.in_(_ACTIVE_STATUSES),
            ).order_by(ApprovalRequest.id.desc())
        )).scalars().first()
        if winner is None:
            # 理论上不该发生（唯一键冲突说明一定有一条活跃单存在）；真出现说明
            # 有更复杂的并发时序，直接把原始冲突抛出去，不要装作成功了。
            raise
        return _to_dict(winner)
    await audit_service.record_async(
        applicant_id, f"{action}.approval_requested",
        resource_type=resource_type, resource_id=resource_id, detail={"approval_id": row.id},
    )
    return _to_dict(row)


async def resource_org_id(db, resource_type: str, resource_id: int) -> Optional[int]:
    """审批单所指资源属于哪个企业；未登记的资源类型返回 None（HTTP 层按“不可见”处理）。
    新增审批类型时必须在这里登记资源 → 企业的对应关系，否则任何企业的管理员都看不到、也批不了它。"""
    if resource_type == "space":
        from models.init_db import KnowledgeSpace
        return (await db.execute(select(KnowledgeSpace.organization_id).where(KnowledgeSpace.id == resource_id))).scalar()
    return None


async def list_pending(db, limit: int = 50, approver_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """approver_id 给定时只返回这位管理员所管企业的审批单（别的企业管理员看不到）；不给则不过滤（内部调用 / 测试）。"""
    rows = (await db.execute(
        select(ApprovalRequest).where(ApprovalRequest.status == "pending")
        .order_by(ApprovalRequest.id.desc()).limit(limit if approver_id is None else max(limit * 10, 500))
    )).scalars().all()
    if approver_id is None:
        return [_to_dict(r) for r in rows]
    from service.enterprise_access import admin_org_ids_async
    orgs = set(await admin_org_ids_async(db, approver_id))
    visible = []
    for row in rows:
        if await resource_org_id(db, row.resource_type, row.resource_id) in orgs:
            visible.append(_to_dict(row))
        if len(visible) >= limit:
            break
    return visible


async def decide(db, approval_id: int, approver_id: int, approve: bool, *, enforce_org: bool = False) -> Dict[str, Any]:
    """P1 并发修复：之前是"读一次 row、在 Python 里判断、再逐个字段赋值、最后
    commit"，两个管理员并发点"批准"/"拒绝"同一条单子，都能读到 status=pending，
    都会走到底下的写入，最后提交的那个会把先提交的那个悄悄覆盖掉——数据库里
    最终状态取决于谁的事务后提交，不是谁先点的，而且两条 `approval_{status}`
    审计记录都会被写下来，看起来像是"先批准又被拒绝"，其实是两个人互相不知道
    对方也点了。改成条件 UPDATE（`WHERE id=:id AND status='pending'`），受影响
    行数为 0 就说明在我们读到 pending 之后、真正写之前，已经被别的并发请求抢先
    决定过了——直接报冲突，不覆盖，前端提示管理员刷新页面看真实结果。
    """
    row = (await db.execute(
        select(ApprovalRequest).where(ApprovalRequest.id == approval_id)
    )).scalars().first()
    if row is None:
        raise NotFound("审批单不存在")
    if enforce_org:
        # 企业管理员只能决定自己企业的审批单；别的企业的、以及没登记所属企业的一律当作不存在（不泄露它存在）
        from service.enterprise_access import admin_org_ids_async
        if await resource_org_id(db, row.resource_type, row.resource_id) not in set(await admin_org_ids_async(db, approver_id)):
            raise NotFound("审批单不存在")
    if approver_id == row.applicant_id:
        # 双人审批的底线：申请人是企业管理员时，`require_org_role("admin")` 本身
        # 挡不住他给自己的申请签字——这条必须在这里单独判断，不能只靠角色门槛。
        raise PermissionDenied("不能审批自己提交的申请")
    if row.status != "pending":
        raise InvalidInput(f"审批单已经是 {row.status} 状态，不能重复决定")
    if _is_expired(row):
        await db.execute(
            update(ApprovalRequest)
            .where(ApprovalRequest.id == approval_id, ApprovalRequest.status == "pending")
            .values(status="expired", active_dedupe_key=None)
        )
        await db.commit()
        raise InvalidInput("审批单已过期，请重新提交")

    new_status = "approved" if approve else "rejected"
    values: Dict[str, Any] = {"approver_id": approver_id, "status": new_status, "decided_at": utcnow()}
    if new_status == "rejected":
        # approved 之后仍然算"活跃"（还没被 try_consume_approved 消费），继续占着
        # dedupe key，跟 request_or_get_pending 判断"要不要复用"的 _ACTIVE_STATUSES
        # 语义一致；rejected 是终态，释放掉让同一个资源可以重新发起申请。
        values["active_dedupe_key"] = None
    result = await db.execute(
        update(ApprovalRequest)
        .where(ApprovalRequest.id == approval_id, ApprovalRequest.status == "pending")
        .values(**values)
    )
    await db.commit()
    if result.rowcount == 0:
        raise Conflict("审批单已被其他人抢先决定，请刷新后重试")
    await db.refresh(row)
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

    第五轮审计 P1-5：这里**不 commit**——调用方拿到非 None 的 id 后，必须在同一个
    还没提交的事务里接着做真正的业务操作（比如删除知识空间），最后由那次操作
    自己的 commit 把"标记已消费"和"真正执行"一起提交。之前这里自己 commit 一次，
    等于把这两件事拆成两个独立事务：如果后面真正执行那一步失败（DB 故障、进程崩溃），
    审批已经被标记消费掉了，但业务没有成功，而且旧审批用不了、新审批又申请不了
    （dedupe key 还占着），资源卡死在一个既没删成又申请不了新审批的状态。现在
    真正执行那一步失败时，如果调用方的 session 因为异常没提交就被关闭/回滚
    （FastAPI 的 AsyncSession 依赖退出时的默认行为），这条 UPDATE 会跟着一起
    回滚，审批单恢复成"approved、未消费"，下次重试会重新走到这里、重新消费，
    不会卡死。调用方必须确保消费和真正执行之间不能有中间提交。
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
    if result.rowcount == 0:
        return None
    return row.id
