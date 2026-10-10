"""客户活动：入库（去重 + 自动关联客户）、待归属列表、销售手动指定客户（记住对应关系）、手工记录。"""
import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from models.init_db import CrmCustomerAlias, CustomerActivity
from service import enterprise_hub_client as hub
from service.crm import matching
from service.crm.sources import ActivityDraft, phones_in
from service.department_access import require_team_member_async
from service.exceptions import InvalidInput, NotFound
from utils.timeutil import utcnow

ACTIVITY_TYPES = ("email", "meeting", "chat", "call", "followup", "quote", "todo")
MANUAL_TYPES = ("call", "followup", "quote", "meeting", "chat", "todo")
TYPE_LABELS = {"email": "邮件", "meeting": "会议", "chat": "群聊", "call": "电话纪要", "followup": "人工跟进",
               "quote": "报价", "todo": "待办", "stage_change": "商机阶段变化"}


def _hub_error(exc: hub.EnterpriseHubError):
    from service.crm_workspace_service import _translate_hub_error
    return _translate_hub_error(exc)


async def hub_get(user_id: int, team_id: int, path: str, operation: str):
    try:
        return await asyncio.to_thread(hub.call, "GET", path, user_id, team_id, ["crm.read"], operation)
    except hub.EnterpriseHubError as exc:
        raise _hub_error(exc)


async def load_directory(db, user_id: int, team_id: int) -> matching.Directory:
    """本部门能看到的客户、联系人（来自业务系统，按调用者身份取，跨部门的看不到）+ 本部门记下的别名。"""
    customers = await hub_get(user_id, team_id, "/crm/customers", "list_team_customers")
    contacts = await hub_get(user_id, team_id, "/crm/contacts", "list_team_contacts")
    aliases = (await db.execute(select(CrmCustomerAlias.alias_type, CrmCustomerAlias.alias_value, CrmCustomerAlias.customer_id)
                                .where(CrmCustomerAlias.team_id == team_id))).all()
    return matching.Directory([{"id": c["id"], "name": c["name"]} for c in customers or []], contacts or [],
                              [(t, v, int(cid)) for t, v, cid in aliases], await internal_domains(db, team_id))


async def internal_domains(db, team_id: int) -> set:
    """本公司的邮箱域名：CRM_INTERNAL_EMAIL_DOMAINS 配置的，加上本部门员工连接的邮箱的域名。
    这些地址是同事，不能拿来关联客户，也不能被“记住”成客户的邮箱。"""
    import os
    from models.init_db import CrmMailAccount
    domains = {d.strip().lower().lstrip("@") for d in os.getenv("CRM_INTERNAL_EMAIL_DOMAINS", "").split(",") if d.strip()}
    for username in (await db.execute(select(CrmMailAccount.username).where(CrmMailAccount.team_id == team_id))).scalars():
        domain = matching.domain_of(matching.normalize_email(username))
        if domain and domain not in matching.PUBLIC_DOMAINS:
            domains.add(domain)
    return domains


def payload(row: CustomerActivity, directory: Optional[matching.Directory] = None) -> Dict[str, Any]:
    return {
        "id": row.id, "team_id": row.team_id, "customer_id": row.customer_id,
        "customer_name": directory.name_of(row.customer_id) if directory and row.customer_id else None,
        "activity_type": row.activity_type, "type_label": TYPE_LABELS.get(row.activity_type, row.activity_type),
        "source_provider": row.source_provider, "occurred_at": row.occurred_at.isoformat() + "Z",
        "participants": json.loads(row.participants_json or "[]"), "title": row.title,
        "content": row.content, "summary": row.summary, "created_by": row.created_by,
        "match_status": row.match_status, "match_confidence": row.match_confidence, "match_method": row.match_method,
        "match_candidates": json.loads(row.match_candidates_json or "[]"),
    }


async def _existing(db, source_provider: str, source_id: str) -> Optional[CustomerActivity]:
    return (await db.execute(select(CustomerActivity).where(
        CustomerActivity.source_provider == source_provider,
        CustomerActivity.external_source_id == source_id))).scalar_one_or_none()


