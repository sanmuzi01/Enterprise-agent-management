"""批量整理：一次交给 AI 多份材料（比如会议纪要、报销单、邮件各一份），在后台依次整理并显示进度。

每份材料就是一条普通的 AI 工作成果（带 batch_id / batch_name），所以权限、额度、密级、依据校验、业务系统核对、
人工核对后才保存——全部沿用单份整理的规则；批次本身不新增任何业务写入。区别只在三点：
1. 立即返回，整理在后台进行，用户可以离开页面，回来看进度（排队中 / 整理中 / 待核对 / 失败）；
2. 每份材料独立成败：一份失败不影响其他份，失败的可以单独重试，也可以把原文带回手动处理；
3. 同一用户同时只允许一个批次在整理，最多同时 2 份在调用模型，避免把额度和模型连接一次打满。
批次靠 batch_id（客户端生成的 UUID）保证幂等：重复提交同一批次不会重复整理。后台处理跑在 API 进程里，
进程被重启时排队的材料会在 15 分钟后被标成失败（可重试），不会永远卡在“排队中”。
"""
import uuid
from datetime import timedelta
from typing import Any, Dict, List

from sqlalchemy import case, func, select, update

from models.init_db import AutomationWork
from service.automation_work_service import authorize, payload, process_work, recover_interrupted
from service.data_egress_policy import is_model_allowed
from service.exceptions import Conflict, InvalidInput, NotFound, PermissionDenied
from service.llm.llm_config_service import async_get_api_config
from service.quota_service import enforce_quota_async
from service.workflows import get_workflow
from utils.logger_handler import get_logger
from utils.timeutil import utcnow

logger = get_logger("automation_batch")

MAX_ITEMS = 10
MIN_TEXT = 10
MAX_TEXT = 15000
MAX_TOTAL_CHARS = 60000
CONCURRENCY = 2
ACTIVE = ("queued", "processing")


def item_key(batch_id: str, index: int) -> str:
    return str(uuid.uuid5(uuid.UUID(batch_id), f"item-{index}"))


def _summary(works: List[AutomationWork]) -> Dict[str, Any]:
    counts = {"queued": 0, "processing": 0, "ready": 0, "applying": 0, "retry": 0, "applied": 0, "failed": 0}
    for work in works:
        counts[work.status] = counts.get(work.status, 0) + 1
    finished = counts["queued"] == 0 and counts["processing"] == 0
    return {"total": len(works), "counts": counts, "finished": finished,
            "done": len(works) - counts["queued"] - counts["processing"]}


def batch_payload(batch_id: str, works: List[AutomationWork]) -> Dict[str, Any]:
    works = sorted(works, key=lambda w: (w.batch_index if w.batch_index is not None else 0, w.created_at))
    first = works[0]
    items = [{**payload(w, detail=False), "batch_name": w.batch_name, "batch_index": w.batch_index} for w in works]
    return {"batch_id": batch_id, "kind": first.kind, "team_id": first.team_id, "model_name": first.model_name,
            "sensitivity": first.sensitivity, "created_at": first.created_at.isoformat() + "Z",
            **_summary(works), "items": items}


async def _load(db, user_id: int, batch_id: str) -> List[AutomationWork]:
    works = list((await db.execute(select(AutomationWork).where(
        AutomationWork.user_id == user_id, AutomationWork.batch_id == batch_id))).scalars().all())
    if not works:
        raise NotFound("批次不存在")
    return works


