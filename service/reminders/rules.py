"""具体提醒规则。每条规则计算"此刻应存在的提醒"，由 sync_reminders 同步为待办与通知；
条件不再成立的待办会被自动关闭（已审批、已跟进、已补发票）。

调用 Java 业务系统时，用"本来就有权看到这些数据的人"的身份签名（部门负责人看本部门待审批、
部门成员看本部门客户、员工看自己的报销单），权限标志如实从数据库计算，不做任何提权。
"""
from datetime import timedelta
from typing import List, Tuple

from sqlalchemy import func, select

from models.init_db import AutomationWork, CustomerActivity, User, WorkItem
from service import notification_center
from service.reminders.base import (Reminder, ReminderRule, active_teams, approvers, beijing_day_start, env_int,
                                    parse_instant, sync_reminders, team_members)
from service.exceptions import PermissionDenied
from utils.timeutil import utcnow

LEAVE_TYPES = {"annual": "年假", "sick": "病假", "personal": "事假"}


async def user_names(db, ids):
    ids = [i for i in ids if i]
    if not ids:
        return {}
    return dict((await db.execute(select(User.id, User.name).where(User.id.in_(ids)))).all())


# ---------------- 审批等待超时 ----------------

async def _pending_for_team(db, team, approver_id):
    from service import department_workspace_service as oa, finance_workspace_service as fin
    from service import hr_service as hr
    from service import it_service as it
    from service import procurement_workspace_service as proc
    results = []
    for kind, loader in (("leave", oa.list_team_pending_leave_requests_async),
                         ("purchase", proc.list_team_pending_purchase_requests_async),
                         ("expense", fin.list_team_pending_expense_claims_async),
                         ("ticket", it.list_team_pending_async),
                         ("hr", hr.team_pending_approval_async)):
        results.extend((kind, item) for item in await loader(db, approver_id, team.id))
    return results


def _approval_reminder(kind, item, team, approver_id, now, wait_hours, names):
    submitted = parse_instant(item.get("submittedAt") or item.get("createdAt"))
    if submitted is None:
        return None
    waited = (now - submitted).total_seconds() / 3600
    if waited < wait_hours:
        return None
    applicant = item.get("applicantUserId") or item.get("requesterUserId") or item.get("employeeUserId")
    if applicant == approver_id or item.get("initiatorUserId") == approver_id:
        return None  # 不能审批自己提交的单据、与自己有关的人事事项
    priority, due = ("high" if waited >= wait_hours * 3 else "normal"), submitted + timedelta(hours=wait_hours * 3)
    if kind == "leave":
        name = f"请假单 #{item['id']}"
        detail = (f"{LEAVE_TYPES.get(item.get('leaveTypeCode'), item.get('leaveTypeCode'))} "
                  f"{item['startDate']} 至 {item['endDate']}（{item['days']:g} 天）")
        start = beijing_day_start(item["startDate"])
        due = min(due, start)
        if start - now <= timedelta(days=2):
            priority = "high"
            detail += "，即将开始"
    elif kind == "purchase":
        name, detail = f"采购申请 #{item['id']}", f"金额 ¥{float(item['totalAmount']):,.2f}"
    elif kind == "ticket":
        name, detail = f"IT 工单 #{item['id']}", f"{item.get('categoryLabel') or item['category']}「{item['title']}」"
    elif kind == "hr":
        name, detail = f"{item['caseTypeLabel']}事项 #{item['id']}", f"生效日期 {item['effectiveDate']}"
    else:
        name, detail = f"报销单 #{item['id']}", f"金额 ¥{float(item['totalAmount']):,.2f}"
    return Reminder(user_id=approver_id, key=f"approval:{kind}:{item['id']}", team_id=team.id,
                    title=f"{team.name}：{name} 已等待审批 {int(waited)} 小时",
                    detail=f"申请人 {names.get(applicant, f'#{applicant}')}，{detail}",
                    priority=priority, due_at=due)