async def ingest(db, user_id: int, team_id: int, draft: ActivityDraft, source_provider: str, *,
                 explicit_customer_id: Optional[int] = None, trace_id: Optional[str] = None,
                 directory: Optional[matching.Directory] = None) -> Dict[str, Any]:
    """入库一条活动。同一来源（同一封邮件、同一个会议、同一条消息）只会有一条：重复的直接返回已有的那条。"""
    await require_team_member_async(db, user_id, team_id, "crm", message="不属于该部门，不能记录客户活动")
    found = await _existing(db, source_provider, draft.external_source_id)
    if found is not None:
        return {"duplicate": True, "activity": payload(found)}
    directory = directory or await load_directory(db, user_id, team_id)
    result = matching.match(directory, explicit_customer_id=explicit_customer_id, emails=draft.emails,
                            phones=phones_in(draft.content) + [p.get("phone", "") for p in draft.participants],
                            text=f"{draft.title}\n{draft.content}")
    if result.method == "explicit_not_found":
        raise NotFound("客户不存在，或不属于你的部门")
    linked = result.customer_id if result.status in ("explicit", "auto") else None
    row = CustomerActivity(
        team_id=team_id, customer_id=linked, activity_type=draft.activity_type, source_provider=source_provider,
        external_source_id=draft.external_source_id, occurred_at=draft.occurred_at,
        participants_json=json.dumps(draft.participants, ensure_ascii=False), title=draft.title, content=draft.content,
        created_by=user_id, trace_id=trace_id, match_status=result.status, match_confidence=result.confidence,
        match_method=result.method, match_candidates_json=json.dumps(result.candidates, ensure_ascii=False) or None)
    db.add(row)
    try:
        await db.commit()
    except IntegrityError:                     # 并发时另一个请求先存进去了
        await db.rollback()
        found = await _existing(db, source_provider, draft.external_source_id)
        return {"duplicate": True, "activity": payload(found) if found else None}
    await db.refresh(row)
    return {"duplicate": False, "activity": payload(row, directory)}


async def add_manual(db, user_id: int, team_id: int, customer_id: int, activity_type: str, title: str, content: str,
                     occurred_at: Optional[str] = None) -> Dict[str, Any]:
    if activity_type not in MANUAL_TYPES:
        raise InvalidInput("活动类型只能是电话纪要、人工跟进、报价、会议、群聊或待办")
    if not (title or "").strip() and not (content or "").strip():
        raise InvalidInput("请填写标题或内容")
    when = utcnow()
    if occurred_at:
        try:
            parsed = datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
        except ValueError:
            raise InvalidInput("时间格式不正确") from None
        # 不带时区的按北京时间理解，统一存 UTC
        when = (parsed - timedelta(hours=8)) if parsed.tzinfo is None else parsed.astimezone(timezone.utc).replace(tzinfo=None)
    draft = ActivityDraft(activity_type, f"manual:{uuid.uuid4().hex}", when, (title or "").strip()[:300] or TYPE_LABELS[activity_type],
                          (content or "").strip()[:20000])
    return await ingest(db, user_id, team_id, draft, "manual", explicit_customer_id=customer_id)


async def list_activities(db, user_id: int, team_id: int, status: str = "pending", limit: int = 100) -> Dict[str, Any]:
    await require_team_member_async(db, user_id, team_id, "crm")
    query = select(CustomerActivity).where(CustomerActivity.team_id == team_id)
    if status == "pending":
        query = query.where(CustomerActivity.customer_id.is_(None), CustomerActivity.match_status != "ignored")
    elif status != "all":
        raise InvalidInput("status 只能是 pending / all")
    rows = (await db.execute(query.order_by(CustomerActivity.occurred_at.desc()).limit(limit))).scalars().all()
    pending = (await db.execute(select(func.count()).where(CustomerActivity.team_id == team_id,
                                                            CustomerActivity.customer_id.is_(None),
                                                            CustomerActivity.match_status != "ignored"))).scalar()
    return {"items": [payload(r) for r in rows], "pending_count": int(pending or 0)}


async def _get_in_team(db, team_id: int, activity_id: int) -> CustomerActivity:
    row = (await db.execute(select(CustomerActivity).where(CustomerActivity.id == activity_id,
                                                            CustomerActivity.team_id == team_id))).scalar_one_or_none()
    if row is None:
        raise NotFound("活动不存在")
    return row


