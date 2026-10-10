"""发票识别：上传图片 / PDF → 识别字段（每个字段带置信度）→ 校验（号码格式、税号、金额合计、重复文件、重复发票号、
供应商、抬头）→ 员工核对确认 → 带进报销单。

- 低置信度的字段必须员工逐项确认（confirm 时必须把这些字段列进 confirmed_fields），没确认的发票不能用于报销；
- 同一个文件（SHA-256）、同一个发票号重复出现都会拦下：重复发票号同时查本平台识别过的和业务系统里已报销的；
- 文件只用来识别，不落盘保存；OCR 走现有的文档解析（受数据外发策略约束）。
"""
import asyncio
import hashlib
import json
from datetime import timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select

from models.init_db import InvoiceExtraction, Organization, Team
from service import document_intake, invoice_parser
from service.department_access import require_team_member_async
from service.exceptions import Conflict, InvalidInput, NotFound
from utils.logger_handler import get_logger
from utils.timeutil import utcnow

logger = get_logger("invoice_service")

SUPPORTED = ("pdf", "png", "jpg", "jpeg")
EDITABLE = tuple(f for f in invoice_parser.FIELDS)


def _today():
    return (utcnow() + timedelta(hours=8)).date()


def payload(row: InvoiceExtraction) -> Dict[str, Any]:
    fields = {f: getattr(row, f) for f in invoice_parser.FIELDS}
    confidence = json.loads(row.field_confidence_json or "{}")
    return {
        "id": row.id, "file_name": row.file_name, "status": row.status, "method": row.method, "fields": fields,
        "confidence": confidence, "overall_confidence": row.confidence,
        "needs_review": invoice_parser.needs_review(confidence, fields) if row.status == "needs_review" else [],
        "checks": json.loads(row.checks_json or "[]"), "corrected_fields": json.loads(row.corrected_fields_json or "[]"),
        "labels": invoice_parser.FIELD_LABELS, "claim_id": row.claim_id, "raw_reference": row.raw_reference,
        "created_at": row.created_at.isoformat() + "Z",
    }


async def _company_name(db, team_id: int) -> Optional[str]:
    return (await db.execute(select(Organization.name).join(Team, Team.organization_id == Organization.id)
                             .where(Team.id == team_id))).scalar()


