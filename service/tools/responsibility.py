"""部门责任执行 Agent 工具：把工作文本整理成责任计划草稿，员工查询/接受/反馈/提交，负责人与验收人查询、验收、退回。

边界（与工作台完全一致，权限由业务系统强制执行）：
- Agent 可以起草、查询、总结、检查风险——这些直接执行；
- 接受责任、提交成果、验收通过、退回是真实的人事/业务决定，标为高风险：模型不能在对话里自己执行，
  必须先生成待确认单，由用户在界面上点击确认后才真正生效；
- 刻意没有：正式指派（发布）、更换责任人、改期限、取消任务、强制关闭——这些必须在工作台里由负责人操作。
Agent 只根据系统里的事实回答，不评价员工态度，也不根据聊天字数、在线时长等推测绩效。
"""
import hashlib
import json
import uuid

from service import enterprise_hub_client as hub
from service import responsibility_service as rs
from service.exceptions import AppError
from service.tools.base import BaseTool, ToolRegistry


def _fail(message, **extra) -> str:
    return json.dumps({"error": message, **extra}, ensure_ascii=False)


def _identity(tool):
    ctx = tool._ctx
    if not ctx or not ctx.user_id:
        raise ValueError("缺少用户上下文，无法调用企业业务中心")
    auth = hub.resolve_caller_context(ctx.user_id, ctx.agent_id)
    if auth["team_id"] is None:
        raise ValueError("当前用户不属于任何部门，无法使用责任协同")
    return ctx.user_id, auth["team_id"]


def _call(tool, method, path, scopes, operation, body=None, write=False, key=None, enrich=True, **params):
    """统一调用：身份按工作台同一套算法计算；业务系统的拒绝原样翻成文字返回给 Agent。"""
    try:
        user_id, team_id = _identity(tool)
        actor = rs.actor_for_sync(user_id, team_id)
        result = hub.call(method, rs.scoped(path, actor, **params), user_id, team_id, scopes, operation, json_body=body,
                          idempotency_key=(key or str(uuid.uuid4())) if write else None)
        if enrich:
            result = rs.enrich_sync(result)
    except ValueError as exc:
        return _fail(str(exc))
    except AppError as exc:
        return _fail(exc.message)
    except hub.EnterpriseHubError as exc:
        return _fail(exc.detail, status_code=exc.status_code)
    return result


def _dump(result) -> str:
    return result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)


def _brief(task: dict) -> dict:
    keys = ("id", "planTitle", "seq", "title", "statusLabel", "priorityLabel", "responsibleName", "reviewerName", "collaboratorNames",
            "dueDate", "overdue", "deliverable", "acceptanceCriteria", "blockedReason", "pendingDueDate", "objectionNote", "myActions")
    return {k: task.get(k) for k in keys if k in task}