async def create_batch(db, user_id: int, data) -> Dict[str, Any]:
    """校验并登记批次（所有材料先落成“排队中”），返回批次状态；真正的整理由 run_batch 在后台做。"""
    batch_id = str(data.batch_id)
    team = await authorize(db, user_id, data.team_id, data.kind)
    existing = list((await db.execute(select(AutomationWork).where(
        AutomationWork.user_id == user_id, AutomationWork.batch_id == batch_id))).scalars().all())
    if existing:
        if existing[0].team_id != data.team_id or existing[0].kind != data.kind:
            raise Conflict("批次编号已被其他材料使用")
        await recover_interrupted(db, user_id, data.team_id)
        return batch_payload(batch_id, await _load(db, user_id, batch_id))
    if not 1 <= len(data.items) <= MAX_ITEMS:
        raise InvalidInput(f"一个批次最多 {MAX_ITEMS} 份材料")
    texts = [item.text.strip() for item in data.items]
    if any(len(t) < MIN_TEXT or len(t) > MAX_TEXT for t in texts):
        raise InvalidInput(f"每份材料需要 {MIN_TEXT} 到 {MAX_TEXT:,} 字")
    if sum(len(t) for t in texts) > MAX_TOTAL_CHARS:
        raise InvalidInput(f"一个批次的材料合计不能超过 {MAX_TOTAL_CHARS:,} 字，请分批整理")
    workflow = get_workflow(data.kind)
    if workflow.needs_customer or workflow.precheck:
        raise InvalidInput(f"「{workflow.name}」需要指定客户，暂不支持批量整理，请单份整理")
    if not is_model_allowed(data.model_name, data.sensitivity):
        raise PermissionDenied("所选模型不允许处理该密级的材料")
    from service.llm.model_catalog import model_type
    if model_type(data.model_name) != "chat":
        raise InvalidInput("请选择聊天模型进行材料整理")
    if not await async_get_api_config(db, user_id, data.model_name):
        raise InvalidInput("请先在设置中连接并启用所选模型")
    await recover_interrupted(db, user_id, data.team_id)
    busy = (await db.execute(select(func.count()).where(
        AutomationWork.user_id == user_id, AutomationWork.batch_id.is_not(None),
        AutomationWork.status.in_(ACTIVE)))).scalar()
    if busy:
        raise Conflict("你有一个批次还在整理中，请等它完成后再提交新的批次")
    await enforce_quota_async(db, user_id)
    for index, item in enumerate(data.items):
        work_id = str(uuid.uuid4())
        db.add(AutomationWork(
            id=work_id, user_id=user_id, team_id=team.id, request_key=item_key(batch_id, index), kind=data.kind,
            model_name=data.model_name, sensitivity=data.sensitivity, source_text=texts[index], status="queued",
            batch_id=batch_id, batch_index=index, batch_name=(item.name or f"材料 {index + 1}")[:255]))
        _emit_item(db, team, user_id, batch_id, work_id)
    await db.commit()   # 材料和它们的事件同一个事务：要么都在、要么都不在，进程随时崩溃也不会出现“排队了但没人处理”
    return batch_payload(batch_id, await _load(db, user_id, batch_id))


def _emit_item(db, team, user_id: int, batch_id: str, work_id: str) -> None:
    from service.events import outbox
    outbox.emit(db, topic="automation.job.v1", event_type="batch.item.queued", aggregate_type="automation_work", aggregate_id=work_id,
                key=batch_id, organization_id=team.organization_id, department_id=team.id,
                payload={"work_id": work_id, "user_id": user_id, "batch_id": batch_id})


async def get_batch(db, user_id: int, batch_id: str) -> Dict[str, Any]:
    works = await _load(db, user_id, batch_id)
    await authorize(db, user_id, works[0].team_id)
    await recover_interrupted(db, user_id, works[0].team_id)
    return batch_payload(batch_id, await _load(db, user_id, batch_id))


async def list_batches(db, user_id: int, team_id: int, limit: int = 10) -> List[Dict[str, Any]]:
    """最近的批次（离开页面后回来继续看进度用）。"""
    await authorize(db, user_id, team_id)
    await recover_interrupted(db, user_id, team_id)
    rows = (await db.execute(select(
        AutomationWork.batch_id, AutomationWork.kind, func.min(AutomationWork.created_at), func.count(),
        func.sum(case((AutomationWork.status.in_(ACTIVE), 1), else_=0)),
        func.sum(case((AutomationWork.status == "failed", 1), else_=0)),
    ).where(AutomationWork.user_id == user_id, AutomationWork.team_id == team_id, AutomationWork.batch_id.is_not(None))
        .group_by(AutomationWork.batch_id, AutomationWork.kind)
        .order_by(func.min(AutomationWork.created_at).desc()).limit(limit))).all()
    return [{"batch_id": b, "kind": k, "created_at": c.isoformat() + "Z", "total": int(n), "active": int(a or 0),
             "failed": int(f or 0), "finished": not a} for b, k, c, n, a, f in rows]


