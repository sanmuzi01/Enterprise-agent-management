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
    if kind == "crm" and team.department_code != "sales":
        raise PermissionDenied("CRM 材料整理仅对销售部门开放")
    if kind == "procurement" and team.department_code != "procurement":
        raise PermissionDenied("采购材料整理仅对采购部门开放")
    return team


def payload(work, detail=True):
    result = {"id": work.id, "team_id": work.team_id, "kind": work.kind, "status": work.status,
              "model_name": work.model_name, "sensitivity": work.sensitivity, "customer_id": work.customer_id,
              "elapsed_ms": work.elapsed_ms, "total_tokens": work.total_tokens,
              "edited": bool(work.edited), "error_message": work.error_message,
              "created_at": work.created_at.isoformat() + "Z",
              "proposal": json.loads(work.accepted_json or work.proposal_json or "null"),
              "business_result": json.loads(work.business_result_json or "null"),
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
    if data.kind == "crm":
        if data.customer_id is None:
            raise InvalidInput("请先选择当前部门的客户")
        from service.crm_workspace_service import get_customer_summary_async
        await get_customer_summary_async(db, user_id, data.team_id, data.customer_id)
    elif data.customer_id is not None:
        raise InvalidInput("当前工作类型不接受客户编号")
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
    return {"items": [payload(w, detail=False) for w in rows],
            "stats": dict(zip(keys, [int(c or 0) for c in counts]))}


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
             edited=int(accepted != work.proposal_json), error_message=None))
    if claimed.rowcount != 1:
        await db.rollback()
        raise Conflict("另一请求正在保存，请刷新")
    await db.commit()
    # A retry always uses the persisted body and the SAME Java idempotency key.
    try:
        data = json.loads(accepted)
        if work.kind == "crm":
            path, scope, operation = f"/crm/customers/{work.customer_id}/followups", "crm.write", "create_followup_draft"
            body = {"content": data["content"]}
        elif work.kind == "procurement":
            path, scope, operation = "/procurement/requests", "procurement.write", "create_purchase_draft"
            body = {"lines": [{"sku": item["sku"], "quantity": item["quantity"]} for item in data["items"]]}
        elif work.kind == "leave":
            path, scope, operation = "/oa/leave/requests", "oa.leave.write", "create_leave_draft"
            body = {"leaveTypeCode": data["leave_type_code"], "startDate": data["start_date"],
                    "endDate": data["end_date"], "reason": data["reason"]}
        else:
            path, scope, operation = "/finance/expenses", "finance.write", "create_expense_draft"
            body = {"lines": [{"category": line["category"], "amount": line["amount"],
                               "description": line["description"], "invoiceNo": line.get("invoice_no")}
                              for line in data["lines"]]}
        result = await asyncio.to_thread(hub.call, "POST", path, user_id, work.team_id,
                                        [scope], operation, json_body=body,
                                        idempotency_key=f"automation-{work.id}")
        work.business_result_json = encode(result)
        work.status = "applied"
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
    return payload(work)


async def complete_task(db, user_id, work_id, index, done):
    work = await get_work(db, user_id, work_id)
    if work.status != "applied" or work.kind != "crm":
        raise Conflict("请先保存 CRM 草稿再处理跟进待办")
    tasks = json.loads(work.accepted_json)["tasks"]
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
    await db.refresh(work)
    return payload(work)