async def run_approval_waiting(db) -> Tuple[int, int]:
    wait_hours, now, reminders = env_int("REMIND_APPROVAL_WAIT_HOURS", 24), utcnow(), []
    for team in await active_teams(db):
        team_approvers = await approvers(db, team)
        if not team_approvers:
            continue
        pending = await _pending_for_team(db, team, team_approvers[0])
        names = await user_names(db, {i.get("applicantUserId") or i.get("requesterUserId") or i.get("employeeUserId")
                                      for _, i in pending})
        for approver_id in team_approvers:
            for kind, item in pending:
                reminder = _approval_reminder(kind, item, team, approver_id, now, wait_hours, names)
                if reminder:
                    reminders.append(reminder)
    return await sync_reminders(db, "approval_waiting", "approval", reminders)


# ---------------- 客户长期未跟进 ----------------

async def run_stale_customers(db) -> Tuple[int, int]:
    from service import crm_workspace_service as crm
    stale_days, now, reminders = env_int("REMIND_CUSTOMER_STALE_DAYS", 14), utcnow(), []
    for team in await active_teams(db, "sales"):
        members = await team_members(db, team.id)
        if not members:
            continue
        admins = await team_members(db, team.id, "admin") or members[:1]
        for customer in await crm.list_team_customers_async(db, members[0], team.id):
            summary = await crm.get_customer_summary_async(db, members[0], team.id, customer["id"])
            followups = summary.get("recentFollowUps") or []
            last = parse_instant(followups[0]["createdAt"]) if followups else None
            # CRM Copilot 收进来的邮件、会议、电话纪要也算跟进
            touched = (await db.execute(select(func.max(CustomerActivity.occurred_at)).where(
                CustomerActivity.team_id == team.id, CustomerActivity.customer_id == customer["id"],
                CustomerActivity.activity_type.in_(("email", "meeting", "call", "chat", "followup"))))).scalar()
            if touched is not None and (last is None or touched > last):
                last = touched
            ever = last is not None
            last = last or parse_instant(customer.get("createdAt"))
            if last is None or now - last < timedelta(days=stale_days):
                continue
            days = (now - last).days
            owner = customer["ownerUserId"] if customer["ownerUserId"] in members else admins[0]
            reminders.append(Reminder(
                user_id=owner, key=f"stale-customer:{customer['id']}", team_id=team.id,
                title=f"客户「{customer['name']}」已 {days} 天没有跟进",
                detail="上次跟进：" + (last.date().isoformat() if ever else "从未跟进（按建档时间计算）"),
                priority="high" if days >= stale_days * 2 else "normal", due_at=last + timedelta(days=stale_days)))
    return await sync_reminders(db, "stale_customer", "crm_followup", reminders)


# ---------------- 商机风险扫描 ----------------

async def run_crm_risks(db) -> Tuple[int, int]:
    """每个销售部门的客户都按确定性规则扫一遍风险（不调用模型）。同一风险只有一条记录、只生成一条建议，
    不再成立的自动解除；建议要销售自己确认才进待办。返回（扫描的客户数，0）。"""
    from service import crm_workspace_service as crm
    from service.crm import insights
    scanned = 0
    for team in await active_teams(db, "sales"):
        members = await team_members(db, team.id)
        if not members:
            continue
        for customer in await crm.list_team_customers_async(db, members[0], team.id):
            try:
                await insights.scan_customer(db, members[0], team.id, customer["id"])
                scanned += 1
            except Exception:  # noqa: BLE001 —— 一个客户失败不影响其他客户
                await db.rollback()
    return scanned, 0


# ---------------- 报销缺发票 ----------------

async def run_missing_invoices(db) -> Tuple[int, int]:
    from service import finance_workspace_service as fin
    reminders, seen = [], set()
    for team in await active_teams(db):
        for user_id in await team_members(db, team.id):
            if user_id in seen:
                continue
            seen.add(user_id)
            for claim in await fin.list_my_expense_claims_async(user_id):
                if claim["status"] not in ("DRAFT", "SUBMITTED"):
                    continue
                missing = [l for l in claim.get("lines") or [] if not l.get("invoiceNo")]
                if missing:
                    reminders.append(Reminder(
                        user_id=user_id, key=f"expense-invoice:{claim['id']}", team_id=claim.get("teamId"),
                        title=f"报销单 #{claim['id']} 有 {len(missing)} 条费用缺发票",
                        detail="、".join(f"{l['description']} ¥{float(l['amount']):,.2f}" for l in missing[:5])
                               + "；审批前请补充票据"))
    return await sync_reminders(db, "missing_invoice", "expense_invoice", reminders)


