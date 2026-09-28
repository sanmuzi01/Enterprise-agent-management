"""OA 请假闭环的 Agent 工具（Phase 5，docs/enterprise-business-hub-plan.md 第4/7节）。

每个工具对应企业业务中心 `enterprise-business-hub` 的一个 REST 接口
（`com.enterprisehub.oa.LeaveController`）。这些工具只管"签名转发 + 把结果/错误
翻成给用户看的话"，业务规则（余额够不够、状态机对不对）全在 Java 那边判断——
跟平台里其它需要外部服务的工具（RAG 检索、网页抓取）一样的边界原则：工具层薄，
业务逻辑不重复实现两遍。
"""
import json
import uuid

from service import enterprise_hub_client as hub
from service.tools.base import BaseTool, ToolRegistry


def _current_user_id(ctx) -> int:
    if not ctx or not ctx.user_id:
        raise ValueError("缺少用户上下文，无法调用企业业务中心")
    return ctx.user_id


def _error_json(exc: hub.EnterpriseHubError) -> str:
    return json.dumps({"error": exc.detail, "status_code": exc.status_code}, ensure_ascii=False)


@ToolRegistry.register
class GetLeaveBalanceTool(BaseTool):
    requires_context = True

    def get_name(self) -> str:
        return "get_leave_balance"

    def get_description(self) -> str:
        return "查询当前用户某年度的请假余额（年假/病假/事假剩余天数）。用户问“我还有多少年假”时调用。"

    def get_parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "year": {"type": "integer", "description": "查询年度，不填默认当前年"},
            },
        }

    def execute(self, **kwargs) -> str:
        user_id = _current_user_id(self._ctx)
        auth = hub.resolve_caller_context(user_id, self._ctx.agent_id)
        year = kwargs.get("year")
        path = "/oa/leave/balance" + (f"?year={int(year)}" if year else "")
        try:
            result = hub.call(
                "GET", path, user_id, auth["team_id"], ["oa.leave.read"], "get_leave_balance",
                is_org_admin=auth["is_org_admin"], is_team_admin=auth["is_team_admin"],
            )
        except hub.EnterpriseHubError as exc:
            return _error_json(exc)
        return json.dumps(result, ensure_ascii=False)


@ToolRegistry.register
class CreateLeaveDraftTool(BaseTool):
    requires_context = True

    def get_name(self) -> str:
        return "create_leave_draft"

    def get_description(self) -> str:
        return "创建一条请假草稿（还没提交，用户确认信息无误后要再调 submit_leave_request 才会真正生效）。"

    def get_parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "leave_type_code": {"type": "string", "description": "请假类型代码：annual(年假)/sick(病假)/personal(事假)"},
                "start_date": {"type": "string", "description": "开始日期，格式 YYYY-MM-DD"},
                "end_date": {"type": "string", "description": "结束日期，格式 YYYY-MM-DD"},
                "reason": {"type": "string", "description": "请假原因，可选"},
            },
            "required": ["leave_type_code", "start_date", "end_date"],
        }

    def execute(self, **kwargs) -> str:
        user_id = _current_user_id(self._ctx)
        auth = hub.resolve_caller_context(user_id, self._ctx.agent_id)
        body = {
            "leaveTypeCode": kwargs.get("leave_type_code"),
            "startDate": kwargs.get("start_date"),
            "endDate": kwargs.get("end_date"),
            "reason": kwargs.get("reason"),
        }
        try:
            result = hub.call(
                "POST", "/oa/leave/requests", user_id, auth["team_id"], ["oa.leave.write"], "create_leave_draft",
                is_org_admin=auth["is_org_admin"], is_team_admin=auth["is_team_admin"],
                json_body=body, idempotency_key=str(uuid.uuid4()),
            )
        except hub.EnterpriseHubError as exc:
            return _error_json(exc)
        return json.dumps(result, ensure_ascii=False)


@ToolRegistry.register
class SubmitLeaveRequestTool(BaseTool):
    requires_context = True

    def get_name(self) -> str:
        return "submit_leave_request"

    def get_description(self) -> str:
        return "提交一条草稿状态的请假单，提交后进入待审批状态，等部门负责人处理。"

    def get_parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "request_id": {"type": "integer", "description": "请假单 id（create_leave_draft 返回的 id）"},
            },
            "required": ["request_id"],
        }

    def execute(self, **kwargs) -> str:
        user_id = _current_user_id(self._ctx)
        auth = hub.resolve_caller_context(user_id, self._ctx.agent_id)
        request_id = kwargs.get("request_id")
        try:
            result = hub.call(
                "POST", f"/oa/leave/requests/{int(request_id)}/submit", user_id, auth["team_id"],
                ["oa.leave.write"], "submit_leave_request",
                is_org_admin=auth["is_org_admin"], is_team_admin=auth["is_team_admin"],
                idempotency_key=str(uuid.uuid4()),
            )
        except hub.EnterpriseHubError as exc:
            return _error_json(exc)
        return json.dumps(result, ensure_ascii=False)


