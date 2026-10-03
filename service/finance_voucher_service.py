"""财务记账（凭证）：报销单批准后自动生成的凭证草稿，由财务部门人员核对、确认入账。

权限在 FastAPI 这层先判断、再签 scope 调 Java：
- 只有"财务类型部门（department_code = finance）的有效成员"或企业管理员能使用，其他部门（含 HR/销售）一律拒绝；
- 可见范围是该财务部门所在企业的全部部门（scopeTeamIds），写进已签名的请求路径，Java 再按它限定一次；
- 申请人不能确认自己报销单的凭证（制单与复核分离）在 Java 里强制，这里不重复。
"""
import asyncio
import uuid
from decimal import Decimal
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

from sqlalchemy import text

from models.enterprise_dao import is_team_member_of_team_async
from service import enterprise_access
from service import enterprise_hub_client as hub
from service.exceptions import AppError, Conflict, InvalidInput, NotFound, PermissionDenied, UpstreamError

READ = ["finance.voucher.read"]
WRITE = ["finance.voucher.read", "finance.voucher.write"]
RISK_LABELS = {"NONE": "无风险", "INFO": "有提示", "WARN": "需核对", "BLOCK": "不能入账"}


from service.hub_gateway import call_hub as _gateway_call, scoped_path as _scoped_path, translate_hub_error as _translate_hub_error  # noqa: E402


async def staff_scope_async(db, user_id: int, team_id: int) -> List[int]:
    """校验调用者是财务部门人员，返回可见的部门 id 列表（同一企业的全部部门）。"""
    from service.department_access import department_staff_scope_async
    return (await department_staff_scope_async(db, user_id, team_id, "finance", "记账凭证"))["scope"]


def staff_scope(user_id: int, team_id: int) -> List[int]:
    """`staff_scope_async` 的同步版（Agent 工具用），规则一致；不满足时抛 PermissionDenied。"""
    from service.department_access import department_staff_scope
    return department_staff_scope(user_id, team_id, "finance", "记账凭证")["scope"]


def _path(base: str, scope: List[int], **params: Any) -> str:
    return _scoped_path(base, scope, **params)


async def _call(method: str, path: str, user_id: int, team_id: int, scopes: List[str], operation: str,
                json_body: Optional[Dict[str, Any]] = None, write: bool = False) -> Any:
    return await _gateway_call(method, path, user_id, team_id, scopes, operation, json_body, write)


async def _names(db, table: str, ids: List[int], column: str = "name") -> Dict[int, str]:
    from service.name_lookup import names
    return await names(db, table, ids, column)


