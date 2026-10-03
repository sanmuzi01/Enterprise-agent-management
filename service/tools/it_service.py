"""IT 服务台 Agent 工具。

员工侧：查自助解决方案、提交工单、查我的工单、补充信息、查我名下的设备。
IT 台侧（仅 IT 部门）：看队列、工单详情、服务台汇总、设备台账——只读，方便 IT 人员快速了解情况。
刻意没有接单/解决/批准/改分类/发放设备的工具：这些都是需要人负责的决定，必须在工作台里由人操作。
"""
import json
import uuid
from urllib.parse import quote

from service import enterprise_hub_client as hub
from service import it_service as it
from service.department_access import check_team_module, department_staff_scope
from service.exceptions import AppError
from service.hub_gateway import scoped_path
from service.tools.base import BaseTool, ToolRegistry

CATEGORIES = "INCIDENT 故障（含忘记密码）/ ACCOUNT 账号申请 / PERMISSION 权限申请 / DEVICE 设备申请 / OTHER 咨询"


def _error_json(exc: hub.EnterpriseHubError) -> str:
    return json.dumps({"error": exc.detail, "status_code": exc.status_code}, ensure_ascii=False)


def _auth(ctx):
    if not ctx or not ctx.user_id:
        raise ValueError("缺少用户上下文，无法调用企业业务中心")
    auth = hub.resolve_caller_context(ctx.user_id, ctx.agent_id)
    if auth["team_id"] is None:
        raise ValueError("当前用户不属于任何部门，无法使用 IT 服务（工单按部门提交和审批）")
    check_team_module(auth["team_id"], "ticket")
    return ctx.user_id, auth


def _desk(ctx):
    user_id, auth = _auth(ctx)
    try:
        scope = department_staff_scope(user_id, auth["team_id"], "it", "IT 服务台")["scope"]
    except AppError as exc:
        raise ValueError(exc.message)
    return user_id, auth["team_id"], scope


def _employee_call(tool, method, path, scopes, operation, body=None, write=False):
    try:
        user_id, auth = _auth(tool._ctx)
    except ValueError as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)
    try:
        result = hub.call(method, path, user_id, auth["team_id"], scopes, operation,
                          is_org_admin=auth["is_org_admin"], is_team_admin=auth["is_team_admin"],
                          json_body=body, idempotency_key=str(uuid.uuid4()) if write else None)
    except hub.EnterpriseHubError as exc:
        return _error_json(exc)
    return json.dumps(result, ensure_ascii=False)


def _desk_call(tool, method, base, operation, **params):
    try:
        user_id, team_id, scope = _desk(tool._ctx)
    except ValueError as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)
    try:
        result = hub.call(method, scoped_path(base, scope, **params), user_id, team_id, it.DESK_READ, operation)
    except hub.EnterpriseHubError as exc:
        return _error_json(exc)
    return json.dumps(result, ensure_ascii=False)


@ToolRegistry.register
class SearchItSolutionsTool(BaseTool):
    requires_context = True
    risk_level = "read"

    def get_name(self) -> str:
        return "search_it_solutions"

    def get_description(self) -> str:
        return "根据问题描述查找 IT 自助解决方案，并给出规则判断的工单类型与优先级建议。提交工单前先用它，能自己解决的不必等人。"

    def get_parameters(self) -> dict:
        return {"type": "object", "properties": {"text": {"type": "string", "description": "问题描述"}}, "required": ["text"]}

    def execute(self, **kwargs) -> str:
        text = str(kwargs.get("text") or "")
        if len(text.strip()) < 2:
            return json.dumps({"error": "请先描述遇到的问题"}, ensure_ascii=False)
        try:
            user_id, auth = _auth(self._ctx)
            classification = hub.call("GET", f"/it/classify?text={quote(text)}", user_id, auth["team_id"], it.TICKET_READ, "classify_it_ticket")
            articles = hub.call("GET", f"/it/kb/suggest?text={quote(text)}", user_id, auth["team_id"], it.TICKET_READ, "suggest_it_solutions")
        except ValueError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)
        except hub.EnterpriseHubError as exc:
            return _error_json(exc)
        return json.dumps({"classification": classification, "articles": articles}, ensure_ascii=False)


@ToolRegistry.register
class CreateItTicketTool(BaseTool):
    requires_context = True
    risk_level = "write"   # 会真的创建工单（账号/权限/设备类还要部门负责人批准），提交前模型应复述并让用户确认

    def get_name(self) -> str:
        return "create_it_ticket"

    def get_description(self) -> str:
        return f"提交一张 IT 工单。类型：{CATEGORIES}。账号、权限、设备申请需要先由本部门负责人批准。"

    def get_parameters(self) -> dict:
        return {"type": "object", "properties": {
            "category": {"type": "string", "description": "工单类型：INCIDENT/ACCOUNT/PERMISSION/DEVICE/OTHER"},
            "priority": {"type": "string", "description": "优先级：LOW/NORMAL/HIGH/URGENT，默认 NORMAL"},
            "title": {"type": "string", "description": "一句话标题"},
            "description": {"type": "string", "description": "问题现象、位置、影响范围"}},
            "required": ["category", "title", "description"]}

    def execute(self, **kwargs) -> str:
        body = {"category": kwargs.get("category"), "priority": kwargs.get("priority") or "NORMAL",
                "title": kwargs.get("title"), "description": kwargs.get("description")}
        return _employee_call(self, "POST", "/it/tickets", it.TICKET_WRITE, "create_it_ticket", body, True)


