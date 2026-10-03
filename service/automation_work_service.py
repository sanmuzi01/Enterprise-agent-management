"""Department workflows: source material -> reviewed proposal -> durable business draft."""
import asyncio
import json
import time
import uuid
from datetime import timedelta

from sqlalchemy import select, update, func, case
from sqlalchemy.exc import IntegrityError

from models.init_db import AutomationWork, Team, Organization, OrganizationMember, TeamMember
from service import enterprise_hub_client as hub
from service.automation_spec import extraction_prompt, parse_answer, validate_proposal
from service.workflows import catalog as workflow_catalog, get_workflow
from service.workflows.business_checks import run_checks
from service.data_egress_policy import is_model_allowed
from service.exceptions import Conflict, InvalidInput, NotFound, PermissionDenied
from service.llm.llm_service import async_chat_with_usage
from service.llm.llm_config_service import async_get_api_config
from service.quota_service import enforce_quota_async
from utils.timeutil import utcnow


DEFINITE_REJECTIONS = {400, 403, 404, 422}


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


async def authorize(db, user_id, team_id, kind=None):
    team = (await db.execute(
        select(Team).join(Organization, Organization.id == Team.organization_id)
        .join(OrganizationMember, OrganizationMember.organization_id == Organization.id)
        .join(TeamMember, TeamMember.team_id == Team.id)
        .where(Team.id == team_id, Team.status == "active", Organization.status == "active",
               OrganizationMember.user_id == user_id, OrganizationMember.status == "active",
               TeamMember.user_id == user_id, TeamMember.status == "active")
    )).scalar_one_or_none()
    if team is None:
        raise PermissionDenied("当前企业或部门身份无效，无法查看或处理工作成果")
    if kind is not None:
        workflow = get_workflow(kind)
        if not workflow.available_for(team.department_code):
            raise PermissionDenied(f"「{workflow.name}」仅对{workflow.availability_label()}开放")
    return team


async def workflows_for_team(db, user_id, team_id):
    team = await authorize(db, user_id, team_id)
    return workflow_catalog(team.department_code)


def payload(work, detail=True):
    result = {"id": work.id, "team_id": work.team_id, "kind": work.kind, "status": work.status,
              "model_name": work.model_name, "sensitivity": work.sensitivity, "customer_id": work.customer_id,
              "elapsed_ms": work.elapsed_ms, "total_tokens": work.total_tokens,
              "edited": bool(work.edited), "error_message": work.error_message,
              "created_at": work.created_at.isoformat() + "Z",
              "proposal": json.loads(work.accepted_json or work.proposal_json or "null"),
              "business_result": json.loads(work.business_result_json or "null"),
              "business_checks": json.loads(work.business_checks_json or "[]"),
              "completed_tasks": json.loads(work.completed_tasks_json or "[]")}
    if detail:
        result["source_text"] = work.source_text
    return result


async def get_work(db, user_id, work_id):
    work = (await db.execute(select(AutomationWork).where(
        AutomationWork.id == work_id, AutomationWork.user_id == user_id))).scalar_one_or_none()
    if work is None:
        raise NotFound("工作成果不存在")
    await authorize(db, user_id, work.team_id)
    await recover_interrupted(db, user_id, work.team_id)
    await db.refresh(work)
    return work


async def recover_interrupted(db, user_id, team_id):
    # Model calls have a 90s deadline. A process killed mid-call must not leave a permanent spinner.
    await db.execute(update(AutomationWork).where(
        AutomationWork.user_id == user_id, AutomationWork.team_id == team_id,
        AutomationWork.status == "processing", AutomationWork.updated_at < utcnow() - timedelta(minutes=3),
    ).values(status="failed", error_message="处理被中断，请从原文重新整理", updated_at=utcnow()))
    await db.commit()