@ToolRegistry.register
class ExtractResponsibilityPlanTool(BaseTool):
    requires_context = True
    risk_level = "write"   # 只生成「草稿」计划，不通知任何人；指派、接受、验收都要人决定

    def get_name(self) -> str:
        return "extract_responsibility_plan"

    def get_description(self) -> str:
        return ("把用户给出的会议纪要、聊天记录或通知整理成「责任计划草稿」。只提取原文明确要某人去做的具体动作："
                "responsible_name / reviewer_name 只写原文里出现的人名（没有写 null），due_text 摘录原文里的期限说法（不要自己推算日期），"
                "deliverable、acceptance_criteria 只在原文提到时填写，evidence 必须是原文逐字片段。仅讨论未决定的内容写进 unresolved。"
                "系统会按企业成员名单匹配人名、把期限换算成日期，找不到的留待补充。生成的是草稿：没有通知任何人，"
                "要让部门负责人在工作台「责任协同」里核对并发布后才会生效，不要说成已经安排好了。")

    def get_parameters(self) -> dict:
        return {"type": "object", "properties": {
            "title": {"type": "string", "description": "计划标题，不超过 30 字"},
            "source_type": {"type": "string", "description": "MEETING/CHAT/EMAIL/NOTICE/OTHER"},
            "source_text": {"type": "string", "description": "用户提供的原文，原样传入，不要改写"},
            "summary": {"type": "string", "description": "原文里明确作出的主要决定"},
            "tasks": {"type": "array", "description": "行动项列表，每项是对象：title、responsible_name、collaborator_names(数组)、"
                      "reviewer_name、due_text、deliverable、acceptance_criteria、priority(LOW/NORMAL/HIGH/URGENT)、depends_on(前置项序号数组，从 1 开始)、evidence"},
            "decisions": {"type": "array", "description": "已作出的决定：[{content, evidence}]"},
            "unresolved": {"type": "array", "description": "原文没说清的事（没有责任人、没有期限、验收人不明、仅讨论）"}},
            "required": ["title", "source_text", "tasks"]}

    def execute(self, **kwargs) -> str:
        from service.automation_spec import validate_proposal
        from service.exceptions import InvalidInput
        from service.workflows.responsibility import beijing_today, match_people
        try:
            user_id, team_id = _identity(self)
            source = str(kwargs.get("source_text") or "")
            if len(source.strip()) < 10:
                return _fail("缺少原文，无法整理：请把用户给出的会议纪要或工作文本原样传入 source_text")
            raw = {"title": kwargs.get("title"), "source_type": (kwargs.get("source_type") or "OTHER").upper(),
                   "summary": kwargs.get("summary") or "", "decisions": kwargs.get("decisions") or [],
                   "tasks": kwargs.get("tasks") or [], "unresolved": kwargs.get("unresolved") or [], "warnings": []}
            data = validate_proposal("responsibility", raw, source)   # 结构、逐字依据、期限说法必须在原文里
            people = rs.candidates_sync(user_id, team_id)
            match_people(data, source, people["members"], people["reviewers"], beijing_today())
            data = validate_proposal("responsibility", data, source, for_save=True)
            body = {"teamId": team_id, "title": data["title"], "sourceType": data["source_type"], "sourceText": source,
                    "summary": data["summary"] or None, "decisions": data["decisions"], "unresolved": data["unresolved"],
                    "tasks": [rs.task_payload({**t, "ai_responsible_user_id": t.get("responsible_user_id")}) for t in data["tasks"]],
                    "eligible": people["eligible"]}
            fingerprint = hashlib.sha256(f"{user_id}|{team_id}|{data['title']}|{source}".encode("utf-8")).hexdigest()[:32]
            plan = hub.call("POST", rs.scoped("/responsibility/plans", people["actor"]), user_id, team_id, rs.WRITE,
                            "agent_create_responsibility_plan", json_body=body, idempotency_key=f"agent-plan-{fingerprint}")
        except ValueError as exc:
            return _fail(str(exc))
        except InvalidInput as exc:
            return _fail(exc.message)
        except AppError as exc:
            return _fail(exc.message)
        except hub.EnterpriseHubError as exc:
            return _fail(exc.detail, status_code=exc.status_code)
        plan = rs.enrich_sync(plan)
        gaps = [{"seq": t["seq"], "title": t["title"], "problems": [i["message"] for i in t["issues"] if i["level"] == "BLOCK"]}
                for t in plan["tasks"] if any(i["level"] == "BLOCK" for i in t["issues"])]
        return _dump({"planId": plan["id"], "status": plan["statusLabel"], "taskCount": len(plan["tasks"]),
                      "needSupplement": gaps, "unresolved": plan["unresolved"],
                      "note": "已生成责任计划草稿，没有通知任何人。缺少的信息请在工作台「责任协同 → 责任计划」补全，由部门负责人核对后发布。"})


