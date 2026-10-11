"""防编造：回答声称“已经办理”（创建、提交、生成……），这一次运行里却没有任何办理类工具真正执行过。

真实事故：员工在采购助手里说“买 10 张 5070 显卡”，同一会话里聊了几轮后，模型不再调用工具，
直接照着对话历史“演”出了库存、单价、“草稿创建成功”“提交成功”，系统里一张单子都没有。
高风险操作本来就必须用户点确认才执行（不会乱写数据），但“没执行却说执行了”同样是错的。

判断只看这一次运行的事实：
- 办理类工具 = 风险等级 write / high_risk 的工具；成功执行或已生成待确认单都算“真的办了”；
- 回答里出现明确的“已为您……”“……成功”这类完成说法，且没有任何办理类工具办成 → 视为编造。
引擎发现后先让模型重来一次（要求真的调用工具，或如实说明没办）；还编造就在回答末尾附上系统核对提示。
"""
import json
import re

# 明确的“这次办成了”说法。刻意不收“已提交的申请有 3 张”这类对已有记录的描述（查询结果里很常见），
# 也不收“提交成功后会进入审批”这类条件句。
_CLAIM_PATTERNS = (
    re.compile(r"(已经?|成功)(为您|帮您|替您|给您|为你|帮你|替你|给你)\S{0,6}?"
               r"(创建|提交|生成|起草|发起|保存|登记|办理|申请|报修|下单|报销|请假|批准|驳回|接受|更新)"),
    re.compile(r"(创建|提交|生成|起草|保存|发起|办理|登记|报修)(成功|完毕)(?!后|之后|以后|的话)"),
    re.compile(r"(草稿|申请单?|单据|工单|报销单|请假单|采购单)(已经?)(为您|帮您)?(创建|生成|提交|起草)(好|成功|完成)?(?!后|之后|以后)"),
)

GUARD_RETRY_PROMPT = (
    "【系统核对】你上一条回答说已经办理（创建 / 提交 / 生成等），但这一轮你没有成功调用任何办理类工具，"
    "业务系统里没有任何记录，这样回答是错误的。请现在调用对应工具真正去办：能查到的信息自己用工具查；"
    "需要用户确认的操作（例如提交）调用工具后界面上会出现确认卡片，由用户点确认，不用在对话里再问一遍。"
    "只有工具确实缺少、查不到、必须由用户决定的信息（例如目录外物品的单价）才向用户询问。"
    "单号、库存、价格、状态只能来自工具返回的结果，不要编造。如果确实办不了，请如实说明原因。"
)

GUARD_WARNING = (
    "\n\n> ⚠️ 系统核对：这次回答没有实际调用业务系统，上面所说的创建 / 提交并没有发生，系统里没有生成任何单据。"
    "请重新说明需求，或到部门工作台对应模块里办理。"
)


def claims_action(text: str) -> bool:
    """回答里是否有“这次已经办成了”的说法。"""
    if not text:
        return False
    return any(p.search(text) for p in _CLAIM_PATTERNS)


def tool_risk_level(tool_name: str) -> str:
    """工具的风险等级；没登记的工具按最严的 high_risk 算（跟 BaseTool 的默认值一致）。"""
    from service.tools.base import ToolRegistry
    tool_class = ToolRegistry.get(tool_name)
    return getattr(tool_class, "risk_level", "high_risk") if tool_class is not None else "high_risk"


def is_action_tool(tool_name: str) -> bool:
    return tool_risk_level(tool_name) in ("write", "high_risk")


def tool_result_outcome(content: str) -> str:
    """工具结果 → "pending"（生成了待确认单）/ "failed"（报错）/ "ok"。"""
    text = (content or "").strip()
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        data = None
    if isinstance(data, dict):
        if data.get("status") == "confirmation_required":
            return "pending"
        if data.get("error"):
            return "failed"
        return "ok"
    if text.startswith("工具权限不足") or text.startswith("Error"):
        return "failed"
    return "ok"
