"""采购 Agent 工具（Phase 5，docs/enterprise-business-hub-plan.md 第4/7节）。

跟 `service/tools/oa_leave.py` 是同一种薄工具层：签名转发到企业业务中心的
`com.enterprisehub.procurement.ProcurementController`，业务规则（预算够不够、
状态机对不对）全在 Java 那边判断。采购比请假多一条硬约束：所有操作都要
`team_id`（没有部门就不知道该查哪个部门的预算），当前用户不在任何部门时
直接返回错误 JSON，不硬凑一个默认值。
"""
import json
import uuid

from service import enterprise_hub_client as hub
from service.tools.base import BaseTool, ToolRegistry


def _require_user_and_auth(ctx) -> tuple:
    """返回 (user_id, auth)，auth 是 `hub.resolve_caller_context()` 算出来的
    team_id/is_org_admin/is_team_admin——权限判断只在那一处算，这里不重复实现。"""
    if not ctx or not ctx.user_id:
        raise ValueError("缺少用户上下文，无法调用企业业务中心")
    user_id = ctx.user_id
    auth = hub.resolve_caller_context(user_id, ctx.agent_id)
    if auth["team_id"] is None and not auth["is_org_admin"]:
        raise ValueError("当前用户不属于任何部门，无法进行采购操作（采购按部门查库存和预算）")
    return user_id, auth


def _error_json(exc: hub.EnterpriseHubError) -> str:
    return json.dumps({"error": exc.detail, "status_code": exc.status_code}, ensure_ascii=False)


@ToolRegistry.register
class GetInventoryStatusTool(BaseTool):
    requires_context = True
    risk_level = "read"

    def get_name(self) -> str:
        return "get_inventory_status"

    def get_description(self) -> str:
        return "查询某个产品（按 SKU）的库存量和是否低于安全库存。用户问库存够不够、要不要补货时调用。"

    def get_parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {"sku": {"type": "string", "description": "产品编号"}},
            "required": ["sku"],
        }

    def execute(self, **kwargs) -> str:
        try:
            user_id, auth = _require_user_and_auth(self._ctx)
        except ValueError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)
        sku = kwargs.get("sku")
        try:
            result = hub.call(
                "GET", f"/procurement/products/{sku}", user_id, auth["team_id"],
                ["procurement.read"], "get_inventory_status",
                is_org_admin=auth["is_org_admin"], is_team_admin=auth["is_team_admin"],
            )
        except hub.EnterpriseHubError as exc:
            return _error_json(exc)
        return json.dumps(result, ensure_ascii=False)


@ToolRegistry.register
class GetDepartmentBudgetTool(BaseTool):
    requires_context = True
    risk_level = "read"

    def get_name(self) -> str:
        return "get_department_budget"

    def get_description(self) -> str:
        return "查询当前用户所在部门某年度的采购预算余额。"

    def get_parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {"year": {"type": "integer", "description": "查询年度，不填默认当前年"}},
        }

    def execute(self, **kwargs) -> str:
        try:
            user_id, auth = _require_user_and_auth(self._ctx)
        except ValueError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)
        year = kwargs.get("year")
        path = "/procurement/budget" + (f"?year={int(year)}" if year else "")
        try:
            result = hub.call(
                "GET", path, user_id, auth["team_id"], ["procurement.read"], "get_department_budget",
                is_org_admin=auth["is_org_admin"], is_team_admin=auth["is_team_admin"],
            )
        except hub.EnterpriseHubError as exc:
            return _error_json(exc)
        return json.dumps(result, ensure_ascii=False)


@ToolRegistry.register
class CreatePurchaseDraftTool(BaseTool):
    requires_context = True
    risk_level = "write"  # 建的是草稿，还没提交，用户确认前可以反复改

    def get_name(self) -> str:
        return "create_purchase_draft"

    def get_description(self) -> str:
        return "创建一条采购申请草稿（还没提交），可以包含多个产品明细。"

    def get_parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "lines": {
                    "type": "array",
                    "description": "采购明细列表",
                    "items": {
                        "type": "object",
                        "properties": {
                            "sku": {"type": "string", "description": "产品编号"},
                            "quantity": {"type": "integer", "description": "采购数量"},
                        },
                        "required": ["sku", "quantity"],
                    },
                },
            },
            "required": ["lines"],
        }

    def execute(self, **kwargs) -> str:
        try:
            user_id, auth = _require_user_and_auth(self._ctx)
        except ValueError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)
        lines = kwargs.get("lines") or []
        try:
            result = hub.call(
                "POST", "/procurement/requests", user_id, auth["team_id"], ["procurement.write"],
                "create_purchase_draft",
                is_org_admin=auth["is_org_admin"], is_team_admin=auth["is_team_admin"],
                json_body={"lines": lines}, idempotency_key=str(uuid.uuid4()),
            )
        except hub.EnterpriseHubError as exc:
            return _error_json(exc)
        return json.dumps(result, ensure_ascii=False)