async def assign(db, user_id: int, team_id: int, activity_id: int, customer_id: int, remember: bool = True) -> Dict[str, Any]:
    """销售指定这条活动属于哪个客户。remember=True 时记下对应关系（外部参与人的邮箱、发件人的公司域名），
    之后同样来源的邮件 / 会议直接对上；并用新的对应关系重新匹配本部门其他待归属的活动。"""
    await require_team_member_async(db, user_id, team_id, "crm", message="不属于该部门，不能归属客户活动")
    row = await _get_in_team(db, team_id, activity_id)
    directory = await load_directory(db, user_id, team_id)
    if not directory.has(customer_id):
        raise NotFound("客户不存在，或不属于你的部门")
    await db.execute(update(CustomerActivity).where(CustomerActivity.id == row.id).values(
        customer_id=customer_id, match_status="manual", match_confidence=1.0, match_method="manual"))
    learned = []
    if remember:
        contact_owner = {matching.normalize_email(c.get("email") or ""): int(c["customerId"]) for c in directory.contacts}
        for person in json.loads(row.participants_json or "[]"):
            address = matching.normalize_email(person.get("email") or "")
            if not address or directory.is_internal(address) or contact_owner.get(address, customer_id) != customer_id:
                continue                       # 同事的地址、已经是别的客户的联系人：不记
            learned.append(("email", address))
            domain = matching.domain_of(address)
            if person.get("role") in ("from", "organizer") and domain and domain not in matching.PUBLIC_DOMAINS:
                learned.append(("domain", domain))
        for alias_type, value in learned:
            exists = (await db.execute(select(CrmCustomerAlias).where(
                CrmCustomerAlias.team_id == team_id, CrmCustomerAlias.alias_type == alias_type,
                CrmCustomerAlias.alias_value == value))).scalar_one_or_none()
            if exists is None:
                db.add(CrmCustomerAlias(team_id=team_id, alias_type=alias_type, alias_value=value[:255],
                                        customer_id=customer_id, created_by=user_id))
            else:
                exists.customer_id = customer_id
    await db.commit()
    rematched = await rematch_pending(db, user_id, team_id) if learned else 0
    from service import audit_service
    await audit_service.record_async(user_id, "crm.activity_assigned", resource_type="customer_activity", resource_id=row.id,
                                     detail={"customer_id": customer_id, "learned": [v for _, v in learned]})
    await db.refresh(row)
    return {"activity": payload(row, directory), "learned": [{"type": t, "value": v} for t, v in learned],
            "rematched": rematched}


async def rematch_pending(db, user_id: int, team_id: int) -> int:
    """用最新的联系人和别名重新匹配本部门待归属的活动；只有达到自动关联标准的才会归属。"""
    directory = await load_directory(db, user_id, team_id)
    rows = (await db.execute(select(CustomerActivity).where(
        CustomerActivity.team_id == team_id, CustomerActivity.customer_id.is_(None),
        CustomerActivity.match_status != "ignored").limit(500))).scalars().all()
    changed = 0
    for row in rows:
        people = json.loads(row.participants_json or "[]")
        result = matching.match(directory, emails=[p.get("email", "") for p in people], phones=phones_in(row.content or ""),
                                text=f"{row.title or ''}\n{row.content or ''}")
        if result.status == "auto":
            row.customer_id, row.match_status = result.customer_id, "auto"
            row.match_confidence, row.match_method = result.confidence, result.method
            row.match_candidates_json = None
            changed += 1
    await db.commit()
    return changed


async def ignore(db, user_id: int, team_id: int, activity_id: int) -> Dict[str, Any]:
    """不属于任何客户（比如内部邮件）：从待归属里移走，不删除原始记录。"""
    await require_team_member_async(db, user_id, team_id, "crm")
    row = await _get_in_team(db, team_id, activity_id)
    row.match_status = "ignored"
    row.match_candidates_json = None
    await db.commit()
    return payload(row)


async def customer_activities(db, team_id: int, customer_id: int, after_id: int = 0, limit: int = 200) -> List[CustomerActivity]:
    """某个客户在本部门的活动（只看本部门的：别的部门对同一客户的记录不会混进来）。"""
    return list((await db.execute(select(CustomerActivity).where(
        CustomerActivity.team_id == team_id, CustomerActivity.customer_id == customer_id, CustomerActivity.id > after_id)
        .order_by(CustomerActivity.id).limit(limit))).scalars().all())