@ToolRegistry.register
class ApproveLeaveRequestTool(BaseTool):
    requires_context = True

    def get_name(self) -> str:
        return "approve_leave_request"

    def get_description(self) -> str:
        return "批准一条已提交的请假单（只有部门负责人视角的对话该调用这个工具，普通申请人不该用）。"

    def get_parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "request_id": {"type": "integer", "description": "请假单 id"},
                "note": {"type": "string", "description": "审批意见，可选"},
            },
            "required": ["request_id"],
        }

    def execute(self, **kwargs) -> str:
        user_id = _current_user_id(self._ctx)
        auth = hub.resolve_caller_context(user_id, self._ctx.agent_id)
        if not (auth["is_org_admin"] or auth["is_team_admin"]):
            return json.dumps({"error": "当前用户不是部门负责人或企业管理员，无权审批请假单"}, ensure_ascii=False)
        request_id = kwargs.get("request_id")
        try:
            result = hub.call(
                "POST", f"/oa/leave/requests/{int(request_id)}/approve", user_id, auth["team_id"],
                ["oa.leave.approve"], "approve_leave_request",
                is_org_admin=auth["is_org_admin"], is_team_admin=auth["is_team_admin"],
                json_body={"note": kwargs.get("note")}, idempotency_key=str(uuid.uuid4()),
            )
        except hub.EnterpriseHubError as exc:
            return _error_json(exc)
        return json.dumps(result, ensure_ascii=False)


@ToolRegistry.register
class RejectLeaveRequestTool(BaseTool):
    requires_context = True

    def get_name(self) -> str:
        return "reject_leave_request"

    def get_description(self) -> str:
        return "拒绝一条已提交的请假单（只有部门负责人视角的对话该调用这个工具）。"

    def get_parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "request_id": {"type": "integer", "description": "请假单 id"},
                "note": {"type": "string", "description": "拒绝理由，可选"},
            },
            "required": ["request_id"],
        }

    def execute(self, **kwargs) -> str:
        user_id = _current_user_id(self._ctx)
        auth = hub.resolve_caller_context(user_id, self._ctx.agent_id)
        if not (auth["is_org_admin"] or auth["is_team_admin"]):
            return json.dumps({"error": "当前用户不是部门负责人或企业管理员，无权处理请假单"}, ensure_ascii=False)
        request_id = kwargs.get("request_id")
        try:
            result = hub.call(
                "POST", f"/oa/leave/requests/{int(request_id)}/reject", user_id, auth["team_id"],
                ["oa.leave.approve"], "reject_leave_request",
                is_org_admin=auth["is_org_admin"], is_team_admin=auth["is_team_admin"],
                json_body={"note": kwargs.get("note")}, idempotency_key=str(uuid.uuid4()),
            )
        except hub.EnterpriseHubError as exc:
            return _error_json(exc)
        return json.dumps(result, ensure_ascii=False)


@ToolRegistry.register
class GetLeaveStatusTool(BaseTool):
    requires_context = True

    def get_name(self) -> str:
        return "get_leave_status"

    def get_description(self) -> str:
        return "查询一条请假单当前的状态（草稿/已提交/已批准/已拒绝）。"

    def get_parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "request_id": {"type": "integer", "description": "请假单 id"},
            },
            "required": ["request_id"],
        }

    def execute(self, **kwargs) -> str:
        user_id = _current_user_id(self._ctx)
        auth = hub.resolve_caller_context(user_id, self._ctx.agent_id)
        request_id = kwargs.get("request_id")
        try:
            result = hub.call(
                "GET", f"/oa/leave/requests/{int(request_id)}", user_id, auth["team_id"],
                ["oa.leave.read"], "get_leave_status",
                is_org_admin=auth["is_org_admin"], is_team_admin=auth["is_team_admin"],
            )
        except hub.EnterpriseHubError as exc:
            return _error_json(exc)
        return json.dumps(result, ensure_ascii=False)