@ToolRegistry.register
class ListMyResponsibilitiesTool(BaseTool):
    requires_context = True
    risk_level = "read"

    def get_name(self) -> str:
        return "list_my_responsibilities"

    def get_description(self) -> str:
        return ("查看我的责任事项（主责、协办）：状态、截止日期、交付物、验收标准、是否逾期。view=mine 我主责的（默认）、collab 我协办的；"
                "可按状态筛选 PENDING_ACCEPT/IN_PROGRESS/BLOCKED/PENDING_REVIEW/DONE。用来回答“我今天最重要的事、哪些快逾期、哪项在等别人”。")

    def get_parameters(self) -> dict:
        return {"type": "object", "properties": {"view": {"type": "string", "description": "mine / collab"}, "status": {"type": "string"}}}

    def execute(self, **kwargs) -> str:
        view = kwargs.get("view") if kwargs.get("view") in ("mine", "collab") else "mine"
        result = _call(self, "GET", "/responsibility/tasks", rs.READ, "agent_list_responsibilities", view=view, status=kwargs.get("status"), limit=50)
        return result if isinstance(result, str) and result.startswith("{") else _dump([_brief(t) for t in result])


@ToolRegistry.register
class GetResponsibilityDetailTool(BaseTool):
    requires_context = True
    risk_level = "read"

    def get_name(self) -> str:
        return "get_responsibility_detail"

    def get_description(self) -> str:
        return "查看一项责任的详情：主责人、验收标准、原文依据、受阻原因、履责记录（谁在何时做了什么）、已提交的成果。"

    def get_parameters(self) -> dict:
        return {"type": "object", "properties": {"task_id": {"type": "integer"}}, "required": ["task_id"]}

    def execute(self, **kwargs) -> str:
        result = _call(self, "GET", f"/responsibility/tasks/{int(kwargs['task_id'])}", rs.READ, "agent_get_responsibility")
        if isinstance(result, dict):
            result.pop("sourceText", None)   # 原文可能很长，需要时让用户到工作台看
            result["events"] = [{"type": e["typeLabel"], "by": e["actorName"], "at": e["createdAt"], "note": e["note"]} for e in result["events"]]
            result["task"] = {k: v for k, v in result["task"].items() if v not in (None, [], "")}
        return _dump(result)


class _ActionTool(BaseTool):
    """对某一项责任做一个动作。身份、状态、权限全部由业务系统校验，失败原因原样返回。"""
    requires_context = True
    action = ""
    operation = ""
    fields: tuple = ()

    def execute(self, **kwargs) -> str:
        try:
            task_id = int(kwargs["task_id"])
        except (KeyError, TypeError, ValueError):
            return _fail("缺少 task_id")
        body = {k: kwargs.get(k) for k in self.fields if kwargs.get(k) not in (None, "")}
        result = _call(self, "POST", f"/responsibility/tasks/{task_id}/{self.action}", rs.WRITE, self.operation, body or {}, write=True)
        if isinstance(result, dict):
            task = result["task"]
            return _dump({"ok": True, "task": _brief(task), "message": f"操作已完成，这项责任现在的状态是「{task['statusLabel']}」"})
        return result


@ToolRegistry.register
class AcceptResponsibilityTool(_ActionTool):
    risk_level = "high_risk"   # 接受责任是员工的承诺，不能由模型自动代替；需要用户点确认
    action, operation, fields = "accept", "agent_accept_responsibility", ()

    def get_name(self) -> str:
        return "accept_responsibility"

    def get_description(self) -> str:
        return "接受分给我的责任事项（我承诺在截止日期前完成并提交交付物）。只有主责员工本人能接受；需要用户在界面上确认。"

    def get_parameters(self) -> dict:
        return {"type": "object", "properties": {"task_id": {"type": "integer"}}, "required": ["task_id"]}


@ToolRegistry.register
class RaiseResponsibilityObjectionTool(_ActionTool):
    risk_level = "write"
    action, operation, fields = "object", "agent_object_responsibility", ("reason",)

    def get_name(self) -> str:
        return "raise_responsibility_objection"

    def get_description(self) -> str:
        return "对分给我、还没接受的责任提出异议（责任人、期限或验收标准不合适），转为待重新协商，由负责人处理。必须写明异议内容。"

    def get_parameters(self) -> dict:
        return {"type": "object", "properties": {"task_id": {"type": "integer"}, "reason": {"type": "string", "description": "哪里不合适、建议怎么调整"}},
                "required": ["task_id", "reason"]}


