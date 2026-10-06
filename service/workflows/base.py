"""工作流定义：一类"材料 → 结构化成果 → 人工核对 → 业务草稿"的工作，只需要声明这些部分，
状态机（整理/待核对/保存/重试/已保存）、幂等键、权限、额度、统计由
service/automation_work_service.py 统一提供，新增工作流不需要再写一套后端流程。"""
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, FrozenSet, List, Optional, Type

from pydantic import BaseModel, ConfigDict

DEPARTMENT_LABELS = {"hr": "人事", "procurement": "采购", "sales": "销售", "finance": "财务", "it": "IT"}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


@dataclass(frozen=True)
class WriteRequest:
    """Java 业务系统写入适配器的输出：调用哪个接口、用什么权限范围、请求体是什么。"""
    path: str
    scope: str
    operation: str
    body: Dict[str, Any]


@dataclass(frozen=True)
class WorkflowDefinition:
    id: str
    title: str                      # 选择器显示：沟通记录 → CRM 跟进与待办
    name: str                       # 成果名称：沟通记录整理
    draft_name: str                 # "已保存{draft_name}草稿"
    schema: Type[BaseModel]         # 模型输出 Schema（额外字段一律拒绝）
    instructions: str               # 只属于这个工作流的整理要求，拼在通用规则之后
    evidence: Callable[[Any], List[str]]                  # 需要逐字出现在原文里的依据
    write: Callable[[Dict[str, Any], Any], WriteRequest]  # 核对后内容 → Java 草稿接口
    form: List[Dict[str, Any]]      # 人工核对表单（前端按描述渲染）
    source_label: str
    example: str
    hint: str
    departments: Optional[FrozenSet[str]] = None          # None = 所有部门可用
    preferred_for: FrozenSet[Optional[str]] = frozenset()  # 这些部门默认选中它
    check: Optional[Callable[[Any, bool], None]] = None   # 额外业务规则；for_save=True 时更严格
    needs_customer: bool = False
    precheck: Optional[Callable[..., Awaitable[None]]] = None  # 调用模型前的真实业务数据预查询
    followups: Optional[Dict[str, str]] = None            # 后续待办：{"key", "title", "due"}
    # 业务系统核对：(user_id, team_id, 整理结果, work) → [{"level": info|warning|blocker, "text"}]
    business_checks: Optional[Callable[..., Awaitable[List[Dict[str, str]]]]] = None
    order: int = 100
    baseline_minutes: float = 10.0   # 手工办理一次的估算时间（分钟），管理员可在后台调整
    extra: Dict[str, Any] = field(default_factory=dict)
    # 整理结果落地前的确定性补充（如按企业成员名单匹配姓名、换算期限）：(db, user_id, team_id, 整理结果, 原文) → 整理结果
    enrich: Optional[Callable[..., Awaitable[Dict[str, Any]]]] = None
    # 自定义保存：不走通用的"POST 一个 Java 草稿接口"，而是由工作流自己校验并写入：(db, user_id, work, 核对后内容) → 业务结果
    apply: Optional[Callable[..., Awaitable[Dict[str, Any]]]] = None

    def available_for(self, department_code: Optional[str]) -> bool:
        return self.departments is None or department_code in self.departments

    def availability_label(self) -> str:
        if self.departments is None:
            return "所有部门"
        return "、".join(DEPARTMENT_LABELS.get(code, code) for code in sorted(self.departments)) + "部门"

    def public(self, department_code: Optional[str]) -> Dict[str, Any]:
        return {
            "id": self.id, "title": self.title, "name": self.name, "draft_name": self.draft_name,
            "source_label": self.source_label, "example": self.example, "hint": self.hint,
            "form": self.form, "rules": self.extra.get("rules", []),
            "needs_customer": self.needs_customer, "followups": self.followups,
            "available": self.available_for(department_code), "availability": self.availability_label(),
        }