@ToolRegistry.register
class SubmitPurchaseRequestTool(BaseTool):
    requires_context = True
    risk_level = "high_risk"  # 提交后进入审批流程，不能由模型自动触发

    def get_name(self) -> str:
        return "submit_purchase_request"

    def get_description(self) -> str:
        return "提交一条草稿状态的采购申请，提交后进入待审批状态。"

    def get_parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {"request_id": {"type": "integer", "description": "采购申请 id"}},
            "required": ["request_id"],
        }

    def execute(self, **kwargs) -> str:
        try:
            user_id, auth = _require_user_and_auth(self._ctx)
        except ValueError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)
        request_id = kwargs.get("request_id")
        try:
            result = hub.call(
                "POST", f"/procurement/requests/{int(request_id)}/submit", user_id, auth["team_id"],
                ["procurement.write"], "submit_purchase_request",
                is_org_admin=auth["is_org_admin"], is_team_admin=auth["is_team_admin"],
                idempotency_key=str(uuid.uuid4()),
            )
        except hub.EnterpriseHubError as exc:
            return _error_json(exc)
        return json.dumps(result, ensure_ascii=False)


@ToolRegistry.register
class ApprovePurchaseRequestTool(BaseTool):
    requires_context = True
    risk_level = "high_risk"  # 批准会真的花预算，不能由模型自动触发

    def get_name(self) -> str:
        return "approve_purchase_request"

    def get_description(self) -> str:
        return "批准一条已提交的采购申请（只有部门负责人视角的对话该调用这个工具）。"

    def get_parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "request_id": {"type": "integer", "description": "采购申请 id"},
                "note": {"type": "string", "description": "审批意见，可选"},
            },
            "required": ["request_id"],
        }

    def execute(self, **kwargs) -> str:
        try:
            user_id, auth = _require_user_and_auth(self._ctx)
        except ValueError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)
        if not (auth["is_org_admin"] or auth["is_team_admin"]):
            return json.dumps({"error": "当前用户不是部门负责人或企业管理员，无权审批采购申请"}, ensure_ascii=False)
        request_id = kwargs.get("request_id")
        try:
            result = hub.call(
                "POST", f"/procurement/requests/{int(request_id)}/approve", user_id, auth["team_id"],
                ["procurement.approve"], "approve_purchase_request",
                is_org_admin=auth["is_org_admin"], is_team_admin=auth["is_team_admin"],
                json_body={"note": kwargs.get("note")}, idempotency_key=str(uuid.uuid4()),
            )
        except hub.EnterpriseHubError as exc:
            return _error_json(exc)
        return json.dumps(result, ensure_ascii=False)


@ToolRegistry.register
class RejectPurchaseRequestTool(BaseTool):
    requires_context = True
    risk_level = "high_risk"  # 拒绝同样是真实的采购决定，不能由模型自动触发

    def get_name(self) -> str:
        return "reject_purchase_request"

    def get_description(self) -> str:
        return "拒绝一条已提交的采购申请（只有部门负责人视角的对话该调用这个工具）。"

    def get_parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "request_id": {"type": "integer", "description": "采购申请 id"},
                "note": {"type": "string", "description": "拒绝理由，可选"},
            },
            "required": ["request_id"],
        }

    def execute(self, **kwargs) -> str:
        try:
            user_id, auth = _require_user_and_auth(self._ctx)
        except ValueError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)
        if not (auth["is_org_admin"] or auth["is_team_admin"]):
            return json.dumps({"error": "当前用户不是部门负责人或企业管理员，无权处理采购申请"}, ensure_ascii=False)
        request_id = kwargs.get("request_id")
        try:
            result = hub.call(
                "POST", f"/procurement/requests/{int(request_id)}/reject", user_id, auth["team_id"],
                ["procurement.approve"], "reject_purchase_request",
                is_org_admin=auth["is_org_admin"], is_team_admin=auth["is_team_admin"],
                json_body={"note": kwargs.get("note")}, idempotency_key=str(uuid.uuid4()),
            )
        except hub.EnterpriseHubError as exc:
            return _error_json(exc)
        return json.dumps(result, ensure_ascii=False)


@ToolRegistry.register
class GetPurchaseStatusTool(BaseTool):
    requires_context = True
    risk_level = "read"

    def get_name(self) -> str:
        return "get_purchase_status"

    def get_description(self) -> str:
        return "查询一条采购申请当前的状态（草稿/已提交/已批准/已拒绝），批准后还会带出生成的采购单信息。"

    def get_parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {"request_id": {"type": "integer", "description": "采购申请 id"}},
            "required": ["request_id"],
        }

    def execute(self, **kwargs) -> str:
        try:
            user_id, auth = _require_user_and_auth(self._ctx)
        except ValueError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)
        request_id = kwargs.get("request_id")
        try:
            result = hub.call(
                "GET", f"/procurement/requests/{int(request_id)}", user_id, auth["team_id"],
                ["procurement.read"], "get_purchase_status",
                is_org_admin=auth["is_org_admin"], is_team_admin=auth["is_team_admin"],
            )
        except hub.EnterpriseHubError as exc:
            return _error_json(exc)
        return json.dumps(result, ensure_ascii=False)