@ToolRegistry.register
class ReportResponsibilityProgressTool(_ActionTool):
    risk_level = "write"
    action, operation, fields = "progress", "agent_progress_responsibility", ("note", "percent")

    def get_name(self) -> str:
        return "report_responsibility_progress"

    def get_description(self) -> str:
        return "报告我主责的责任事项的当前进度（可带完成百分比）。只记录进度，不改变状态。"

    def get_parameters(self) -> dict:
        return {"type": "object", "properties": {"task_id": {"type": "integer"}, "note": {"type": "string", "description": "当前进度"},
                                                 "percent": {"type": "integer", "description": "0-100，可选"}}, "required": ["task_id", "note"]}


@ToolRegistry.register
class ReportResponsibilityBlockerTool(_ActionTool):
    risk_level = "write"
    action, operation, fields = "block", "agent_block_responsibility", ("reason",)

    def get_name(self) -> str:
        return "report_responsibility_blocker"

    def get_description(self) -> str:
        return "报告我主责的责任事项受阻（缺少资料、等待他人、资源不足…），必须写明原因；负责人会收到提醒并协调。"

    def get_parameters(self) -> dict:
        return {"type": "object", "properties": {"task_id": {"type": "integer"}, "reason": {"type": "string"}}, "required": ["task_id", "reason"]}


@ToolRegistry.register
class SubmitDeliverableTool(_ActionTool):
    risk_level = "high_risk"   # 提交后进入验收，不能由模型自动触发
    action, operation, fields = "submit", "agent_submit_deliverable", ("summary", "link")

    def get_name(self) -> str:
        return "submit_deliverable"

    def get_description(self) -> str:
        return "提交我主责的责任事项的成果，请验收人验收。必须写明成果说明（做了什么、成果在哪里），可附 http/https 链接；需要用户在界面上确认。"

    def get_parameters(self) -> dict:
        return {"type": "object", "properties": {"task_id": {"type": "integer"}, "summary": {"type": "string"},
                                                 "link": {"type": "string", "description": "成果链接，可选"}}, "required": ["task_id", "summary"]}


@ToolRegistry.register
class ListPendingAcceptanceTool(BaseTool):
    requires_context = True
    risk_level = "read"

    def get_name(self) -> str:
        return "list_pending_acceptance"

    def get_description(self) -> str:
        return "部门负责人专用：哪些责任还没有被员工接受（含员工提出异议、等待重新协商的）。"

    def get_parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    def execute(self, **kwargs) -> str:
        result = _call(self, "GET", "/responsibility/tasks", rs.READ, "agent_pending_acceptance", view="team",
                       status="PENDING_ACCEPT,NEGOTIATING", limit=100)
        return result if isinstance(result, str) else _dump([_brief(t) for t in result])


@ToolRegistry.register
class ListPendingVerificationTool(BaseTool):
    requires_context = True
    risk_level = "read"

    def get_name(self) -> str:
        return "list_pending_verification"

    def get_description(self) -> str:
        return "验收人专用：等我验收的责任事项（员工已提交成果），含验收标准，方便对照验收。"

    def get_parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    def execute(self, **kwargs) -> str:
        result = _call(self, "GET", "/responsibility/tasks", rs.READ, "agent_pending_verification", view="review", status="PENDING_REVIEW", limit=100)
        return result if isinstance(result, str) else _dump([_brief(t) for t in result])


@ToolRegistry.register
class VerifyDeliverableTool(_ActionTool):
    risk_level = "high_risk"   # 验收通过 = 这项责任完成，不能由模型自动触发
    action, operation, fields = "verify", "agent_verify_deliverable", ("note",)

    def get_name(self) -> str:
        return "verify_deliverable"

    def get_description(self) -> str:
        return "验收通过一项责任（成果达到验收标准）。只有指定的验收人能做，主责人不能验收自己的成果；需要用户在界面上确认。"

    def get_parameters(self) -> dict:
        return {"type": "object", "properties": {"task_id": {"type": "integer"}, "note": {"type": "string", "description": "验收说明，可选"}},
                "required": ["task_id"]}