async def _enrich(db, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    users = await _names(db, "`user`", [r.get("applicantUserId") for r in rows])
    teams = await _names(db, "teams", [r.get("teamId") for r in rows])
    for row in rows:
        row["applicantName"] = users.get(row.get("applicantUserId"))
        row["teamName"] = teams.get(row.get("teamId"))
        row["riskLabel"] = RISK_LABELS.get(row.get("riskLevel"), row.get("riskLevel"))
    return rows


async def list_vouchers_async(db, user_id: int, team_id: int, status: Optional[str] = None,
                              period: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
    scope = await staff_scope_async(db, user_id, team_id)
    rows = await _call("GET", _path("/finance/vouchers", scope, status=status, period=period, limit=limit),
                       user_id, team_id, READ, "list_vouchers")
    return await _enrich(db, rows)


async def get_voucher_async(db, user_id: int, team_id: int, voucher_id: int) -> Dict[str, Any]:
    scope = await staff_scope_async(db, user_id, team_id)
    voucher = await _call("GET", _path(f"/finance/vouchers/{int(voucher_id)}", scope), user_id, team_id, READ,
                          "get_voucher")
    return (await _enrich(db, [voucher]))[0]


async def voucher_by_claim_async(db, user_id: int, team_id: int, claim_id: int) -> Dict[str, Any]:
    scope = await staff_scope_async(db, user_id, team_id)
    voucher = await _call("GET", _path(f"/finance/vouchers/by-claim/{int(claim_id)}", scope), user_id, team_id,
                          READ, "get_voucher_by_claim")
    return (await _enrich(db, [voucher]))[0]


async def list_unbooked_async(db, user_id: int, team_id: int) -> List[Dict[str, Any]]:
    scope = await staff_scope_async(db, user_id, team_id)
    rows = await _call("GET", _path("/finance/vouchers/unbooked", scope), user_id, team_id, READ, "list_unbooked_claims")
    return await _enrich(db, rows)


async def list_subjects_async(db, user_id: int, team_id: int) -> List[Dict[str, Any]]:
    await staff_scope_async(db, user_id, team_id)
    return await _call("GET", "/finance/vouchers/subjects", user_id, team_id, READ, "list_account_subjects")


async def generate_from_claim_async(db, user_id: int, team_id: int, claim_id: int) -> Dict[str, Any]:
    scope = await staff_scope_async(db, user_id, team_id)
    unbooked = await _call("GET", _path("/finance/vouchers/unbooked", scope), user_id, team_id, READ,
                           "list_unbooked_claims")
    claim = next((c for c in unbooked if int(c["id"]) == int(claim_id)), None)
    department_code = None
    if claim is not None:
        row = (await db.execute(text("SELECT department_code FROM teams WHERE id = :t"), {"t": claim["teamId"]})).first()
        department_code = row[0] if row else None
    voucher = await _call("POST", _path(f"/finance/vouchers/from-claim/{int(claim_id)}", scope), user_id, team_id,
                          WRITE, "generate_voucher", json_body={"departmentCode": department_code}, write=True)
    return (await _enrich(db, [voucher]))[0]


async def _write(db, user_id: int, team_id: int, path: str, operation: str,
                 json_body: Optional[Dict[str, Any]] = None, scope: Optional[List[int]] = None) -> Dict[str, Any]:
    scope = scope or await staff_scope_async(db, user_id, team_id)
    voucher = await _call("POST", _path(path, scope), user_id, team_id, WRITE, operation, json_body=json_body, write=True)
    return (await _enrich(db, [voucher]))[0]


async def update_entry_subject_async(db, user_id: int, team_id: int, voucher_id: int, entry_id: int,
                                     subject_code: str, reason: str) -> Dict[str, Any]:
    return await _write(db, user_id, team_id, f"/finance/vouchers/{int(voucher_id)}/entries/{int(entry_id)}/subject",
                        "update_voucher_entry", {"subjectCode": subject_code, "reason": reason})


async def update_voucher_date_async(db, user_id: int, team_id: int, voucher_id: int, voucher_date: str) -> Dict[str, Any]:
    return await _write(db, user_id, team_id, f"/finance/vouchers/{int(voucher_id)}/date", "update_voucher_date",
                        {"voucherDate": voucher_date})


async def recheck_async(db, user_id: int, team_id: int, voucher_id: int) -> Dict[str, Any]:
    scope = await staff_scope_async(db, user_id, team_id)
    voucher = await _call("POST", _path(f"/finance/vouchers/{int(voucher_id)}/recheck", scope), user_id, team_id, WRITE,
                          "recheck_voucher")
    return (await _enrich(db, [voucher]))[0]


async def regenerate_async(db, user_id: int, team_id: int, voucher_id: int) -> Dict[str, Any]:
    return await _write(db, user_id, team_id, f"/finance/vouchers/{int(voucher_id)}/regenerate", "regenerate_voucher")


async def confirm_async(db, user_id: int, team_id: int, voucher_id: int, note: Optional[str],
                        acknowledge_warnings: bool) -> Dict[str, Any]:
    return await _write(db, user_id, team_id, f"/finance/vouchers/{int(voucher_id)}/confirm", "confirm_voucher",
                        {"note": note, "acknowledgeWarnings": acknowledge_warnings})


async def void_async(db, user_id: int, team_id: int, voucher_id: int, reason: str) -> Dict[str, Any]:
    return await _write(db, user_id, team_id, f"/finance/vouchers/{int(voucher_id)}/void", "void_voucher",
                        {"reason": reason})


def _money(value: Any) -> str:
    return f"¥{Decimal(str(value or 0)):,.2f}"


def narrative_for(summary: Dict[str, Any]) -> str:
    """月度小结：只复述数据，不推断；每句都能在汇总数字里找到出处。"""
    by_status = summary.get("byStatus", {})
    posted = by_status.get("POSTED", {})
    draft = by_status.get("DRAFT", {})
    void = by_status.get("VOID", {})
    parts = [f"{summary.get('period')} 已入账凭证 {posted.get('count', 0)} 张，合计 {_money(posted.get('amount'))}。"]
    expense = [r for r in summary.get("bySubject", []) if r.get("direction") == "D"]
    if expense:
        top = max(expense, key=lambda r: Decimal(str(r["amount"])))
        parts.append(f"借方最大科目是「{top['name']}」{_money(top['amount'])}。")
    efficiency = summary.get("efficiency") or {}
    if efficiency.get("postedCount"):
        parts.append(f"其中 {efficiency['adoptedRate']:g}% 的凭证科目建议被原样采纳"
                     + (f"，从自动生成到入账平均 {efficiency['avgHoursToPost']:g} 小时。"
                        if efficiency.get("avgHoursToPost") is not None else "。"))
    if not summary.get("balanced", True):
        parts.append("注意：已入账凭证借贷合计不平衡，请立即核查。")
    if draft.get("count"):
        risk = summary.get("draftRisk", {})
        parts.append(f"另有 {draft['count']} 张待确认凭证（合计 {_money(draft.get('amount'))}），其中需核对 "
                     f"{risk.get('WARN', 0)} 张、有阻断问题 {risk.get('BLOCK', 0)} 张。")
    if summary.get("unbookedClaims"):
        parts.append(f"有 {summary['unbookedClaims']} 张已批准报销单还没有凭证（{_money(summary.get('unbookedAmount'))}），"
                     "请在待生成列表里补生成。")
    if void.get("count"):
        parts.append(f"本期作废 {void['count']} 张。")
    return "".join(parts)


async def monthly_summary_async(db, user_id: int, team_id: int, period: Optional[str]) -> Dict[str, Any]:
    scope = await staff_scope_async(db, user_id, team_id)
    summary = await _call("GET", _path("/finance/vouchers/summary", scope, period=period), user_id, team_id, READ,
                          "voucher_monthly_summary")
    names = await _names(db, "teams", [row["teamId"] for row in summary.get("byTeam", [])])
    for row in summary.get("byTeam", []):
        row["teamName"] = names.get(int(row["teamId"]))
    summary["narrative"] = narrative_for(summary)
    return summary