# ---------------- 记账凭证待核对 ----------------

async def run_pending_vouchers(db) -> Tuple[int, int]:
    """财务部门成员：报销批准后自动生成的凭证草稿还没核对入账，或有已批准报销单没有生成凭证。"""
    from service import finance_voucher_service as vouchers
    wait_hours, now, reminders = env_int("REMIND_VOUCHER_WAIT_HOURS", 24), utcnow(), []
    for team in await active_teams(db, "finance"):
        members = await team_members(db, team.id)
        if not members:
            continue
        drafts = unbooked = None
        for caller in members:   # 用第一个企业身份仍有效的成员读取（停用的成员读不到，也不该收到提醒）
            try:
                drafts = await vouchers.list_vouchers_async(db, caller, team.id, status="DRAFT", limit=200)
                unbooked = await vouchers.list_unbooked_async(db, caller, team.id)
                break
            except PermissionDenied:
                continue
        if drafts is None or (not drafts and not unbooked):
            continue
        needs_review = [d for d in drafts if d.get("riskLevel") in ("WARN", "BLOCK")]
        created = [parse_instant(d.get("createdAt")) for d in drafts]
        oldest = min((c for c in created if c), default=None)
        waited = (now - oldest).total_seconds() / 3600 if oldest else 0
        parts = []
        if drafts:
            parts.append(f"{len(drafts)} 张凭证待核对" + (f"（{len(needs_review)} 张有风险需核对）" if needs_review else ""))
        if unbooked:
            parts.append(f"{len(unbooked)} 张已批准报销单还没有凭证")
        priority = "high" if needs_review or waited >= wait_hours * 3 else "normal"
        for user_id in members:
            reminders.append(Reminder(
                user_id=user_id, key=f"voucher-pending:{team.id}", team_id=team.id,
                title=f"{team.name}：" + "，".join(parts),
                detail="报销批准后系统已自动生成凭证草稿，请核对科目与风险项后确认入账",
                priority=priority, due_at=(oldest + timedelta(hours=wait_hours)) if oldest else None,
                link="/department", notify=waited >= wait_hours or bool(needs_review)))
    return await sync_reminders(db, "pending_voucher", "voucher_pending", reminders)


# ---------------- IT 工单：超时与待接单（IT 部门） ----------------

async def run_it_sla(db) -> Tuple[int, int]:
    """IT 部门成员：有工单已超过处理时限，或还没人接单。接单/解决后自动关闭。"""
    from service import it_service as it
    reminders = []
    for team in await active_teams(db, "it"):
        members = await team_members(db, team.id)
        overdue = unassigned = None
        for caller in members:   # 用第一个企业身份仍有效的成员读取
            try:
                overdue = await it.desk_list_tickets_async(db, caller, team.id, overdue=True, limit=200)
                unassigned = await it.desk_list_tickets_async(db, caller, team.id, status="OPEN", assignee="unassigned", limit=200)
                break
            except PermissionDenied:
                continue
        if overdue is None or (not overdue and not unassigned):
            continue
        at_risk = [t for t in unassigned if t.get("slaStatus") == "AT_RISK"]
        parts = []
        if overdue:
            parts.append(f"{len(overdue)} 张工单已超过处理时限")
        if unassigned:
            parts.append(f"{len(unassigned)} 张待接单" + (f"（{len(at_risk)} 张即将超时）" if at_risk else ""))
        first = min([t for t in overdue + unassigned if t.get("slaDueAt")], key=lambda t: t["slaDueAt"], default=None)
        for user_id in members:
            reminders.append(Reminder(
                user_id=user_id, key=f"it-sla:{team.id}", team_id=team.id, title=f"{team.name}：" + "，".join(parts),
                detail=(f"最急的是 #{first['id']}「{first['title']}」" if first else None),
                priority="high" if overdue else "normal", due_at=parse_instant(first["slaDueAt"]) if first else None,
                notify=bool(overdue or at_risk)))
    return await sync_reminders(db, "it_sla", "it_ticket", reminders)


# ---------------- IT 工单：等待你补充 / 请确认已解决（申请人） ----------------

