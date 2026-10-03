"""财务报销 Agent 工具（里程碑4）。

跟 oa_leave.py/procurement.py 是同一种薄工具层：签名转发到企业业务中心的
com.enterprisehub.finance.FinanceController，业务规则（预算够不够、状态机对不对）
全在 Java 那边判断。跟采购一样要求 team_id（报销按部门查预算），当前用户不在任何
部门时直接返回错误 JSON，不硬凑一个默认值。
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
        raise ValueError("当前用户不属于任何部门，无法进行报销操作（报销按部门查预算）")
    from service.department_access import check_team_module
    check_team_module(auth["team_id"], "finance")
    return user_id, auth


def _error_json(exc: hub.EnterpriseHubError) -> str:
    return json.dumps({"error": exc.detail, "status_code": exc.status_code}, ensure_ascii=False)


@ToolRegistry.register
class GetExpenseBudgetTool(BaseTool):
    requires_context = True
    risk_level = "read"

    def get_name(self) -> str:
        return "get_expense_budget"

    def get_description(self) -> str:
        return "查询当前用户所在部门某年度的报销预算余额。"

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
        path = "/finance/budget" + (f"?year={int(year)}" if year else "")
        try:
            result = hub.call(
                "GET", path, user_id, auth["team_id"], ["finance.read"], "get_expense_budget",
                is_org_admin=auth["is_org_admin"], is_team_admin=auth["is_team_admin"],
            )
        except hub.EnterpriseHubError as exc:
            return _error_json(exc)
        return json.dumps(result, ensure_ascii=False)


@ToolRegistry.register
class CreateExpenseDraftTool(BaseTool):
    requires_context = True
    risk_level = "write"  # 建的是草稿，还没提交，用户确认前可以反复改

    def get_name(self) -> str:
        return "create_expense_draft"

    def get_description(self) -> str:
        return "创建一条报销申请草稿（还没提交），可以包含多条费用明细。"

    def get_parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "lines": {
                    "type": "array",
                    "description": "费用明细列表",
                    "items": {
                        "type": "object",
                        "properties": {
                            "category": {"type": "string",
                                         "description": "费用类别：TRAVEL/MEAL/OFFICE_SUPPLY/TRANSPORT/OTHER"},
                            "amount": {"type": "number", "description": "金额"},
                            "description": {"type": "string", "description": "说明，可选"},
                            "invoice_no": {"type": "string", "description": "发票号，可选"},
                        },
                        "required": ["category", "amount"],
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
        raw_lines = kwargs.get("lines") or []
        lines = [
            {"category": line.get("category"), "amount": line.get("amount"),
             "description": line.get("description"), "invoiceNo": line.get("invoice_no")}
            for line in raw_lines
        ]
        try:
            result = hub.call(
                "POST", "/finance/expenses", user_id, auth["team_id"], ["finance.write"],
                "create_expense_draft",
                is_org_admin=auth["is_org_admin"], is_team_admin=auth["is_team_admin"],
                json_body={"lines": lines}, idempotency_key=str(uuid.uuid4()),
            )
        except hub.EnterpriseHubError as exc:
            return _error_json(exc)
        return json.dumps(result, ensure_ascii=False)


@ToolRegistry.register
class SubmitExpenseClaimTool(BaseTool):
    requires_context = True
    risk_level = "high_risk"  # 提交后进入审批流程，不能由模型自动触发

    def get_name(self) -> str:
        return "submit_expense_claim"

    def get_description(self) -> str:
        return "提交一条草稿状态的报销申请，提交后进入待审批状态。"

    def get_parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {"request_id": {"type": "integer", "description": "报销单 id"}},
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
                "POST", f"/finance/expenses/{int(request_id)}/submit", user_id, auth["team_id"],
                ["finance.write"], "submit_expense_claim",
                is_org_admin=auth["is_org_admin"], is_team_admin=auth["is_team_admin"],
                idempotency_key=str(uuid.uuid4()),
            )
        except hub.EnterpriseHubError as exc:
            return _error_json(exc)
        return json.dumps(result, ensure_ascii=False)


@ToolRegistry.register
class ApproveExpenseClaimTool(BaseTool):
    requires_context = True
    risk_level = "high_risk"  # 批准会真的扣预算，不能由模型自动触发

    def get_name(self) -> str:
        return "approve_expense_claim"

    def get_description(self) -> str:
        return "批准一条已提交的报销申请（只有部门负责人视角的对话该调用这个工具）。"

    def get_parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "request_id": {"type": "integer", "description": "报销单 id"},
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
            return json.dumps({"error": "当前用户不是部门负责人或企业管理员，无权审批报销申请"}, ensure_ascii=False)
        request_id = kwargs.get("request_id")
        try:
            result = hub.call(
                "POST", f"/finance/expenses/{int(request_id)}/approve", user_id, auth["team_id"],
                ["finance.approve"], "approve_expense_claim",
                is_org_admin=auth["is_org_admin"], is_team_admin=auth["is_team_admin"],
                json_body={"note": kwargs.get("note")}, idempotency_key=str(uuid.uuid4()),
            )
        except hub.EnterpriseHubError as exc:
            return _error_json(exc)
        return json.dumps(result, ensure_ascii=False)


@ToolRegistry.register
class RejectExpenseClaimTool(BaseTool):
    requires_context = True
    risk_level = "high_risk"  # 拒绝同样是真实的财务决定，不能由模型自动触发

    def get_name(self) -> str:
        return "reject_expense_claim"

    def get_description(self) -> str:
        return "拒绝一条已提交的报销申请（只有部门负责人视角的对话该调用这个工具）。"

    def get_parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "request_id": {"type": "integer", "description": "报销单 id"},
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
            return json.dumps({"error": "当前用户不是部门负责人或企业管理员，无权处理报销申请"}, ensure_ascii=False)
        request_id = kwargs.get("request_id")
        try:
            result = hub.call(
                "POST", f"/finance/expenses/{int(request_id)}/reject", user_id, auth["team_id"],
                ["finance.approve"], "reject_expense_claim",
                is_org_admin=auth["is_org_admin"], is_team_admin=auth["is_team_admin"],
                json_body={"note": kwargs.get("note")}, idempotency_key=str(uuid.uuid4()),
            )
        except hub.EnterpriseHubError as exc:
            return _error_json(exc)
        return json.dumps(result, ensure_ascii=False)


@ToolRegistry.register
class GetExpenseStatusTool(BaseTool):
    requires_context = True
    risk_level = "read"

    def get_name(self) -> str:
        return "get_expense_status"

    def get_description(self) -> str:
        return "查询一条报销申请当前的状态（草稿/已提交/已批准/已拒绝）。"

    def get_parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {"request_id": {"type": "integer", "description": "报销单 id"}},
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
                "GET", f"/finance/expenses/{int(request_id)}", user_id, auth["team_id"],
                ["finance.read"], "get_expense_status",
                is_org_admin=auth["is_org_admin"], is_team_admin=auth["is_team_admin"],
            )
        except hub.EnterpriseHubError as exc:
            return _error_json(exc)
        return json.dumps(result, ensure_ascii=False)


@ToolRegistry.register
class GetMyExpenseClaimsTool(BaseTool):
    requires_context = True
    risk_level = "read"

    def get_name(self) -> str:
        return "get_my_expense_claims"

    def get_description(self) -> str:
        return "查询当前用户自己提交过的所有报销申请（不分状态）。"

    def get_parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    def execute(self, **kwargs) -> str:
        try:
            user_id, auth = _require_user_and_auth(self._ctx)
        except ValueError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)
        try:
            result = hub.call(
                "GET", "/finance/expenses/mine", user_id, auth["team_id"],
                ["finance.read"], "get_my_expense_claims",
                is_org_admin=auth["is_org_admin"], is_team_admin=auth["is_team_admin"],
            )
        except hub.EnterpriseHubError as exc:
            return _error_json(exc)
        return json.dumps(result, ensure_ascii=False)


@ToolRegistry.register
class GetTeamPendingExpenseClaimsTool(BaseTool):
    requires_context = True
    risk_level = "read"

    def get_name(self) -> str:
        return "get_team_pending_expense_claims"

    def get_description(self) -> str:
        return "查询本部门待审批的报销申请（只有部门负责人/企业管理员该调用）。"

    def get_parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    def execute(self, **kwargs) -> str:
        try:
            user_id, auth = _require_user_and_auth(self._ctx)
        except ValueError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)
        if not (auth["is_org_admin"] or auth["is_team_admin"]):
            return json.dumps({"error": "当前用户不是部门负责人或企业管理员，无权查看待审批列表"}, ensure_ascii=False)
        try:
            result = hub.call(
                "GET", f"/finance/expenses/team-pending?teamId={int(auth['team_id'])}",
                user_id, auth["team_id"], ["finance.read"], "get_team_pending_expense_claims",
                is_org_admin=auth["is_org_admin"], is_team_admin=auth["is_team_admin"],
            )
        except hub.EnterpriseHubError as exc:
            return _error_json(exc)
        return json.dumps(result, ensure_ascii=False)
