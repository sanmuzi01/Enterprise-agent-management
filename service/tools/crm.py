"""销售 Agent 工具（Phase 5，docs/enterprise-business-hub-plan.md 第4/7节）。

跟 oa_leave.py/procurement.py 是同一种薄工具层。CRM 跟采购一样要求 `team_id`
（客户按部门隔离），比 OA/采购简单的地方是没有审批环节，只有草稿->确认两步。

`team_id` 通过 `hub.resolve_caller_context()` 推导（同 oa_leave.py/procurement.py），
不再自己另写一份——之前 CRM 单独手写的 `_resolve_team_id` 只会取"在职的第一个部门"，
一人管两个部门时通过第二个部门的销售 Agent 操作会查错部门数据，这是 OA/采购已经
修过的同一个问题，CRM 当时漏了（docs/enterprise-rbac-plan.md 第16节）。
"""
import json
import uuid

from service import enterprise_hub_client as hub
from service.tools.base import BaseTool, ToolRegistry


def _require_user_and_auth(ctx) -> tuple:
    if not ctx or not ctx.user_id:
        raise ValueError("缺少用户上下文，无法调用企业业务中心")
    user_id = ctx.user_id
    auth = hub.resolve_caller_context(user_id, ctx.agent_id)
    if auth["team_id"] is None and not auth["is_org_admin"]:
        raise ValueError("当前用户不属于任何部门，无法进行 CRM 操作（客户按部门隔离）")
    from service.department_access import check_team_module
    check_team_module(auth["team_id"], "crm")
    return user_id, auth


def _error_json(exc: hub.EnterpriseHubError) -> str:
    return json.dumps({"error": exc.detail, "status_code": exc.status_code}, ensure_ascii=False)


@ToolRegistry.register
class ListTeamCustomersTool(BaseTool):
    requires_context = True
    risk_level = "read"

    def get_name(self) -> str:
        return "list_team_customers"

    def get_description(self) -> str:
        return "查询当前用户所在部门的客户列表。"

    def get_parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    def execute(self, **kwargs) -> str:
        try:
            user_id, auth = _require_user_and_auth(self._ctx)
        except ValueError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)
        try:
            result = hub.call(
                "GET", "/crm/customers", user_id, auth["team_id"],
                ["crm.read"], "list_team_customers",
                is_org_admin=auth["is_org_admin"], is_team_admin=auth["is_team_admin"],
            )
        except hub.EnterpriseHubError as exc:
            return _error_json(exc)
        return json.dumps(result, ensure_ascii=False)


@ToolRegistry.register
class GetCustomerSummaryTool(BaseTool):
    requires_context = True
    risk_level = "read"

    def get_name(self) -> str:
        return "get_customer_summary"

    def get_description(self) -> str:
        return "查询客户摘要：基本信息、联系人、最近跟进记录、商机列表，用于生成客户摘要或准备拜访。"

    def get_parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {"customer_id": {"type": "integer", "description": "客户 id"}},
            "required": ["customer_id"],
        }

    def execute(self, **kwargs) -> str:
        try:
            user_id, auth = _require_user_and_auth(self._ctx)
        except ValueError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)
        customer_id = kwargs.get("customer_id")
        try:
            result = hub.call(
                "GET", f"/crm/customers/{int(customer_id)}", user_id, auth["team_id"],
                ["crm.read"], "get_customer_summary",
                is_org_admin=auth["is_org_admin"], is_team_admin=auth["is_team_admin"],
            )
        except hub.EnterpriseHubError as exc:
            return _error_json(exc)
        return json.dumps(result, ensure_ascii=False)


@ToolRegistry.register
class CreateFollowupDraftTool(BaseTool):
    requires_context = True
    risk_level = "write"  # 建的是草稿，还没确认，用户确认前可以反复改

    def get_name(self) -> str:
        return "create_followup_draft"

    def get_description(self) -> str:
        return "为某个客户创建一条跟进草稿（还没确认），用户确认无误后要调 submit_customer_followup。"

    def get_parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "customer_id": {"type": "integer", "description": "客户 id"},
                "content": {"type": "string", "description": "跟进内容"},
            },
            "required": ["customer_id", "content"],
        }

    def execute(self, **kwargs) -> str:
        try:
            user_id, auth = _require_user_and_auth(self._ctx)
        except ValueError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)
        customer_id = kwargs.get("customer_id")
        try:
            result = hub.call(
                "POST", f"/crm/customers/{int(customer_id)}/followups", user_id, auth["team_id"],
                ["crm.write"], "create_followup_draft",
                is_org_admin=auth["is_org_admin"], is_team_admin=auth["is_team_admin"],
                json_body={"content": kwargs.get("content")}, idempotency_key=str(uuid.uuid4()),
            )
        except hub.EnterpriseHubError as exc:
            return _error_json(exc)
        return json.dumps(result, ensure_ascii=False)


