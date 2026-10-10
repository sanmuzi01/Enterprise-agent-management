"""商机风险：确定性规则（纯函数，方便测试）+ 模型从文字里识别的风险（必须引用原文，引用不到原文的一律丢掉）。

每条风险都带证据（evidence）——写清楚是哪一天、哪个数字、哪段原话让系统得出这个判断，销售看了能核实。
"""
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

OPEN_STAGES = ("LEAD", "QUALIFIED", "PROPOSAL", "NEGOTIATION")
STAGE_LABELS = {"LEAD": "线索", "QUALIFIED": "已确认需求", "PROPOSAL": "方案报价", "NEGOTIATION": "商务谈判",
                "WON": "赢单", "LOST": "输单"}
INBOUND_TYPES = ("email", "meeting", "call", "chat")          # 算作“和客户有沟通”的活动
TOUCH_TYPES = INBOUND_TYPES + ("followup",)
DECISION_TITLES = re.compile(r"总|总监|经理|负责人|董事|老板|CEO|CTO|CFO|COO|CIO|VP|Head|Director|Manager", re.I)

MODEL_RISKS = {
    "BUDGET_INSUFFICIENT": "客户预算不足",
    "DECISION_MAKER_ABSENT": "决策人未参与",
    "COMPETITOR": "有竞争对手",
    "CAPABILITY_MISMATCH": "产品能力不匹配",
    "TIMELINE_CONFLICT": "上线时间冲突",
}
RULE_RISKS = {
    "NO_FOLLOWUP_LONG": "长时间未跟进",
    "CLOSE_DATE_NEAR_STAGE_STALE": "成交日期临近但阶段未更新",
    "QUOTE_NO_RESPONSE": "报价后无回复",
    "AMOUNT_DECREASED": "商机金额下降",
    "MULTIPLE_DELAYS": "多次延期",
    "NO_NEXT_STEP": "没有明确的下一步",
    "KEY_CONTACT_MISSING": "关键联系人缺失",
}
RISK_LABELS = {**RULE_RISKS, **MODEL_RISKS}


@dataclass
class Risk:
    risk_code: str
    level: str                       # high / medium / low
    evidence: str
    suggested_action: str
    opportunity_id: Optional[int] = None
    source: str = "rule"
    source_activity_id: Optional[int] = None

    def public(self) -> Dict[str, Any]:
        return {"risk_code": self.risk_code, "label": RISK_LABELS.get(self.risk_code, self.risk_code), "level": self.level,
                "evidence": self.evidence, "suggested_action": self.suggested_action,
                "opportunity_id": self.opportunity_id, "source": self.source}


def _day(value: Any) -> Optional[date]:
    if not value:
        return None
    if isinstance(value, datetime):
        return (value + timedelta(hours=8)).date()
    if isinstance(value, date):
        return value
    text = str(value)
    try:
        if len(text) == 10:
            return date.fromisoformat(text)
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            parsed = parsed.replace(tzinfo=None) - (parsed.utcoffset() or timedelta())
        return (parsed + timedelta(hours=8)).date()
    except ValueError:
        return None


def _amount(value: Any) -> Optional[Decimal]:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError):
        return None


def _money(value: Decimal) -> str:
    return f"{value:,.2f}".rstrip("0").rstrip(".")


