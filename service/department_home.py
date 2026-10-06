"""部门首页：按部门业务类型和调用者的身份（成员 / 负责人 / 企业管理员）给出一屏概览。

只读汇总，每个数字都来自已有的业务接口（权限判断沿用各模块自己的规则，这里不另起一套）：
某一项读不到（业务服务暂不可用、或者调用者对该项没有权限）就跳过这一项，不影响其它卡片。
卡片带 section，前端点卡片直接切到对应的工作区。
"""
from typing import Any, Awaitable, Callable, Dict, List, Optional

from sqlalchemy import text

from models.enterprise_dao import is_team_admin_of_team_async
from service import enterprise_access, work_item_service
from service.department_access import DEPARTMENT_LABELS, require_team_member_async
from service.exceptions import AppError
from utils.logger_handler import get_logger

logger = get_logger("department_home")

BUSINESS_LABELS = {"finance": "财务记账", "it": "IT 服务台", "hr": "人事办理", "sales": "客户与商机", "procurement": "采购业务"}


def _card(key: str, label: str, value: Any, hint: str, section: str, tone: str = "normal", tab: Optional[str] = None) -> Dict[str, Any]:
    card = {"key": key, "label": label, "value": value, "hint": hint, "section": section, "tone": tone}
    if tab:
        card["tab"] = tab   # 责任协同分区里直接打开哪个视图
    return card


async def _safe(name: str, loader: Callable[[], Awaitable[Any]]) -> Optional[Any]:
    try:
        return await loader()
    except AppError as exc:   # 没权限 / 业务服务拒绝：这一项不显示
        logger.info("首页卡片 %s 跳过：%s", name, exc.message)
        return None
    except Exception:  # noqa: BLE001 —— 首页不因为某一项读不到而整体失败
        logger.exception("首页卡片 %s 读取失败", name)
        return None


