"""人事入转调离 Agent 工具：查我的办理任务、查事项详情与检查结果、发起前预检、人事汇总。

查询类工具只读（预检不创建任何数据）。发起事项、完成分给我的办理任务是 high_risk：模型只能提出，用户在对话里点确认后才执行；
批准、办结、落实系统变更仍只在工作台里由人操作。身份计算与工作台完全一致（service/hr_service.actor_for_sync）。
"""
import json

from service import enterprise_hub_client as hub
from service import hr_service as hr
from service.exceptions import AppError
from service.tools.base import BaseTool, ToolRegistry


def _run(tool, method, path, operation, body=None, **params):
    ctx = tool._ctx
    if not ctx or not ctx.user_id:
        return json.dumps({"error": "缺少用户上下文，无法调用企业业务中心"}, ensure_ascii=False)
    auth = hub.resolve_caller_context(ctx.user_id, ctx.agent_id)
    if auth["team_id"] is None:
        return json.dumps({"error": "当前用户不属于任何部门，无法查询人事事项"}, ensure_ascii=False)
    try:
        actor = hr.actor_for_sync(ctx.user_id, auth["team_id"])
        result = hub.call(method, hr.scoped(path, actor, **params), ctx.user_id, auth["team_id"], hr.READ, operation,
                          json_body=body)
    except AppError as exc:
        return json.dumps({"error": exc.message}, ensure_ascii=False)
    except hub.EnterpriseHubError as exc:
        return json.dumps({"error": exc.detail, "status_code": exc.status_code}, ensure_ascii=False)
    return json.dumps(result, ensure_ascii=False)


@ToolRegistry.register
class GetMyHrTasksTool(BaseTool):
    requires_context = True
    risk_level = "read"

    def get_name(self) -> str:
        return "get_my_hr_tasks"

    def get_description(self) -> str:
        return "查看分给我的入职/转正/调岗/离职办理任务（按期限排序，标出已逾期的）。"

    def get_parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    def execute(self, **kwargs) -> str:
        return _run(self, "GET", "/hr/cases/my-tasks", "my_hr_tasks")


@ToolRegistry.register
class GetHrCaseTool(BaseTool):
    requires_context = True
    risk_level = "read"

    def get_name(self) -> str:
        return "get_hr_case"

    def get_description(self) -> str:
        return "查看一个人事事项的详情：状态、规则检查结果（阻断/需核对/提示）和各方办理清单进度。"

    def get_parameters(self) -> dict:
        return {"type": "object", "properties": {"case_id": {"type": "integer", "description": "事项 id"}}, "required": ["case_id"]}

    def execute(self, **kwargs) -> str:
        return _run(self, "GET", f"/hr/cases/{int(kwargs['case_id'])}", "get_hr_case")


@ToolRegistry.register
class ListHrCasesTool(BaseTool):
    requires_context = True
    risk_level = "read"

    def get_name(self) -> str:
        return "list_hr_cases"

    def get_description(self) -> str:
        return ("列出人事事项。view=hr 全部（仅人事部门）、approval 等我批准的、mine 与我本人有关的；"
                "可按状态 PENDING_APPROVAL/IN_PROGRESS/COMPLETED 和类型 ONBOARDING/PROBATION/TRANSFER/OFFBOARDING 筛选。")

    def get_parameters(self) -> dict:
        return {"type": "object", "properties": {
            "view": {"type": "string", "description": "hr / approval / mine，默认 mine"},
            "status": {"type": "string"}, "type": {"type": "string"}}}

    def execute(self, **kwargs) -> str:
        return _run(self, "GET", "/hr/cases", "list_hr_cases", view=kwargs.get("view") or "mine",
                    status=kwargs.get("status"), type=kwargs.get("type"), limit=50)