@ToolRegistry.register
class SubmitCustomerFollowupTool(BaseTool):
    requires_context = True
    risk_level = "high_risk"  # 确认后正式落库，不能由模型自动触发

    def get_name(self) -> str:
        return "submit_customer_followup"

    def get_description(self) -> str:
        return "确认一条跟进草稿，确认后正式保存为跟进记录。"

    def get_parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {"followup_id": {"type": "integer", "description": "跟进记录 id"}},
            "required": ["followup_id"],
        }

    def execute(self, **kwargs) -> str:
        try:
            user_id, auth = _require_user_and_auth(self._ctx)
        except ValueError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)
        followup_id = kwargs.get("followup_id")
        try:
            result = hub.call(
                "POST", f"/crm/followups/{int(followup_id)}/confirm", user_id, auth["team_id"],
                ["crm.write"], "submit_customer_followup",
                is_org_admin=auth["is_org_admin"], is_team_admin=auth["is_team_admin"],
                idempotency_key=str(uuid.uuid4()),
            )
        except hub.EnterpriseHubError as exc:
            return _error_json(exc)
        return json.dumps(result, ensure_ascii=False)


@ToolRegistry.register
class CreateOrUpdateOpportunityTool(BaseTool):
    requires_context = True
    # 跟请假/采购不一样，这个工具没有"草稿→提交"两步，一次调用就直接落库
    # （新建商机或改阶段/金额），效果等同于别的域里的"submit"，同样不能由模型自动触发。
    risk_level = "high_risk"

    def get_name(self) -> str:
        return "create_or_update_opportunity"

    def get_description(self) -> str:
        return "为客户创建新商机，或更新已有商机的阶段和金额（传 opportunity_id 就是更新，不传就新建）。"

    def get_parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "customer_id": {"type": "integer", "description": "客户 id"},
                "opportunity_id": {"type": "integer", "description": "要更新的商机 id，新建则不传"},
                "stage": {"type": "string",
                          "description": "商机阶段：LEAD/QUALIFIED/PROPOSAL/NEGOTIATION/WON/LOST"},
                "amount": {"type": "number", "description": "商机金额"},
            },
            "required": ["customer_id", "stage", "amount"],
        }

    def execute(self, **kwargs) -> str:
        try:
            user_id, auth = _require_user_and_auth(self._ctx)
        except ValueError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)
        customer_id = kwargs.get("customer_id")
        body = {"stage": kwargs.get("stage"), "amount": kwargs.get("amount")}
        if kwargs.get("opportunity_id") is not None:
            body["opportunityId"] = kwargs.get("opportunity_id")
        try:
            result = hub.call(
                "POST", f"/crm/customers/{int(customer_id)}/opportunities", user_id, auth["team_id"],
                ["crm.write"], "create_or_update_opportunity",
                is_org_admin=auth["is_org_admin"], is_team_admin=auth["is_team_admin"],
                json_body=body, idempotency_key=str(uuid.uuid4()),
            )
        except hub.EnterpriseHubError as exc:
            return _error_json(exc)
        return json.dumps(result, ensure_ascii=False)


@ToolRegistry.register
class GetOpportunitiesTool(BaseTool):
    requires_context = True
    risk_level = "read"

    def get_name(self) -> str:
        return "get_opportunities"

    def get_description(self) -> str:
        return "查询某个客户名下的所有商机。"

    def get_parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {"customer_id": {"type": "integer", "description": "客户 id"}},
            "required": ["customer_id"],
        }

    def execute(self, **kwargs) -> str:
        try:
            user_id, auth = _require_user_and_auth(self._ctx)
        except ValueError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)
        customer_id = kwargs.get("customer_id")
        try:
            result = hub.call(
                "GET", f"/crm/customers/{int(customer_id)}/opportunities", user_id, auth["team_id"],
                ["crm.read"], "get_opportunities",
                is_org_admin=auth["is_org_admin"], is_team_admin=auth["is_team_admin"],
            )
        except hub.EnterpriseHubError as exc:
            return _error_json(exc)
        return json.dumps(result, ensure_ascii=False)