async def generate(db, user_id, data):
    await authorize(db, user_id, data.team_id, data.kind)
    existing = (await db.execute(select(AutomationWork).where(
        AutomationWork.user_id == user_id, AutomationWork.request_key == str(data.request_key)
    ))).scalar_one_or_none()
    if existing:
        if (existing.team_id, existing.kind, existing.source_text, existing.customer_id, existing.model_name,
            existing.sensitivity) != (data.team_id, data.kind, data.source_text, data.customer_id,
                                      data.model_name, data.sensitivity):
            raise Conflict("请求编号已被不同材料使用")
        return payload(existing)
    if not is_model_allowed(data.model_name, data.sensitivity):
        raise PermissionDenied("所选模型不允许处理该密级的材料")
    from service.llm.model_catalog import model_type
    if model_type(data.model_name) != "chat":
        raise InvalidInput("请选择聊天模型进行材料整理")
    if not await async_get_api_config(db, user_id, data.model_name):
        raise InvalidInput("请先在设置中连接并启用所选模型")
    await enforce_quota_async(db, user_id)
    workflow = get_workflow(data.kind)
    if not workflow.needs_customer and data.customer_id is not None:
        raise InvalidInput("当前工作类型不接受客户编号")
    if workflow.precheck:
        await workflow.precheck(db, user_id, data.team_id, data)
    work = AutomationWork(id=str(uuid.uuid4()), user_id=user_id, team_id=data.team_id,
                          request_key=str(data.request_key), kind=data.kind, model_name=data.model_name,
                          sensitivity=data.sensitivity, source_text=data.source_text,
                          customer_id=data.customer_id, status="processing")
    db.add(work)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise Conflict("该材料正在处理，请刷新工作成果") from None
    started = time.monotonic()
    try:
        answer, usage = await asyncio.wait_for(async_chat_with_usage(
            db, user_id, data.model_name, extraction_prompt(data.kind), [], data.source_text, temperature=0,
        ), timeout=90)
        work.total_tokens = int(usage.get("total_tokens", 0)) if usage else None
        result = parse_answer(data.kind, answer, data.source_text)
        serialized = encode(result)
        if len(serialized.encode("utf-8")) > 50000:
            raise InvalidInput("整理结果过长，请拆分材料")
        work.proposal_json = serialized
        work.status = "ready"
        work.business_checks_json = encode(await run_checks(get_workflow(data.kind), user_id, data.team_id, result, work))
    except InvalidInput as exc:
        work.status, work.error_message = "failed", exc.message
    except Exception:
        # Do not persist provider responses, credentials or source excerpts in error messages.
        work.status, work.error_message = "failed", "模型整理失败或超时，请检查模型连接后重新整理"
    work.elapsed_ms = int((time.monotonic() - started) * 1000)
    work.updated_at = utcnow()
    await db.commit()
    # Membership might have been revoked while the model was running.
    await authorize(db, user_id, data.team_id)
    return payload(work)


async def history(db, user_id, team_id, offset=0):
    await authorize(db, user_id, team_id)
    await recover_interrupted(db, user_id, team_id)
    scope = [AutomationWork.user_id == user_id, AutomationWork.team_id == team_id]
    rows = (await db.execute(select(AutomationWork).where(*scope)
                            .order_by(AutomationWork.created_at.desc(), AutomationWork.id)
                            .offset(offset).limit(20))).scalars().all()
    counts = (await db.execute(select(
        func.count(),
        func.sum(case((AutomationWork.status == "applied", 1), else_=0)),
        func.sum(case((AutomationWork.status == "ready", 1), else_=0)),
        func.sum(case((AutomationWork.status == "failed", 1), else_=0)),
        func.sum(AutomationWork.elapsed_ms), func.sum(AutomationWork.total_tokens),
        func.sum(AutomationWork.edited),
    ).select_from(AutomationWork).where(*scope))).one()
    keys = ["total", "applied", "ready", "failed", "elapsed_ms", "total_tokens", "edited"]
    by_kind = (await db.execute(select(
        AutomationWork.kind, func.count(),
        func.sum(case((AutomationWork.status == "applied", 1), else_=0)),
        func.sum(case((AutomationWork.status == "failed", 1), else_=0)),
        func.sum(AutomationWork.edited),
    ).where(*scope).group_by(AutomationWork.kind))).all()
    stats = dict(zip(keys, [int(c or 0) for c in counts]))
    stats["by_kind"] = {kind: {"total": int(t or 0), "applied": int(a or 0), "failed": int(f or 0),
                               "edited": int(e or 0)} for kind, t, a, f, e in by_kind}
    return {"items": [payload(w, detail=False) for w in rows], "stats": stats}