async def run_ticket_followup(db) -> Tuple[int, int]:
    from service import it_service as it
    reminders, seen, now = [], set(), utcnow()
    confirm_after = timedelta(hours=env_int("REMIND_TICKET_CONFIRM_HOURS", 48))
    for team in await active_teams(db):
        for user_id in await team_members(db, team.id):
            if user_id in seen:
                continue
            seen.add(user_id)
            for ticket in await it.list_my_tickets_async(db, user_id):
                if ticket["status"] == "WAITING_USER":
                    reminders.append(Reminder(
                        user_id=user_id, key=f"ticket-waiting:{ticket['id']}", team_id=ticket.get("teamId"),
                        title=f"IT 工单 #{ticket['id']}「{ticket['title']}」在等你补充信息",
                        detail="IT 需要更多信息才能继续处理，请在工单里回复", priority="high"))
                elif ticket["status"] == "RESOLVED":
                    updated = parse_instant(ticket.get("updatedAt"))
                    if updated and now - updated >= confirm_after:
                        reminders.append(Reminder(
                            user_id=user_id, key=f"ticket-confirm:{ticket['id']}", team_id=ticket.get("teamId"),
                            title=f"IT 工单 #{ticket['id']}「{ticket['title']}」已解决，请确认",
                            detail="问题解决了请确认关闭；没有解决可以重新打开"))
    return await sync_reminders(db, "ticket_followup", "ticket_followup", reminders)


# ---------------- 人事事项：办理任务到期（各办理方） ----------------

async def run_hr_tasks(db) -> Tuple[int, int]:
    """人事/IT/财务/负责人/员工本人：分到自己名下的入转调离办理任务，3 天内到期或已逾期。办完自动关闭。"""
    from datetime import date
    from service import hr_service as hr
    reminders, seen = [], set()
    soon = (utcnow() + timedelta(hours=8)).date() + timedelta(days=env_int("REMIND_HR_TASK_DAYS", 3))
    for team in await active_teams(db):
        for user_id in await team_members(db, team.id):
            if user_id in seen:
                continue
            seen.add(user_id)
            try:
                tasks = await hr.my_tasks_async(db, user_id, team.id)
            except PermissionDenied:
                continue
            for task in tasks:
                if date.fromisoformat(task["dueDate"]) > soon:
                    continue
                who = task.get("employeeName") or f"#{task['employeeUserId']}"
                reminders.append(Reminder(
                    user_id=user_id, key=f"hr-task:{task['taskId']}", team_id=task["teamId"],
                    title=f"{task['caseTypeLabel']}办理：{task['title']}（{who}）",
                    detail=f"期限 {task['dueDate']}" + ("，已逾期" if task["overdue"] else ""),
                    priority="high" if task["overdue"] else "normal", due_at=beijing_day_start(task["dueDate"])))
    return await sync_reminders(db, "hr_task", "hr_task", reminders)


# ---------------- 待办到期（AI 成果后续事项、手工待办） ----------------

async def run_task_due(db) -> Tuple[int, int]:
    """不生成新待办，只给 24 小时内到期或已逾期的开放待办发一次通知。"""
    now, sent = utcnow(), 0
    rows = (await db.execute(select(WorkItem).where(
        WorkItem.status == "open", WorkItem.source_type.in_(("automation", "manual")),
        WorkItem.due_at.is_not(None), WorkItem.due_at <= now + timedelta(hours=24)))).scalars().all()
    for item in rows:
        overdue = item.due_at < now
        title = f"待办{'已逾期' if overdue else '即将到期'}：{item.title}"
        sent += int(await notification_center.notify(
            db, item.user_id, "task_due", title, body=item.detail, link="/todos",
            dedupe_key=f"task-due:{item.id}:{'overdue' if overdue else 'soon'}:{item.due_at:%Y%m%d}"))
    return sent, 0


# ---------------- 每周部门工作摘要 ----------------

