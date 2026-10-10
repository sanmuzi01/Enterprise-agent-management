"""费用标准（expense_policy_rule）：结构化规则，不写进提示词。

一条报销明细适用哪条规则：同一类别、在生效期内、城市等级 / 职级匹配（规则里为空 = 不限），取条件最具体的一条
（城市、职级都指定的优先）。检查结果：
  - receipt_required 但没有发票号 → 不能保存（缺票）；
  - 金额超过 amount_limit → 必须填写“超标准说明”，说明和需要的审批级别写进明细说明，审批人一眼能看到。
员工职级：部门负责人（部门角色 admin）按 manager，其他按 staff。
"""
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

from sqlalchemy import select

from models.init_db import ExpensePolicyRule
from service.exceptions import InvalidInput, NotFound
from utils.timeutil import utcnow

CATEGORIES = {"TRAVEL": "差旅", "MEAL": "餐费", "OFFICE_SUPPLY": "办公用品", "TRANSPORT": "交通", "OTHER": "其他"}
CITY_LEVELS = {"tier1": "一线城市", "tier2": "二线城市", "other": "其他城市"}
EMPLOYEE_LEVELS = {"staff": "员工", "manager": "部门负责人"}
APPROVAL_LEVELS = {"team_admin": "部门负责人", "finance": "财务", "org_admin": "企业管理员"}


def _dec(value: Any) -> Optional[Decimal]:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        raise InvalidInput("金额格式不对") from None


def payload(row: ExpensePolicyRule) -> Dict[str, Any]:
    return {"id": row.id, "category": row.category, "category_label": CATEGORIES.get(row.category, row.category),
            "city_level": row.city_level, "employee_level": row.employee_level, "amount_limit": row.amount_limit,
            "receipt_required": bool(row.receipt_required), "approval_level": row.approval_level,
            "approval_label": APPROVAL_LEVELS.get(row.approval_level), "effective_from": row.effective_from,
            "effective_to": row.effective_to, "note": row.note}


def _validate(data: Dict[str, Any]) -> Dict[str, Any]:
    if data.get("category") not in CATEGORIES:
        raise InvalidInput("费用类别只能是：" + "、".join(CATEGORIES.values()))
    if data.get("city_level") not in (None, "", *CITY_LEVELS):
        raise InvalidInput("城市等级只能是一线、二线或其他")
    if data.get("employee_level") not in (None, "", *EMPLOYEE_LEVELS):
        raise InvalidInput("职级只能是员工或部门负责人")
    if data.get("approval_level", "team_admin") not in APPROVAL_LEVELS:
        raise InvalidInput("审批级别只能是部门负责人、财务或企业管理员")
    limit = _dec(data.get("amount_limit"))
    if limit is not None and limit <= 0:
        raise InvalidInput("上限必须大于 0")
    for key in ("effective_from", "effective_to"):
        if data.get(key):
            try:
                date.fromisoformat(data[key])
            except ValueError:
                raise InvalidInput("生效日期格式应为 YYYY-MM-DD") from None
    if data.get("effective_from") and data.get("effective_to") and data["effective_from"] > data["effective_to"]:
        raise InvalidInput("生效开始日期不能晚于结束日期")
    return {"category": data["category"], "city_level": data.get("city_level") or None,
            "employee_level": data.get("employee_level") or None, "amount_limit": str(limit) if limit is not None else None,
            "receipt_required": 1 if data.get("receipt_required", True) else 0,
            "approval_level": data.get("approval_level") or "team_admin",
            "effective_from": data.get("effective_from") or None, "effective_to": data.get("effective_to") or None,
            "note": (data.get("note") or "")[:300] or None}


async def _org(db) -> int:
    from service.organization_admin_service import _get_default_organization
    return (await _get_default_organization(db)).id


async def list_rules(db) -> List[Dict[str, Any]]:
    rows = (await db.execute(select(ExpensePolicyRule).where(ExpensePolicyRule.organization_id == await _org(db))
                             .order_by(ExpensePolicyRule.category, ExpensePolicyRule.id))).scalars().all()
    return [payload(r) for r in rows]