@ToolRegistry.register
class GetMyItTicketsTool(BaseTool):
    requires_context = True
    risk_level = "read"

    def get_name(self) -> str:
        return "get_my_it_tickets"

    def get_description(self) -> str:
        return "查看我提交的全部 IT 工单及状态。"

    def get_parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    def execute(self, **kwargs) -> str:
        return _employee_call(self, "GET", "/it/tickets/mine", it.TICKET_READ, "get_my_it_tickets")


@ToolRegistry.register
class GetItTicketStatusTool(BaseTool):
    requires_context = True
    risk_level = "read"

    def get_name(self) -> str:
        return "get_it_ticket_status"

    def get_description(self) -> str:
        return "查看一张 IT 工单的详情、处理进度和 IT 的回复（我自己的工单，或我作为部门负责人能看到的本部门工单）。"

    def get_parameters(self) -> dict:
        return {"type": "object", "properties": {"ticket_id": {"type": "integer", "description": "工单 id"}}, "required": ["ticket_id"]}

    def execute(self, **kwargs) -> str:
        return _employee_call(self, "GET", f"/it/tickets/{int(kwargs['ticket_id'])}", it.TICKET_READ, "get_it_ticket")


@ToolRegistry.register
class AddItTicketCommentTool(BaseTool):
    requires_context = True
    risk_level = "write"

    def get_name(self) -> str:
        return "add_it_ticket_comment"

    def get_description(self) -> str:
        return "给我的 IT 工单补充信息（IT 在等待用户回复时，补充后会继续处理）。"

    def get_parameters(self) -> dict:
        return {"type": "object", "properties": {"ticket_id": {"type": "integer", "description": "工单 id"},
                                                 "body": {"type": "string", "description": "补充内容"}},
                "required": ["ticket_id", "body"]}

    def execute(self, **kwargs) -> str:
        return _employee_call(self, "POST", f"/it/tickets/{int(kwargs['ticket_id'])}/comments", it.TICKET_WRITE,
                              "comment_it_ticket", {"body": kwargs.get("body"), "internal": False}, True)


@ToolRegistry.register
class GetMyDevicesTool(BaseTool):
    requires_context = True
    risk_level = "read"

    def get_name(self) -> str:
        return "get_my_devices"

    def get_description(self) -> str:
        return "查看登记在我名下的 IT 设备（资产编号、型号、保修状态）。"

    def get_parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    def execute(self, **kwargs) -> str:
        return _employee_call(self, "GET", "/it/devices/mine", it.TICKET_READ, "get_my_devices")


@ToolRegistry.register
class ListItQueueTool(BaseTool):
    requires_context = True
    risk_level = "read"

    def get_name(self) -> str:
        return "list_it_queue"

    def get_description(self) -> str:
        return "IT 部门专用：查看工单队列（按 SLA 截止时间排序，最急的在前）。可按状态、处理人（me/unassigned）、是否已超时筛选。"

    def get_parameters(self) -> dict:
        return {"type": "object", "properties": {
            "status": {"type": "string", "description": "OPEN/IN_PROGRESS/WAITING_USER/RESOLVED，不填默认排除待审批"},
            "assignee": {"type": "string", "description": "me 我的 / unassigned 未指派"},
            "overdue": {"type": "boolean", "description": "只看已超时的"}}}

    def execute(self, **kwargs) -> str:
        return _desk_call(self, "GET", "/it/desk/tickets", "list_it_queue", status=kwargs.get("status"),
                          assignee=kwargs.get("assignee"), overdue="true" if kwargs.get("overdue") else None, limit=50)


@ToolRegistry.register
class GetItTicketDetailTool(BaseTool):
    requires_context = True
    risk_level = "read"

    def get_name(self) -> str:
        return "get_it_ticket_detail"

    def get_description(self) -> str:
        return "IT 部门专用：查看工单详情，含内部备注、规则的分类建议依据和 SLA 状态。"

    def get_parameters(self) -> dict:
        return {"type": "object", "properties": {"ticket_id": {"type": "integer", "description": "工单 id"}}, "required": ["ticket_id"]}

    def execute(self, **kwargs) -> str:
        return _desk_call(self, "GET", f"/it/desk/tickets/{int(kwargs['ticket_id'])}", "get_it_ticket_detail")


@ToolRegistry.register
class GetItDeskSummaryTool(BaseTool):
    requires_context = True
    risk_level = "read"

    def get_name(self) -> str:
        return "get_it_desk_summary"

    def get_description(self) -> str:
        return "IT 部门专用：服务台汇总——积压、超时、处理人负载，以及近期的首次响应、解决耗时、SLA 达成率。"

    def get_parameters(self) -> dict:
        return {"type": "object", "properties": {"days": {"type": "integer", "description": "统计最近多少天，默认 30"}}}

    def execute(self, **kwargs) -> str:
        output = _desk_call(self, "GET", "/it/desk/summary", "get_it_desk_summary", days=int(kwargs.get("days") or 30))
        try:
            data = json.loads(output)
        except ValueError:
            return output
        if "error" not in data:
            data["narrative"] = it.narrative_for(data)
        return json.dumps(data, ensure_ascii=False)


@ToolRegistry.register
class ListItDevicesTool(BaseTool):
    requires_context = True
    risk_level = "read"

    def get_name(self) -> str:
        return "list_it_devices"

    def get_description(self) -> str:
        return "IT 部门专用：查看设备台账，可按状态（IN_STOCK/ASSIGNED/REPAIR/RETIRED）筛选，含持有人和保修状态。"

    def get_parameters(self) -> dict:
        return {"type": "object", "properties": {"status": {"type": "string", "description": "设备状态"}}}

    def execute(self, **kwargs) -> str:
        return _desk_call(self, "GET", "/it/desk/devices", "list_it_devices", status=kwargs.get("status"))