async def _known_seller(db, tax_id: Optional[str], exclude_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """供应商匹配：这个税号以前（已确认 / 已报销的发票里）对应的销售方名称。"""
    if not tax_id:
        return None
    query = select(InvoiceExtraction.seller_name, func.count()).where(
        InvoiceExtraction.seller_tax_id == tax_id, InvoiceExtraction.status.in_(("confirmed", "used")))
    if exclude_id:
        query = query.where(InvoiceExtraction.id != exclude_id)
    row = (await db.execute(query.group_by(InvoiceExtraction.seller_name).order_by(func.count().desc()).limit(1))).first()
    return {"name": row[0], "count": int(row[1])} if row else None


async def _duplicates(db, user_id: int, row: InvoiceExtraction) -> List[Dict[str, str]]:
    checks = []
    same_file = (await db.execute(select(InvoiceExtraction.id, InvoiceExtraction.status).where(
        InvoiceExtraction.file_sha256 == row.file_sha256, InvoiceExtraction.id != row.id,
        InvoiceExtraction.status != "discarded").limit(1))).first()
    if same_file:
        checks.append({"level": "error", "code": "DUPLICATE_FILE", "text": f"同一个文件已经上传过（识别记录 #{same_file[0]}）"})
    if row.invoice_number:
        same_no = (await db.execute(select(InvoiceExtraction.id).where(
            InvoiceExtraction.invoice_number == row.invoice_number, InvoiceExtraction.id != row.id,
            InvoiceExtraction.status.in_(("confirmed", "used")),
            func.coalesce(InvoiceExtraction.invoice_code, "") == (row.invoice_code or "")).limit(1))).scalar()
        if same_no:
            checks.append({"level": "error", "code": "DUPLICATE_INVOICE", "text": f"发票号 {row.invoice_number} 已经确认 / 报销过（识别记录 #{same_no}）"})
        else:
            used = await _used_in_claims(user_id, row.invoice_number)
            if used:
                checks.append({"level": "error", "code": "DUPLICATE_INVOICE",
                               "text": f"发票号 {row.invoice_number} 已在报销单 #{used} 中使用"})
    return checks


async def _used_in_claims(user_id: int, number: str) -> Optional[int]:
    """业务系统里已报销的发票号（Java 的 /finance/invoices/usage）。业务系统不可用时不拦，但提交报销时 Java 还会再查一次。"""
    from urllib.parse import quote
    from service import enterprise_hub_client as hub
    try:
        rows = await asyncio.to_thread(hub.call, "GET", f"/finance/invoices/usage?numbers={quote(number)}", user_id, None,
                                       ["finance.read"], "invoice_usage")
    except Exception:  # noqa: BLE001
        logger.warning("查询发票使用情况失败", exc_info=True)
        return None
    return int(rows[0]["claimId"]) if rows else None


async def _evaluate(db, user_id: int, row: InvoiceExtraction, confidence: Dict[str, float]) -> None:
    fields = {f: getattr(row, f) for f in invoice_parser.FIELDS}
    confidence, checks = invoice_parser.validate(
        fields, confidence, today=_today(), company_name=await _company_name(db, row.team_id),
        known_seller=await _known_seller(db, row.seller_tax_id, row.id))
    checks = await _duplicates(db, user_id, row) + checks
    if any(c["code"] in ("DUPLICATE_FILE", "DUPLICATE_INVOICE") for c in checks):
        confidence["invoice_number"] = min(confidence.get("invoice_number", 0), 0.4)
    row.field_confidence_json = json.dumps(confidence)
    row.checks_json = json.dumps(checks, ensure_ascii=False)
    present = [confidence.get(f, 0.0) for f in invoice_parser.FIELDS if fields.get(f)]
    row.confidence = round(min(present), 2) if present else 0.0


async def extract(db, user_id: int, team_id: int, filename: str, content: bytes) -> Dict[str, Any]:
    await require_team_member_async(db, user_id, team_id, "finance", message="不属于该部门，不能识别发票")
    kind = document_intake.file_type_of(filename)
    if kind not in SUPPORTED:
        raise InvalidInput("发票只支持 PDF 或图片（PNG / JPG）")
    if not content or len(content) > document_intake.MAX_BYTES:
        raise InvalidInput("文件为空或超过 10MB")
    document_intake._check_signature(kind, content)
    digest = hashlib.sha256(content).hexdigest()
    parsed_text = await document_intake.extract_text(db, user_id, team_id, filename, content, "internal")
    text, from_ocr = parsed_text["text"], bool(parsed_text.get("ocr")) or kind in document_intake.IMAGE_TYPES
    result = invoice_parser.parse(text, from_ocr=from_ocr)
    row = InvoiceExtraction(user_id=user_id, team_id=team_id, file_name=filename[:255], file_sha256=digest,
                            raw_reference=result["reference"], method="ocr" if from_ocr else "text",
                            status="needs_review", **result["fields"])
    db.add(row)
    await db.flush()
    await _evaluate(db, user_id, row, result["confidence"])
    if not invoice_parser.needs_review(json.loads(row.field_confidence_json), {f: getattr(row, f) for f in invoice_parser.FIELDS}) \
            and not any(c["level"] == "error" for c in json.loads(row.checks_json)):
        row.status, row.confirmed_at = "confirmed", utcnow()   # 文字版发票全部字段高置信度、校验全过：不用再逐项确认
    await db.commit()
    await db.refresh(row)
    return payload(row)


async def _own(db, user_id: int, extraction_id: int) -> InvoiceExtraction:
    row = (await db.execute(select(InvoiceExtraction).where(InvoiceExtraction.id == extraction_id,
                                                            InvoiceExtraction.user_id == user_id))).scalar_one_or_none()
    if row is None:
        raise NotFound("发票识别记录不存在")
    return row


async def get(db, user_id: int, extraction_id: int) -> Dict[str, Any]:
    return payload(await _own(db, user_id, extraction_id))


async def list_mine(db, user_id: int, status: Optional[str] = None) -> List[Dict[str, Any]]:
    query = select(InvoiceExtraction).where(InvoiceExtraction.user_id == user_id)
    if status:
        query = query.where(InvoiceExtraction.status == status)
    rows = (await db.execute(query.order_by(InvoiceExtraction.id.desc()).limit(100))).scalars().all()
    return [payload(r) for r in rows]


async def confirm(db, user_id: int, extraction_id: int, values: Dict[str, Optional[str]], confirmed_fields: List[str]) -> Dict[str, Any]:
    """员工核对后确认。低置信度字段必须出现在 confirmed_fields 里（表示“我看过、这个值是对的”）；
    改过的字段置信度记为 1.0（人工填写）。校验仍有错误（金额对不上、重复发票）时不能确认。"""
    row = await _own(db, user_id, extraction_id)
    if row.status not in ("needs_review", "confirmed"):
        raise Conflict("这张发票已经用过或作废，不能再修改")
    confidence = json.loads(row.field_confidence_json or "{}")
    corrected = set(json.loads(row.corrected_fields_json or "[]"))
    for field, value in (values or {}).items():
        if field not in EDITABLE:
            raise InvalidInput(f"不认识的字段：{field}")
        value = (value or "").strip() or None
        if field.endswith("tax_id") and value:
            value = value.upper()
        if value != getattr(row, field):
            setattr(row, field, value)
            corrected.add(field)
            confidence[field] = 1.0
    for field in confirmed_fields or []:
        if field in EDITABLE:
            confidence[field] = max(confidence.get(field, 0.0), 1.0)
    await _evaluate(db, user_id, row, confidence)
    fields = {f: getattr(row, f) for f in invoice_parser.FIELDS}
    confidence = json.loads(row.field_confidence_json)
    missing = invoice_parser.needs_review(confidence, fields)
    errors = [c for c in json.loads(row.checks_json) if c["level"] == "error"]
    row.corrected_fields_json = json.dumps(sorted(corrected))
    if missing or errors:
        await db.commit()
        reason = errors[0]["text"] if errors else "还有字段没确认：" + "、".join(invoice_parser.FIELD_LABELS[f] for f in missing)
        raise InvalidInput(reason)
    row.status, row.confirmed_at = "confirmed", utcnow()
    await db.commit()
    await db.refresh(row)
    return payload(row)


async def discard(db, user_id: int, extraction_id: int) -> Dict[str, Any]:
    row = await _own(db, user_id, extraction_id)
    if row.status == "used":
        raise Conflict("已经用于报销的发票不能作废")
    row.status = "discarded"
    await db.commit()
    return payload(row)


async def take_for_claim(db, user_id: int, extraction_ids: List[int]) -> Dict[int, InvoiceExtraction]:
    """报销单引用的发票：必须是本人的、已确认、还没用过的。"""
    rows = {}
    for extraction_id in extraction_ids:
        row = await _own(db, user_id, extraction_id)
        if row.status == "needs_review":
            raise InvalidInput(f"发票「{row.file_name}」还有字段没确认，请先核对确认")
        if row.status != "confirmed":
            raise Conflict(f"发票「{row.file_name}」已经用过或作废")
        rows[extraction_id] = row
    return rows


async def mark_used(db, rows: Dict[int, InvoiceExtraction], claim_id: Optional[int]) -> None:
    for row in rows.values():
        row.status, row.claim_id = "used", claim_id
    await db.commit()