@ToolRegistry.register
class PrecheckHrCaseTool(BaseTool):
    requires_context = True
    risk_level = "read"   # 只跑规则检查，不创建事项

    def get_name(self) -> str:
        return "precheck_hr_case"

    def get_description(self) -> str:
        return ("人事部门专用：发起入职/转正/调岗/离职前先检查——是否有重复或冲突的事项、名下设备、未结报销、待批请假、"
                "未休年假、是否部门负责人等。只检查，不创建；发起要在工作台里由人事人员确认。")

    def get_parameters(self) -> dict:
        return {"type": "object", "properties": {
            "case_type": {"type": "string", "description": "ONBOARDING/PROBATION/TRANSFER/OFFBOARDING"},
            "employee_user_id": {"type": "integer"}, "employee_team_id": {"type": "integer", "description": "员工所在部门"},
            "target_team_id": {"type": "integer", "description": "调岗目标部门"},
            "effective_date": {"type": "string", "description": "YYYY-MM-DD"}},
            "required": ["case_type", "employee_user_id", "employee_team_id", "effective_date"]}

    def execute(self, **kwargs) -> str:
        from models.enterprise_dao import is_team_admin_of_team
        from models.init_db import SessionLocal
        db = SessionLocal()
        try:
            is_head = is_team_admin_of_team(db, int(kwargs["employee_user_id"]), int(kwargs["employee_team_id"]))
        finally:
            db.close()
        body = {"caseType": kwargs.get("case_type"), "employeeUserId": kwargs.get("employee_user_id"),
                "teamId": kwargs.get("employee_team_id"), "targetTeamId": kwargs.get("target_team_id"),
                "effectiveDate": kwargs.get("effective_date"), "employeeIsHead": is_head}
        return _run(self, "POST", "/hr/cases/precheck", "precheck_hr_case", body)


@ToolRegistry.register
class GetHrSummaryTool(BaseTool):
    requires_context = True
    risk_level = "read"

    def get_name(self) -> str:
        return "get_hr_summary"

    def get_description(self) -> str:
        return "人事部门专用：入转调离汇总——各类事项进行中/已办结数量、逾期的办理任务、平均办理天数、待落实的系统变更。"

    def get_parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    def execute(self, **kwargs) -> str:
        output = _run(self, "GET", "/hr/cases/summary", "hr_case_summary")
        data = json.loads(output)
        if "error" not in data:
            data["narrative"] = hr.narrative_for(data)
        return json.dumps(data, ensure_ascii=False)


# ---------------------------------------------------------------- 可执行动作（都要用户在对话里点确认才真正执行）
# 发起入转调离（只给人事部门成员）、把分给我的办理任务标记为完成。都是 high_risk：模型只能提出，
# 确认卡片写清员工、类型、日期 / 任务，用户点“确认执行”后以本人身份调用业务服务，校验规则和工作台完全一致。
# 批准、办结、落实系统变更（改部门、停账号）仍只在工作台里由人操作。

def _write(tool, path, operation, body):
    import uuid
    ctx = tool._ctx
    if not ctx or not ctx.user_id:
        return None, None, json.dumps({"error": "缺少用户上下文，无法调用企业业务中心"}, ensure_ascii=False)
    auth = hub.resolve_caller_context(ctx.user_id, ctx.agent_id)
    if auth["team_id"] is None:
        return None, None, json.dumps({"error": "当前用户不属于任何部门，无法办理人事事项"}, ensure_ascii=False)
    try:
        actor = hr.actor_for_sync(ctx.user_id, auth["team_id"])
        payload = body(actor) if callable(body) else body
        result = hub.call("POST", hr.scoped(path, actor), ctx.user_id, auth["team_id"], hr.WRITE, operation,
                          json_body=payload, idempotency_key=str(uuid.uuid4()))
    except AppError as exc:
        return None, None, json.dumps({"error": exc.message}, ensure_ascii=False)
    except hub.EnterpriseHubError as exc:
        return None, None, json.dumps({"error": exc.detail, "status_code": exc.status_code}, ensure_ascii=False)
    return result, actor, None