def evaluate(today: date, customer: Dict[str, Any], activities: List[Dict[str, Any]],
             history: Dict[int, List[Dict[str, Any]]], open_tasks: int, *, stale_days: int = 14,
             quote_wait_days: int = 7, close_window_days: int = 7, stage_stale_days: int = 14) -> List[Risk]:
    """customer：业务系统的客户摘要（contacts、recentFollowUps、opportunities）；
    activities：本部门这个客户的活动 [{id, activity_type, occurred_at(date|datetime), title}]；
    history：商机变化记录 {opportunity_id: [{stage, amount, expected_close_date, captured_at}]}（按时间正序）；
    open_tasks：这个客户还没完成的待办数。"""
    risks: List[Risk] = []
    name = customer.get("name") or "该客户"
    opportunities = customer.get("opportunities") or []
    open_opps = [o for o in opportunities if o.get("stage") in OPEN_STAGES]

    # 1. 长时间未跟进：最近一次沟通 / 已确认跟进
    touches = [(_day(a["occurred_at"]), f"{a.get('type_label') or a['activity_type']}「{a.get('title') or ''}」")
               for a in activities if a["activity_type"] in TOUCH_TYPES]
    touches += [(_day(f.get("confirmedAt") or f.get("createdAt")), "跟进记录")
                for f in customer.get("recentFollowUps") or [] if f.get("status") == "CONFIRMED"]
    touches = [t for t in touches if t[0]]
    if open_opps or touches:
        if touches:
            last_day, last_what = max(touches, key=lambda t: t[0])
            gap = (today - last_day).days
            if gap >= stale_days:
                risks.append(Risk("NO_FOLLOWUP_LONG", "high" if gap >= stale_days * 2 else "medium",
                                  f"最近一次沟通是 {last_day.isoformat()} 的{last_what}，已经 {gap} 天没有跟进",
                                  f"联系{name}了解最新进展，并记录跟进"))
        elif open_opps:
            created = _day(customer.get("createdAt")) or today
            if (today - created).days >= stale_days:
                risks.append(Risk("NO_FOLLOWUP_LONG", "high", f"有进行中的商机，但从未记录过任何沟通（建档于 {created.isoformat()}）",
                                  f"安排一次与{name}的沟通并记录"))

    # 2. 报价后无回复
    quotes = sorted((a for a in activities if a["activity_type"] == "quote"), key=lambda a: _day(a["occurred_at"]) or today)
    if quotes:
        quote = quotes[-1]
        quote_day = _day(quote["occurred_at"])
        replies = [a for a in activities if a["activity_type"] in INBOUND_TYPES and _day(a["occurred_at"])
                   and _day(a["occurred_at"]) > quote_day]
        waited = (today - quote_day).days
        if not replies and waited >= quote_wait_days:
            risks.append(Risk("QUOTE_NO_RESPONSE", "high" if waited >= quote_wait_days * 2 else "medium",
                              f"报价发送于 {quote_day.isoformat()}（{quote.get('title') or '报价'}），之后 {waited} 天没有有效沟通",
                              "联系采购负责人确认评审进度", source_activity_id=quote.get("id")))

    for opp in open_opps:
        oid = int(opp["id"])
        stage = STAGE_LABELS.get(opp.get("stage"), opp.get("stage"))
        rows = history.get(oid) or []
        close_day = _day(opp.get("expectedCloseDate"))

        # 3. 成交日期临近（或已过）但阶段很久没变
        if close_day and (close_day - today).days <= close_window_days:
            stage_since = _day(opp.get("updatedAt")) or today
            for prev, cur in zip(rows, rows[1:]):
                if prev["stage"] != cur["stage"]:
                    stage_since = _day(cur["captured_at"])
            stale = (today - stage_since).days
            if stale >= stage_stale_days and opp.get("stage") != "NEGOTIATION":
                left = (close_day - today).days
                when = f"已经过了 {-left} 天" if left < 0 else f"还有 {left} 天"
                risks.append(Risk("CLOSE_DATE_NEAR_STAGE_STALE", "high" if left <= 0 else "medium",
                                  f"预计成交日期 {close_day.isoformat()}（{when}），阶段仍是「{stage}」，已经 {stale} 天没有变化",
                                  "和客户确认成交时间，更新商机阶段或预计成交日期", opportunity_id=oid))

        # 4. 金额下降
        amounts = [(r["captured_at"], _amount(r["amount"])) for r in rows if _amount(r["amount"]) is not None]
        current = _amount(opp.get("amount"))
        if amounts and current is not None:
            peak_at, peak = max(amounts, key=lambda a: a[1])
            if current < peak:
                drop = (peak - current) / peak * 100 if peak else Decimal(0)
                risks.append(Risk("AMOUNT_DECREASED", "high" if drop >= 30 else "medium",
                                  f"商机金额从 {_money(peak)}（{_day(peak_at)}）降到 {_money(current)}，下降 {drop:.0f}%",
                                  "了解金额下调的原因（范围缩小、预算削减还是竞争压价）", opportunity_id=oid))

        # 5. 多次延期：预计成交日期被往后推了 2 次以上
        delays, previous = [], None
        for r in rows:
            d = _day(r.get("expected_close_date"))
            if d and previous and d > previous:
                delays.append((previous, d))
            previous = d or previous
        if len(delays) >= 2:
            chain = "，".join(f"{a.isoformat()}→{b.isoformat()}" for a, b in delays[-3:])
            risks.append(Risk("MULTIPLE_DELAYS", "high" if len(delays) >= 3 else "medium",
                              f"预计成交日期已推迟 {len(delays)} 次：{chain}",
                              "确认客户真实的采购时间表和阻碍因素", opportunity_id=oid))

        # 6. 没有明确下一步
        if not (opp.get("nextStep") or "").strip() and open_tasks == 0:
            risks.append(Risk("NO_NEXT_STEP", "medium" if opp.get("stage") in ("PROPOSAL", "NEGOTIATION") else "low",
                              f"商机处于「{stage}」阶段，没有填写下一步，也没有未完成的待办",
                              "约定下一次沟通的时间和内容", opportunity_id=oid))

    # 7. 关键联系人缺失
    contacts = customer.get("contacts") or []
    late = [o for o in open_opps if o.get("stage") in ("PROPOSAL", "NEGOTIATION")]
    if open_opps and not contacts:
        risks.append(Risk("KEY_CONTACT_MISSING", "medium", "有进行中的商机，但客户没有登记任何联系人",
                          "补充客户联系人（至少包括对接人和决策人）"))
    elif late and not any(DECISION_TITLES.search(c.get("title") or "") for c in contacts):
        titles = "、".join(f"{c.get('name')}（{c.get('title') or '无职务'}）" for c in contacts[:5])
        risks.append(Risk("KEY_CONTACT_MISSING", "medium",
                          f"商机已到「{STAGE_LABELS.get(late[0]['stage'])}」阶段，登记的联系人里没有决策人：{titles}",
                          "确认并接触客户的决策人", opportunity_id=int(late[0]["id"])))
    return risks


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def grounded_model_risks(raw: Any, activities: Dict[int, Dict[str, Any]]) -> List[Risk]:
    """模型识别的文字风险：只认 MODEL_RISKS 里的类型，evidence 必须是对应活动里逐字出现的原话。"""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return []
    items = raw.get("risks") if isinstance(raw, dict) else raw
    risks = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict) or item.get("risk_code") not in MODEL_RISKS:
            continue
        quote = str(item.get("evidence") or "").strip().strip("“”\"'「」")
        try:
            activity = activities.get(int(item.get("activity_id")))
        except (TypeError, ValueError):
            activity = None
        if not activity or len(_squash(quote)) < 4:
            continue
        source = _squash(f"{activity.get('title') or ''}{activity.get('content') or ''}")
        if _squash(quote) not in source:
            continue                                  # 引用不到原文：不采信
        when = _day(activity["occurred_at"])
        level = item.get("level") if item.get("level") in ("high", "medium", "low") else "medium"
        risks.append(Risk(item["risk_code"], level,
                          f"{when.isoformat() if when else ''} {activity.get('type_label') or ''}「{activity.get('title') or ''}」中提到：“{quote[:200]}”".strip(),
                          str(item.get("suggested_action") or "")[:300] or f"针对“{MODEL_RISKS[item['risk_code']]}”制定应对方案",
                          source="model", source_activity_id=activity.get("id")))
    return risks