async def save_rule(db, operator_id: int, data: Dict[str, Any], rule_id: Optional[int] = None) -> Dict[str, Any]:
    from service import audit_service
    values = _validate(data)
    org = await _org(db)
    if rule_id is None:
        row = ExpensePolicyRule(organization_id=org, created_by=operator_id, **values)
        db.add(row)
    else:
        row = (await db.execute(select(ExpensePolicyRule).where(ExpensePolicyRule.id == rule_id,
                                                                ExpensePolicyRule.organization_id == org))).scalar_one_or_none()
        if row is None:
            raise NotFound("规则不存在")
        for key, value in values.items():
            setattr(row, key, value)
    await db.commit()
    await db.refresh(row)
    await audit_service.record_async(operator_id, "finance.policy_saved", resource_type="expense_policy_rule",
                                     resource_id=row.id, detail=values)
    return payload(row)


async def delete_rule(db, operator_id: int, rule_id: int) -> None:
    from service import audit_service
    row = (await db.execute(select(ExpensePolicyRule).where(ExpensePolicyRule.id == rule_id,
                                                            ExpensePolicyRule.organization_id == await _org(db)))).scalar_one_or_none()
    if row is None:
        raise NotFound("规则不存在")
    await db.delete(row)
    await db.commit()
    await audit_service.record_async(operator_id, "finance.policy_deleted", resource_type="expense_policy_rule", resource_id=rule_id)


def pick_rule(rules: List[ExpensePolicyRule], category: str, city_level: Optional[str], employee_level: str,
              on: date) -> Optional[ExpensePolicyRule]:
    candidates = []
    for r in rules:
        if r.category != category:
            continue
        if r.effective_from and on < date.fromisoformat(r.effective_from):
            continue
        if r.effective_to and on > date.fromisoformat(r.effective_to):
            continue
        if r.city_level and r.city_level != city_level:
            continue
        if r.employee_level and r.employee_level != employee_level:
            continue
        candidates.append(r)
    candidates.sort(key=lambda r: (-(bool(r.city_level) + bool(r.employee_level)), -r.id))
    return candidates[0] if candidates else None


def check_lines(rules: List[ExpensePolicyRule], lines: List[Dict[str, Any]], *, city_level: Optional[str],
                employee_level: str, on: date) -> List[Dict[str, Any]]:
    """逐条明细检查。返回 [{index, rule_id, status: ok|over_limit|missing_receipt, limit, over_by, approval_level, message}]。"""
    results = []
    for index, line in enumerate(lines):
        rule = pick_rule(rules, line.get("category"), city_level, employee_level, on)
        result = {"index": index, "rule_id": rule.id if rule else None, "status": "ok", "limit": None,
                  "over_by": None, "approval_level": None, "message": "没有适用的费用标准" if rule is None else "符合标准"}
        if rule is not None:
            amount = _dec(line.get("amount")) or Decimal(0)
            limit = _dec(rule.amount_limit)
            if rule.receipt_required and not (line.get("invoiceNo") or "").strip():
                result.update(status="missing_receipt", message=f"{CATEGORIES.get(rule.category)}按规定必须有发票，请填写发票号或上传发票")
            elif limit is not None and amount > limit:
                result.update(status="over_limit", limit=str(limit), over_by=str(amount - limit),
                              approval_level=rule.approval_level,
                              message=f"超出标准 {amount - limit}（上限 {limit}），需要填写超标准说明，"
                                      f"并由{APPROVAL_LEVELS.get(rule.approval_level)}审批")
        results.append(result)
    return results


async def employee_level(db, user_id: int, team_id: int) -> str:
    from models.enterprise_dao import is_team_admin_of_team_async
    try:
        return "manager" if await is_team_admin_of_team_async(db, user_id, team_id) else "staff"
    except Exception:  # noqa: BLE001
        return "staff"


async def check_claim(db, user_id: int, team_id: int, lines: List[Dict[str, Any]], city_level: Optional[str]) -> List[Dict[str, Any]]:
    rules = list((await db.execute(select(ExpensePolicyRule).where(ExpensePolicyRule.organization_id == await _org(db)))).scalars().all())
    return check_lines(rules, lines, city_level=city_level, employee_level=await employee_level(db, user_id, team_id),
                       on=(utcnow() + timedelta(hours=8)).date())