def _validated_case_body(actor, kwargs):
    """同 hr_service._validated_body：员工必须是该部门有效成员，调岗目标部门必须在本企业；是否负责人由服务端算。"""
    from sqlalchemy import text
    from models.enterprise_dao import is_team_admin_of_team
    from models.init_db import SessionLocal
    from service.exceptions import InvalidInput, PermissionDenied
    if "HR" not in actor["roles"]:
        raise PermissionDenied("只有人事部门成员能发起人事事项")
    case_type = str(kwargs.get("case_type") or "").upper()
    if case_type not in ("ONBOARDING", "PROBATION", "TRANSFER", "OFFBOARDING"):
        raise InvalidInput("事项类型只能是入职 / 转正 / 调岗 / 离职")
    employee, team = int(kwargs["employee_user_id"]), int(kwargs["employee_team_id"])
    if team not in actor["scope"]:
        raise PermissionDenied("部门不在你所在企业内")
    db = SessionLocal()
    try:
        member = db.execute(text(
            "SELECT 1 FROM team_members tm JOIN organization_members om ON om.user_id = tm.user_id "
            "AND om.organization_id = :o AND om.status = 'active' WHERE tm.team_id = :t AND tm.user_id = :u AND tm.status = 'active'"),
            {"o": actor["organization_id"], "t": team, "u": employee}).first()
        if member is None:
            raise InvalidInput("该员工不是这个部门的有效成员；入职请先由管理员把员工加入部门")
        target = kwargs.get("target_team_id")
        if case_type == "TRANSFER" and (target is None or int(target) not in actor["scope"]):
            raise InvalidInput("调岗需要选择本企业内有效的目标部门")
        is_head = is_team_admin_of_team(db, employee, team)
    finally:
        db.close()
    return {"caseType": case_type, "employeeUserId": employee, "teamId": team,
            "targetTeamId": int(target) if case_type == "TRANSFER" else None,
            "position": kwargs.get("position"), "effectiveDate": kwargs.get("effective_date"),
            "reason": kwargs.get("reason"), "employeeIsHead": is_head}


@ToolRegistry.register
class CreateHrCaseTool(BaseTool):
    requires_context = True
    risk_level = "high_risk"

    def get_name(self) -> str:
        return "create_hr_case"

    def get_description(self) -> str:
        return ("人事部门专用：发起一个入职 / 转正 / 调岗 / 离职事项（发起后等部门负责人批准，批准后自动生成各方办理清单）。"
                "必须先用 precheck_hr_case 预检，把阻断项告诉用户；复述员工、类型、生效日期再提出。")

    def get_parameters(self) -> dict:
        return {"type": "object", "properties": {
            "case_type": {"type": "string", "description": "ONBOARDING/PROBATION/TRANSFER/OFFBOARDING"},
            "employee_user_id": {"type": "integer"}, "employee_team_id": {"type": "integer", "description": "员工所在部门"},
            "target_team_id": {"type": "integer", "description": "调岗目标部门"},
            "effective_date": {"type": "string", "description": "YYYY-MM-DD"},
            "position": {"type": "string", "description": "岗位，可选"}, "reason": {"type": "string", "description": "原因，可选"}},
            "required": ["case_type", "employee_user_id", "employee_team_id", "effective_date"]}

    def execute(self, **kwargs) -> str:
        result, _, error = _write(self, "/hr/cases", "create_hr_case", lambda actor: _validated_case_body(actor, kwargs))
        return error or json.dumps(result, ensure_ascii=False)


@ToolRegistry.register
class CompleteHrTaskTool(BaseTool):
    requires_context = True
    risk_level = "high_risk"

    def get_name(self) -> str:
        return "complete_hr_task"

    def get_description(self) -> str:
        return "把分给我的一项入转调离办理任务标记为已完成（比如“开通邮箱”“归还电脑”），可附备注。先用 get_my_hr_tasks 找到事项号和任务号。"

    def get_parameters(self) -> dict:
        return {"type": "object", "properties": {
            "case_id": {"type": "integer", "description": "事项号"}, "task_id": {"type": "integer", "description": "任务号"},
            "note": {"type": "string", "description": "备注，可选"}}, "required": ["case_id", "task_id"]}

    def execute(self, **kwargs) -> str:
        path = f"/hr/cases/{int(kwargs['case_id'])}/tasks/{int(kwargs['task_id'])}/done"
        result, _, error = _write(self, path, "done_hr_task", {"note": kwargs.get("note")})
        return error or json.dumps(result, ensure_ascii=False)