async def run_weekly_digest(db) -> Tuple[int, int]:
    now, sent = utcnow(), 0
    week = (now + timedelta(hours=8)).strftime("%G-W%V")
    since = now - timedelta(days=7)
    for team in await active_teams(db):
        rows = (await db.execute(select(AutomationWork.kind, AutomationWork.status, func.count()).where(
            AutomationWork.team_id == team.id, AutomationWork.created_at >= since
        ).group_by(AutomationWork.kind, AutomationWork.status))).all()
        processed = sum(r[2] for r in rows)
        applied = sum(r[2] for r in rows if r[1] == "applied")
        for admin_id in await team_members(db, team.id, "admin"):
            pending = (await db.execute(select(func.count()).where(
                WorkItem.user_id == admin_id, WorkItem.status == "open", WorkItem.team_id == team.id,
                WorkItem.rule == "approval_waiting"))).scalar() or 0
            body = (f"近 7 天部门 AI 材料整理 {processed} 份，保存为业务草稿 {applied} 份；"
                    f"当前等待你审批超过时限的单据 {pending} 件。")
            sent += int(await notification_center.notify(
                db, admin_id, "digest", f"{team.name} 本周工作摘要", body=body, link="/department",
                dedupe_key=f"digest:{team.id}:{week}"))
    return sent, 0


from service.reminders.responsibility_rules import run_resp_accept, run_resp_due, run_resp_review  # noqa: E402
from service.reminders.attendance_reminders import run_attendance_decide, run_attendance_explain  # noqa: E402

RULES: List[ReminderRule] = [
    ReminderRule("approval_waiting", "审批等待超时", "approval", 30, run_approval_waiting,
                 "部门负责人：请假、采购、报销提交后超过时限仍未审批（请假临近开始升为高优先级）"),
    ReminderRule("stale_customer", "客户长期未跟进", "crm_followup", 360, run_stale_customers,
                 "销售：客户超过指定天数没有跟进记录"),
    ReminderRule("crm_risk_scan", "商机风险扫描", "crm_risk", 720, run_crm_risks,
                 "销售：按规则扫描客户和商机风险（长期未跟进、报价无回复、临近成交未推进、金额下降、多次延期……），生成下一步建议"),
    ReminderRule("missing_invoice", "报销缺发票", "expense_invoice", 360, run_missing_invoices,
                 "员工：未完成的报销单中有费用缺发票号"),
    ReminderRule("pending_voucher", "记账凭证待核对", "voucher_pending", 120, run_pending_vouchers,
                 "财务：报销批准后生成的凭证草稿超过时限未核对入账，或有已批准报销单没有凭证"),
    ReminderRule("it_sla", "IT 工单超时", "it_ticket", 30, run_it_sla,
                 "IT 部门：工单超过处理时限，或还没人接单（接单/解决后自动关闭）"),
    ReminderRule("ticket_followup", "工单待你处理", "ticket_followup", 120, run_ticket_followup,
                 "申请人：IT 在等你补充信息，或已解决的工单超过 48 小时还没确认"),
    ReminderRule("hr_task", "人事办理任务", "hr_task", 120, run_hr_tasks,
                 "入转调离的各办理方：分给自己的办理任务 3 天内到期或已逾期（办完自动关闭）"),
    ReminderRule("resp_accept", "责任待接受", "responsibility", 60, run_resp_accept,
                 "员工：有责任等你接受；指派人：员工超过时限没接受，或对责任提出了异议（接受/调整后自动关闭）"),
    ReminderRule("resp_due", "责任到期与受阻", "responsibility", 60, run_resp_due,
                 "主责员工：责任 2 天内到期或已逾期；指派人：已逾期，或阻塞超过 48 小时（完成/解除后自动关闭）"),
    ReminderRule("resp_review", "责任待验收", "responsibility", 60, run_resp_review,
                 "验收人：员工已提交成果等你验收，超过 24 小时升为高优先级"),
    ReminderRule("attendance_explain", "考勤异常待说明", "attendance", 360, run_attendance_explain,
                 "员工：有考勤异常等你说明原因（每人一条汇总，超过 2 天升为高优先级，说明后自动关闭）"),
    ReminderRule("attendance_decide", "考勤异常待认定", "attendance", 360, run_attendance_decide,
                 "部门负责人和人事：员工已说明、等你认定的考勤异常（每人一条汇总，自己的不算，认定后自动关闭）"),
    ReminderRule("task_due", "待办到期", "task_due", 15, run_task_due,
                 "所有人：24 小时内到期或已逾期的待办"),
    ReminderRule("weekly_digest", "每周工作摘要", "digest", 60 * 24, run_weekly_digest,
                 "部门负责人：每周一份部门 AI 整理与审批积压摘要"),
]
RULES_BY_NAME = {rule.name: rule for rule in RULES}