async def build_home(db, user_id: int, team_id: int) -> Dict[str, Any]:
    from service import (crm_workspace_service as crm, department_workspace_service as oa, finance_voucher_service as vouchers,
                         finance_workspace_service as fin, hr_service as hr, it_service as it,
                         procurement_workspace_service as proc, responsibility_service as resp)
    await require_team_member_async(db, user_id, team_id, "leave")   # 请假是所有部门通用的模块：只校验成员身份
    row = (await db.execute(text("SELECT name, department_code FROM teams WHERE id = :t"), {"t": team_id})).first()
    code = row.department_code
    is_head = await is_team_admin_of_team_async(db, user_id, team_id)
    org_admin = await enterprise_access.is_org_admin_async(db, user_id)
    hr_actor = await _safe("hr_actor", lambda: hr.actor_for(db, user_id, team_id))
    roles = hr_actor["roles"] if hr_actor else []

    cards: List[Dict[str, Any]] = []
    counts = await work_item_service.counts(db, user_id)
    cards.append(_card("todos", "我的待办", counts.get("open", 0),
                       f"其中逾期 {counts.get('overdue', 0)} 项" if counts.get("overdue") else "包含审批、提醒和后续事项",
                       "todos", "danger" if counts.get("overdue") else "normal"))

    # ---- 审批（负责人 / 企业管理员）----
    if is_head or org_admin:
        pending = 0
        for name, loader in (("leave", oa.list_team_pending_leave_requests_async),
                             ("expense", fin.list_team_pending_expense_claims_async),
                             ("ticket", it.list_team_pending_async),
                             ("hr", hr.team_pending_approval_async)):
            rows = await _safe(f"approval_{name}", lambda loader=loader: loader(db, user_id, team_id))
            pending += len(rows or [])
        if code == "procurement":
            rows = await _safe("approval_purchase", lambda: proc.list_team_pending_purchase_requests_async(db, user_id, team_id))
            pending += len(rows or [])
        cards.append(_card("approvals", "待我审批", pending, "请假、报销、采购、IT 申请、人事事项", "office",
                           "warn" if pending else "normal"))

    # ---- 责任协同（所有部门：员工看自己的责任，负责人另看部门维度）----
    mine = await _safe("responsibility", lambda: resp.mine_async(db, user_id, team_id))
    if mine is not None:
        overdue, due_soon = mine.get("overdue", 0), mine.get("dueSoon", 0)
        cards.append(_card("resp_accept", "待我接受", mine.get("pendingAccept", 0), "指派给我、等我确认的责任事项", "collab",
                           "warn" if mine.get("pendingAccept") else "normal", "mine"))
        cards.append(_card("resp_doing", "我的执行中", mine.get("inProgress", 0),
                           f"其中逾期 {overdue} 项" if overdue else (f"{due_soon} 项两天内到期" if due_soon else "我已接受、正在做的责任"),
                           "collab", "danger" if overdue else "warn" if due_soon else "normal", "mine"))
        if mine.get("blocked"):
            cards.append(_card("resp_blocked", "我的受阻", mine["blocked"], "我报告了阻塞、等待解决的责任", "collab", "warn", "mine"))
        if mine.get("pendingReview"):
            cards.append(_card("resp_review", "待我验收", mine["pendingReview"], "员工提交了成果，等我对照验收标准验收", "collab", "warn", "review"))
        if mine.get("isHead"):
            team_overdue = mine.get("teamOverdue", 0)
            cards.append(_card("resp_team_overdue", "部门逾期", team_overdue, "部门里已过截止日期仍未结束的责任", "collab",
                               "danger" if team_overdue else "normal", "team"))
            rate = mine.get("weekCompletionRate")
            cards.append(_card("resp_week", "本周责任完成率", "—" if rate is None else f"{rate:g}%", "本周到期的责任里已验收完成的比例",
                               "collab", "normal", "team"))

    # ---- 考勤异常（有待办才出现：本人要说明的 / 人事和负责人要认定的）----
    from service import attendance_service as attendance
    att = await _safe("attendance", lambda: attendance.me(db, user_id, team_id))
    if att and att["open_mine"]:
        cards.append(_card("att_explain", "待说明的考勤异常", att["open_mine"], "系统按打卡记录发现的异常，请写明原因", "attendance", "warn"))
    if att and att["to_decide"]:
        cards.append(_card("att_decide", "待认定的考勤异常", att["to_decide"], "员工已说明，等你认定为异常或正常", "attendance", "warn"))

    # ---- 部门专业业务 ----
    if code == "finance":
        drafts = await _safe("vouchers", lambda: vouchers.list_vouchers_async(db, user_id, team_id, status="DRAFT", limit=200))
        if drafts is not None:
            risky = sum(1 for v in drafts if v.get("riskLevel") in ("WARN", "BLOCK"))
            cards.append(_card("vouchers", "待核对凭证", len(drafts), f"其中 {risky} 张有风险需核对" if risky else "报销批准后自动生成",
                               "business", "warn" if risky else "normal"))
        unbooked = await _safe("unbooked", lambda: vouchers.list_unbooked_async(db, user_id, team_id))
        if unbooked:
            cards.append(_card("unbooked", "未生成凭证的报销", len(unbooked), "已批准但还没有凭证，可一键补生成", "business", "warn"))
    elif code == "it":
        summary = await _safe("it_summary", lambda: it.desk_summary_async(db, user_id, team_id))
        if summary is not None:
            cards.append(_card("it_unassigned", "待接单工单", summary["unassigned"], "按处理时限排序，最急的在前", "business",
                               "warn" if summary["unassigned"] else "normal"))
            cards.append(_card("it_overdue", "已超时工单", summary["overdue"], "超过处理时限仍未解决", "business",
                               "danger" if summary["overdue"] else "normal"))
            rate = summary.get("effect", {}).get("slaMetRate")
            cards.append(_card("it_sla", "近 30 天 SLA 达成率", "—" if rate is None else f"{rate:g}%",
                               f"解决 {summary.get('effect', {}).get('resolved', 0)} 张", "business"))
    elif code == "hr" and "HR" in roles:
        summary = await _safe("hr_summary", lambda: hr.summary_async(db, user_id, team_id))
        if summary is not None:
            active = sum(v.get("PENDING_APPROVAL", 0) + v.get("IN_PROGRESS", 0) for v in summary.get("byType", {}).values())
            cards.append(_card("hr_active", "进行中的人事事项", active, "入职、转正、调岗、离职", "business"))
            cards.append(_card("hr_overdue", "逾期办理任务", summary.get("overdueTasks", 0), "各方办理清单里过了期限的", "business",
                               "danger" if summary.get("overdueTasks") else "normal"))
            if summary.get("effectPending"):
                cards.append(_card("hr_effect", "待落实系统变更", summary["effectPending"], "调岗改部门 / 离职停账号，需企业管理员", "business", "warn"))
    elif code == "sales":
        customers = await _safe("customers", lambda: crm.list_team_customers_async(db, user_id, team_id))
        if customers is not None:
            mine = sum(1 for c in customers if c.get("ownerUserId") == user_id)
            cards.append(_card("customers", "部门客户", len(customers), f"其中由我负责 {mine} 个", "business"))
    elif code == "procurement":
        mine = await _safe("purchases", lambda: proc.list_my_purchase_requests_async(user_id))
        if mine is not None:
            open_ = sum(1 for r in mine if r.get("status") in ("DRAFT", "SUBMITTED"))
            cards.append(_card("purchases", "我的进行中采购", open_, "草稿与待审批", "business"))

    # ---- 人人都有的办公事务 ----
    hr_tasks = await _safe("hr_tasks", lambda: hr.my_tasks_async(db, user_id, team_id))
    if hr_tasks:
        overdue = sum(1 for t in hr_tasks if t.get("overdue"))
        cards.append(_card("hr_tasks", "我的人事办理任务", len(hr_tasks), f"其中逾期 {overdue} 项" if overdue else "入转调离分给我的",
                           "business" if code == "hr" else "office", "danger" if overdue else "normal"))
    tickets = await _safe("tickets", lambda: it.list_my_tickets_async(db, user_id))
    if tickets is not None:
        waiting = sum(1 for t in tickets if t["status"] == "WAITING_USER")
        resolved = sum(1 for t in tickets if t["status"] == "RESOLVED")
        active = sum(1 for t in tickets if t["status"] in ("PENDING_APPROVAL", "OPEN", "IN_PROGRESS", "WAITING_USER"))
        hint = "；".join(x for x in (f"{waiting} 张等你补充" if waiting else "", f"{resolved} 张待你确认" if resolved else "") if x) or "故障、账号、权限、设备申请"
        cards.append(_card("tickets", "我的进行中工单", active, hint, "office", "warn" if waiting or resolved else "normal"))

    business = BUSINESS_LABELS.get(code) if code != "hr" or "HR" in roles else None
    return {
        "department": {"id": team_id, "name": row.name, "department_code": code,
                       "department_label": DEPARTMENT_LABELS.get(code, "未设置业务类型") if code else "未设置业务类型"},
        "identity": {"is_head": is_head, "org_admin": org_admin, "roles": roles},
        "business_label": business,
        "cards": cards,
    }
