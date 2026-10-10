"""已入账凭证推送到 ERP（通用 Webhook 连接器）。

- 只有财务部门人员（和记账凭证同一个权限）能推送，只能推送已入账（POSTED）的凭证；
- 幂等：erp_export 上 voucher_id 唯一，请求头 Idempotency-Key 固定为 voucher-{id}。同一张凭证点几次、重试几次，
  ERP 收到的都是同一个幂等键；已经推送成功的直接返回原结果，不再发请求；两个人同时点，只有一个真的发出去；
- 请求体用 ERP_WEBHOOK_SECRET 做 HMAC-SHA256 签名（X-Signature），ERP 侧据此校验来源。

配置：ERP_WEBHOOK_URL、ERP_WEBHOOK_SECRET。没配置时推送按钮给出明确提示，不影响入账本身。
"""
import hashlib
import hmac
import json
import os
from datetime import timedelta
from typing import Any, Dict

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from models.init_db import ErpExport, Team
from service import finance_voucher_service as vouchers
from service.exceptions import Conflict, InvalidInput
from utils.logger_handler import get_logger
from utils.timeutil import utcnow

logger = get_logger("erp_connector")

SENDING_TIMEOUT = timedelta(minutes=5)


def configured() -> bool:
    return bool(os.getenv("ERP_WEBHOOK_URL") and os.getenv("ERP_WEBHOOK_SECRET"))


def payload(row: ErpExport) -> Dict[str, Any]:
    return {"voucher_id": row.voucher_id, "status": row.status, "attempts": row.attempts,
            "erp_document_id": row.erp_document_id, "last_error": row.last_error,
            "sent_at": row.sent_at.isoformat() + "Z" if row.sent_at else None}


def _send(body: Dict[str, Any], key: str) -> str:
    from service.http_resilience import request_with_retry
    import requests
    raw = json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")
    signature = hmac.new(os.environ["ERP_WEBHOOK_SECRET"].encode("utf-8"), raw, hashlib.sha256).hexdigest()

    def send(timeout: float) -> requests.Response:
        return requests.post(os.environ["ERP_WEBHOOK_URL"], data=raw, timeout=timeout, allow_redirects=False,
                             headers={"Content-Type": "application/json", "Idempotency-Key": key, "X-Signature": signature})
    response = request_with_retry("erp", send, "ERP_TIMEOUT_SECONDS", 15.0)
    if response.status_code >= 400:
        raise RuntimeError(f"ERP 返回 HTTP {response.status_code}")
    try:
        data = response.json()
    except ValueError:
        data = {}
    return str(data.get("document_id") or data.get("id") or "")[:120]


async def push_voucher(db, user_id: int, team_id: int, voucher_id: int) -> Dict[str, Any]:
    import asyncio
    if not configured():
        raise InvalidInput("还没有配置 ERP 连接（ERP_WEBHOOK_URL / ERP_WEBHOOK_SECRET），请联系管理员")
    voucher = await vouchers.get_voucher_async(db, user_id, team_id, voucher_id)     # 校验财务人员权限和可见范围
    if voucher.get("status") != "POSTED":
        raise Conflict("只有已入账的凭证才能推送到 ERP")
    org_id = (await db.execute(select(Team.organization_id).where(Team.id == team_id))).scalar()
    key = f"voucher-{int(voucher_id)}"
    row = (await db.execute(select(ErpExport).where(ErpExport.voucher_id == voucher_id))).scalar_one_or_none()
    if row is None:
        db.add(ErpExport(organization_id=org_id, voucher_id=voucher_id, idempotency_key=key, status="pending",
                         attempts=0, requested_by=user_id))
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
        row = (await db.execute(select(ErpExport).where(ErpExport.voucher_id == voucher_id))).scalar_one()
    if row.status == "sent":
        return {**payload(row), "duplicate": True}
    # 抢占发送权：只有把状态从 pending / failed（或卡住超过 5 分钟的 sending）改成 sending 的那个请求真的发
    stale = utcnow() - SENDING_TIMEOUT
    claimed = await db.execute(update(ErpExport).where(
        ErpExport.id == row.id,
        (ErpExport.status.in_(("pending", "failed"))) | ((ErpExport.status == "sending") & (ErpExport.sent_at < stale)))
        .values(status="sending", attempts=ErpExport.attempts + 1, sent_at=utcnow()))
    await db.commit()
    if claimed.rowcount == 0:
        raise Conflict("这张凭证正在推送，请稍后刷新查看结果")
    body = {"voucher_id": voucher_id, "voucher_no": voucher.get("voucherNo"), "date": voucher.get("voucherDate"),
            "summary": voucher.get("summary"), "period": voucher.get("period"), "total_amount": str(voucher.get("totalAmount")),
            "entries": voucher.get("entries"), "team": voucher.get("teamName"),
            "claim_id": voucher.get("expenseClaimId")}
    try:
        document_id = await asyncio.to_thread(_send, body, key)
    except Exception as exc:  # noqa: BLE001 —— 失败可重试（同一个幂等键）
        await db.execute(update(ErpExport).where(ErpExport.id == row.id).values(status="failed", last_error=str(exc)[:500]))
        await db.commit()
        logger.warning(f"凭证 {voucher_id} 推送 ERP 失败：{exc}")
        raise InvalidInput(f"推送失败，可以稍后重试：{str(exc)[:200]}") from None
    await db.execute(update(ErpExport).where(ErpExport.id == row.id).values(
        status="sent", erp_document_id=document_id or None, last_error=None, sent_at=utcnow()))
    await db.commit()
    from service import audit_service
    await audit_service.record_async(user_id, "finance.voucher_exported", resource_type="voucher", resource_id=voucher_id,
                                     detail={"erp_document_id": document_id})
    row = (await db.execute(select(ErpExport).where(ErpExport.id == row.id).execution_options(populate_existing=True))).scalar_one()
    return {**payload(row), "duplicate": False}


async def export_status(db, user_id: int, team_id: int, voucher_id: int) -> Dict[str, Any]:
    await vouchers.staff_scope_async(db, user_id, team_id)
    row = (await db.execute(select(ErpExport).where(ErpExport.voucher_id == voucher_id))).scalar_one_or_none()
    return {"configured": configured(), "export": payload(row) if row else None}