async def retry_item(db, user_id: int, batch_id: str, work_id: str) -> None:
    """把批次里失败的一份重新排队：状态改回“排队中”并登记一个新事件（同一个事务）。"""
    works = await _load(db, user_id, batch_id)
    team = await authorize(db, user_id, works[0].team_id, works[0].kind)
    work = next((w for w in works if w.id == work_id), None)
    if work is None:
        raise NotFound("这份材料不在该批次里")
    claimed = await db.execute(update(AutomationWork).where(
        AutomationWork.id == work.id, AutomationWork.status == "failed", AutomationWork.proposal_json.is_(None),
    ).values(status="queued", error_message=None, updated_at=utcnow()))
    if claimed.rowcount != 1:
        await db.rollback()
        raise Conflict("只有整理失败的材料能重试")
    _emit_item(db, team, user_id, batch_id, work.id)
    await db.commit()


STALE_PROCESSING_SECONDS = 100   # 小于事件租约（120 秒）：租约到期被重新投递时，上一个处理者如果真的崩了，这里能接手


async def handle_item_event(event: Dict[str, Any]) -> None:
    """消费者：整理批次里的一份材料。幂等：已经整理完/失败的材料再收到事件直接跳过；
    进程崩溃时材料停在“整理中”，事件租约到期重新投递后由这里接手继续整理，而不是标成失败。"""
    from models.async_db import AsyncSessionLocal
    from service.events.runner import TryAgain
    work_id, user_id = event["payload"]["work_id"], int(event["payload"]["user_id"])
    async with AsyncSessionLocal() as db:
        stale = utcnow() - timedelta(seconds=STALE_PROCESSING_SECONDS)
        claimed = await db.execute(update(AutomationWork).where(
            AutomationWork.id == work_id, AutomationWork.user_id == user_id,
            (AutomationWork.status == "queued") | ((AutomationWork.status == "processing") & (AutomationWork.updated_at < stale))
            # 死信里的事件被管理员重新投递：这份材料当时被标成了失败，现在要重新整理（已有整理结果的不会被覆盖）
            | ((AutomationWork.status == "failed") & AutomationWork.proposal_json.is_(None)),
        ).values(status="processing", updated_at=utcnow()))
        await db.commit()
        work = (await db.execute(select(AutomationWork).where(AutomationWork.id == work_id))).scalar_one_or_none()
        if work is None:
            return   # 材料已被删除（用户注销等）：没有什么可做的
        if claimed.rowcount != 1:
            if work.status == "processing":
                raise TryAgain("这份材料正在被另一个处理者整理")
            return   # 已经整理完成或已失败：重复投递，跳过

        async def fail(message: str) -> None:
            work.status, work.error_message, work.updated_at = "failed", message[:300], utcnow()
            await db.commit()
        try:
            await authorize(db, user_id, work.team_id, work.kind)   # 排队期间成员身份可能已被撤销
        except PermissionDenied:
            return await fail("当前企业或部门身份已失效，无法整理")
        try:
            await enforce_quota_async(db, user_id)
        except Exception as exc:  # noqa: BLE001 —— 额度用尽等：这一份失败，不影响已完成的
            return await fail(getattr(exc, "message", None) or "额度不足，无法整理")
        if not await async_get_api_config(db, user_id, work.model_name):
            return await fail("所选模型已不可用，请重新连接模型后重试")
        await process_work(db, work)


async def handle_item_dead(event: Dict[str, Any], error: str) -> None:
    """事件重试用尽：把这份材料标成失败（用户能看到原因并重试），不让它永远停在排队/整理中。"""
    await _mark_failed(event["payload"]["work_id"], int(event["payload"]["user_id"]))


async def _mark_failed(work_id: str, user_id: int) -> None:
    from models.async_db import AsyncSessionLocal
    try:
        async with AsyncSessionLocal() as db:
            await db.execute(update(AutomationWork).where(
                AutomationWork.id == work_id, AutomationWork.user_id == user_id, AutomationWork.status.in_(ACTIVE),
            ).values(status="failed", error_message="处理时出现异常，请重试这份材料", updated_at=utcnow()))
            await db.commit()
    except Exception:  # noqa: BLE001
        logger.exception("标记失败时出错: %s", work_id)