async def apply_work(db, user_id, work_id, proposal):
    work = await get_work(db, user_id, work_id)
    await authorize(db, user_id, work.team_id, work.kind)
    accepted = encode(validate_proposal(work.kind, proposal, work.source_text, for_save=True))
    if len(accepted.encode("utf-8")) > 50000:
        raise InvalidInput("整理结果过长")
    if work.status == "applied":
        if accepted != work.accepted_json:
            raise Conflict("此成果已保存，不能更换内容重复保存")
        return payload(work)
    if work.status not in ("ready", "applying", "retry"):
        raise Conflict("当前成果不能保存，请等待整理完成")
    if work.status != "ready" and work.accepted_json and accepted != work.accepted_json:
        raise Conflict("首次保存已锁定内容；重试必须使用相同内容")
    if work.status == "applying" and work.updated_at > utcnow() - timedelta(minutes=5):
        raise Conflict("正在保存业务草稿，请稍后刷新")
    previous_status, previous_updated = work.status, work.updated_at
    claimed = await db.execute(update(AutomationWork).where(
        AutomationWork.id == work.id, AutomationWork.status == previous_status,
        AutomationWork.updated_at == previous_updated,
    ).values(status="applying", accepted_json=accepted, updated_at=utcnow(),
             edited=int(accepted != work.proposal_json), error_message=None,
             apply_attempts=AutomationWork.apply_attempts + 1))
    if claimed.rowcount != 1:
        await db.rollback()
        raise Conflict("另一请求正在保存，请刷新")
    await db.commit()
    # A retry always uses the persisted body and the SAME Java idempotency key.
    try:
        target = get_workflow(work.kind).write(json.loads(accepted), work)
        result = await asyncio.to_thread(hub.call, "POST", target.path, user_id, work.team_id,
                                        [target.scope], target.operation, json_body=target.body,
                                        idempotency_key=f"automation-{work.id}")
        work.business_result_json = encode(result)
        work.status = "applied"
        work.applied_at = work.applied_at or utcnow()
        work.error_message = None
    except hub.EnterpriseHubError as exc:
        # Java business errors are thrown inside IdempotencyService.execute's transaction,
        # so the placeholder rolls back with them: a 400/403/404 for this key proves nothing
        # was ever committed under it, even after an earlier ambiguous attempt. Unlocking the
        # body is then safe. 409 (key in flight) and 5xx stay locked for same-body retries.
        if exc.status_code in DEFINITE_REJECTIONS:
            work.status = "ready"
            work.error_message = f"业务系统未接受：{exc.detail}。请修改后重新保存，或将原文带回重新整理"[:300]
        else:
            work.status = "retry"
            work.error_message = "业务服务暂未接受保存，请检查服务及业务权限后按原内容重试"
    except Exception:
        work.status = "retry"
        work.error_message = "业务服务未确认保存结果；可用同一内容重试，不会主动生成新的操作编号"
    work.updated_at = utcnow()
    await db.commit()
    if work.status == "applied":
        await _publish_followups(db, work)
    return payload(work)


async def _publish_followups(db, work):
    """把成果里的后续事项放进统一待办中心（幂等：同一成果同一条只建一次）。"""
    workflow = get_workflow(work.kind)
    if not workflow.followups:
        return
    from service import work_item_service
    fu = workflow.followups
    for index, task in enumerate(json.loads(work.accepted_json).get(fu["key"]) or []):
        await work_item_service.upsert(
            db, work.user_id, "automation", f"automation:{work.id}:{index}", title=task[fu["title"]],
            team_id=work.team_id, detail=f"来自{workflow.name}（{workflow.draft_name}草稿 #"
                                         f"{(json.loads(work.business_result_json or '{}') or {}).get('id', '')}）",
            link="/department", due_at=work_item_service.parse_due(task.get(fu["due"])))


async def recheck(db, user_id, work_id, proposal):
    """用员工修改后的内容重新做业务系统核对（只读），结论随成果保存。"""
    work = await get_work(db, user_id, work_id)
    await authorize(db, user_id, work.team_id, work.kind)
    data = validate_proposal(work.kind, proposal, work.source_text)
    checks = await run_checks(get_workflow(work.kind), user_id, work.team_id, data, work)
    await db.execute(update(AutomationWork).where(AutomationWork.id == work.id)
                     .values(business_checks_json=encode(checks)))
    await db.commit()
    await db.refresh(work)
    return payload(work)


async def complete_task(db, user_id, work_id, index, done, sync_work_item=True):
    work = await get_work(db, user_id, work_id)
    followups = get_workflow(work.kind).followups
    if followups is None:
        raise InvalidInput("这类工作成果没有后续待办")
    if work.status != "applied":
        raise Conflict("请先保存业务草稿再处理后续待办")
    tasks = json.loads(work.accepted_json)[followups["key"]]
    if not 0 <= index < len(tasks):
        raise InvalidInput("待办不存在")
    previous = work.completed_tasks_json
    completed = set(json.loads(previous))
    completed.add(index) if done else completed.discard(index)
    result = await db.execute(update(AutomationWork).where(
        AutomationWork.id == work.id, AutomationWork.completed_tasks_json == previous,
    ).values(completed_tasks_json=encode(sorted(completed)), updated_at=utcnow()))
    if result.rowcount != 1:
        await db.rollback()
        raise Conflict("待办状态已变化，请刷新")
    await db.commit()
    if sync_work_item:
        from models.init_db import WorkItem
        await db.execute(update(WorkItem).where(
            WorkItem.user_id == user_id, WorkItem.source_key == f"automation:{work.id}:{index}"
        ).values(status="done" if done else "open", resolved_by="user" if done else None,
                 completed_at=utcnow() if done else None))
        await db.commit()
    await db.refresh(work)
    return payload(work)
