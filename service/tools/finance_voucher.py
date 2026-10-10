"""财务记账 Agent 工具：查看待核对凭证、凭证详情与风险、补生成凭证草稿、月度汇总。

只做"建议和整理"，不做"决定"：确认入账、作废、改科目都必须财务人员在工作台里核对后自己操作，
所以这里没有 confirm/void 工具——即使模型被诱导，也没有能入账的工具可调。
权限与财务工作台完全一致（只有财务部门有效成员/企业管理员；可见范围限定在同一企业）。
"""
import json
import uuid

from service import enterprise_hub_client as hub
from service import finance_voucher_service as svc
from service.exceptions import AppError
from service.tools.base import BaseTool, ToolRegistry


def _staff_context(ctx):
    if not ctx or not ctx.user_id:
        raise ValueError("缺少用户上下文，无法调用企业业务中心")
    auth = hub.resolve_caller_context(ctx.user_id, ctx.agent_id)
    team_id = auth["team_id"]
    if team_id is None:
        raise ValueError("当前用户不属于任何财务部门，无法使用记账凭证")
    try:
        scope = svc.staff_scope(ctx.user_id, team_id)
    except AppError as exc:
        raise ValueError(exc.message)
    return ctx.user_id, team_id, scope


def _error_json(exc: hub.EnterpriseHubError) -> str:
    return json.dumps({"error": exc.detail, "status_code": exc.status_code}, ensure_ascii=False)


def _run(tool, method, base, operation, *, scopes, params=None, json_body=None, write=False, id_check=None):
    try:
        user_id, team_id, scope = _staff_context(tool._ctx)
    except ValueError as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)
    try:
        result = hub.call(method, svc._path(base, scope, **(params or {})), user_id, team_id, scopes, operation,
                          json_body=json_body, idempotency_key=str(uuid.uuid4()) if write else None)
    except hub.EnterpriseHubError as exc:
        return _error_json(exc)
    return json.dumps(result, ensure_ascii=False)


@ToolRegistry.register
class ListPendingVouchersTool(BaseTool):
    requires_context = True
    risk_level = "read"

    def get_name(self) -> str:
        return "list_pending_vouchers"

    def get_description(self) -> str:
        return "列出财务部门待核对的记账凭证草稿（报销单批准后自动生成），含风险等级，便于优先处理有问题的。"

    def get_parameters(self) -> dict:
        return {"type": "object", "properties": {
            "limit": {"type": "integer", "description": "最多返回条数，默认 30，最大 100"}}}

    def execute(self, **kwargs) -> str:
        limit = max(1, min(int(kwargs.get("limit") or 30), 100))
        return _run(self, "GET", "/finance/vouchers", "list_pending_vouchers", scopes=svc.READ,
                    params={"status": "DRAFT", "limit": limit})


@ToolRegistry.register
class GetVoucherDetailTool(BaseTool):
    requires_context = True
    risk_level = "read"

    def get_name(self) -> str:
        return "get_voucher_detail"

    def get_description(self) -> str:
        return "查看一张记账凭证的分录、科目建议依据、置信度和风险项。"

    def get_parameters(self) -> dict:
        return {"type": "object", "properties": {"voucher_id": {"type": "integer", "description": "凭证 id"}},
                "required": ["voucher_id"]}

    def execute(self, **kwargs) -> str:
        return _run(self, "GET", f"/finance/vouchers/{int(kwargs['voucher_id'])}", "get_voucher_detail",
                    scopes=svc.READ)


@ToolRegistry.register
class GenerateVoucherDraftTool(BaseTool):
    requires_context = True
    risk_level = "write"  # 只生成待核对的草稿，入账仍要财务人员确认

    def get_name(self) -> str:
        return "generate_voucher_draft"

    def get_description(self) -> str:
        return "为一张已批准但还没有凭证的报销单补生成记账凭证草稿（科目按规则建议，需财务人员核对后才能入账）。"

    def get_parameters(self) -> dict:
        return {"type": "object", "properties": {"claim_id": {"type": "integer", "description": "报销单 id"}},
                "required": ["claim_id"]}

    def execute(self, **kwargs) -> str:
        return _run(self, "POST", f"/finance/vouchers/from-claim/{int(kwargs['claim_id'])}",
                    "generate_voucher_draft", scopes=svc.WRITE, json_body={}, write=True)


@ToolRegistry.register
class GetVoucherMonthlySummaryTool(BaseTool):
    requires_context = True
    risk_level = "read"

    def get_name(self) -> str:
        return "get_voucher_monthly_summary"

    def get_description(self) -> str:
        return "查询某个期间（YYYY-MM，默认本月）的记账汇总：已入账按科目/部门汇总、待确认与风险积压、未生成凭证的报销单。"

    def get_parameters(self) -> dict:
        return {"type": "object", "properties": {
            "period": {"type": "string", "description": "期间，格式 YYYY-MM，不填默认本月"}}}

    def execute(self, **kwargs) -> str:
        output = _run(self, "GET", "/finance/vouchers/summary", "get_voucher_monthly_summary", scopes=svc.READ,
                      params={"period": kwargs.get("period") or None})
        try:
            data = json.loads(output)
        except ValueError:
            return output
        if "error" not in data:
            data["narrative"] = svc.narrative_for(data)
        return json.dumps(data, ensure_ascii=False)