@ToolRegistry.register
class RequestReworkTool(_ActionTool):
    risk_level = "high_risk"
    action, operation, fields = "rework", "agent_request_rework", ("reason",)

    def get_name(self) -> str:
        return "request_rework"

    def get_description(self) -> str:
        return "验收不通过，退回修改。只有验收人能做，必须写明哪里没有达到验收标准；需要用户在界面上确认。"

    def get_parameters(self) -> dict:
        return {"type": "object", "properties": {"task_id": {"type": "integer"}, "reason": {"type": "string"}}, "required": ["task_id", "reason"]}


@ToolRegistry.register
class GetDepartmentResponsibilityRisksTool(BaseTool):
    requires_context = True
    risk_level = "read"

    def get_name(self) -> str:
        return "get_department_responsibility_risks"

    def get_description(self) -> str:
        return ("部门负责人专用：当前的履责风险——已逾期、未被接受（含异议）、受阻（含超过两天的）、待验收积压、在手责任较多的员工。"
                "只陈述系统里的事实，不评价员工。")

    def get_parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    def execute(self, **kwargs) -> str:
        try:
            user_id, team_id = _identity(self)
        except ValueError as exc:
            return _fail(str(exc))
        data = _call(self, "GET", "/responsibility/summary", rs.READ, "agent_responsibility_risks", enrich=False, teamId=team_id)
        if isinstance(data, str):
            return data
        ids = {r.get(k) for key in ("overdue", "waitingAccept", "blocked", "pendingReview") for r in data.get(key, [])
               for k in ("responsibleUserId", "reviewerUserId", "waitingOn") if r.get(k)} | {r["userId"] for r in data.get("load", [])}
        from models.init_db import SessionLocal
        db = SessionLocal()
        try:
            lookup = rs._names_sync(db, list(ids))
        finally:
            db.close()
        for key in ("overdue", "waitingAccept", "blocked", "pendingReview"):
            for row in data.get(key, []):
                row["responsibleName"] = lookup.get(row.get("responsibleUserId"))
                row["reviewerName"] = lookup.get(row.get("reviewerUserId"))
                row["statusLabel"] = rs.STATUS_LABELS.get(row.get("status"), row.get("status"))
        for row in data.get("load", []):
            row["name"] = lookup.get(row["userId"]) or f"用户 {row['userId']}"
        return _dump({"overdueCount": data["overdueCount"], "overdue": data["overdue"], "waitingAccept": data["waitingAccept"],
                      "blocked": data["blocked"], "blockedOver2Days": data["blockedOver2Days"], "pendingReview": data["pendingReview"],
                      "load": data["load"], "draftTasks": data["draftTasks"]})


@ToolRegistry.register
class GetResponsibilityWeeklySummaryTool(BaseTool):
    requires_context = True
    risk_level = "read"

    def get_name(self) -> str:
        return "get_responsibility_weekly_summary"

    def get_description(self) -> str:
        return ("部门负责人专用：部门责任周报——本周到期与已验收完成、按时率、首次验收通过率、接受责任的中位时间、受阻与逾期、"
                "无主/无验收标准比例、AI 草稿一次匹配准确率。只根据系统数据，不评价员工，也不据此推测绩效。")

    def get_parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    def execute(self, **kwargs) -> str:
        try:
            user_id, team_id = _identity(self)
        except ValueError as exc:
            return _fail(str(exc))
        data = _call(self, "GET", "/responsibility/summary", rs.READ, "agent_responsibility_weekly", enrich=False, teamId=team_id)
        if isinstance(data, str):
            return data
        from models.init_db import SessionLocal
        db = SessionLocal()
        try:
            lookup = rs._names_sync(db, [r["userId"] for r in data.get("load", [])])
        finally:
            db.close()
        for row in data.get("load", []):
            row["name"] = lookup.get(row["userId"]) or f"用户 {row['userId']}"
        return _dump({"narrative": rs.narrative_for(data), "week": data["week"], "metrics": data["metrics"], "byStatus": data["byStatus"]})
