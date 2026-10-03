"""工作流注册表。新增一类工作：写一个定义模块（参照 crm.py），在 _BUILTIN 里登记即可。"""
from typing import Dict, List, Optional

from service.exceptions import InvalidInput
from service.workflows.base import WorkflowDefinition, WriteRequest  # noqa: F401
from service.workflows import crm, expense, leave, procurement, ticket

REGISTRY: Dict[str, WorkflowDefinition] = {}
_BUILTIN = (expense, leave, procurement, crm, ticket)


def register(workflow: WorkflowDefinition) -> WorkflowDefinition:
    if workflow.id in REGISTRY:
        raise ValueError(f"工作流 {workflow.id} 重复注册")
    REGISTRY[workflow.id] = workflow
    return workflow


def unregister(workflow_id: str) -> None:
    REGISTRY.pop(workflow_id, None)


for _module in _BUILTIN:
    register(_module.WORKFLOW)


def get_workflow(workflow_id: str) -> WorkflowDefinition:
    workflow = REGISTRY.get(workflow_id)
    if workflow is None:
        raise InvalidInput("不支持的工作类型")
    return workflow


def all_workflows() -> List[WorkflowDefinition]:
    return sorted(REGISTRY.values(), key=lambda w: (w.order, w.id))


def catalog(department_code: Optional[str]) -> Dict[str, object]:
    """前端用的目录：全部工作流（历史成果需要显示名称），标注当前部门是否可用，以及默认选中哪个。"""
    workflows = all_workflows()
    available = [w for w in workflows if w.available_for(department_code)]
    preferred = next((w for w in available if department_code in w.preferred_for), None)
    default = preferred or (available[0] if available else None)
    return {"workflows": [w.public(department_code) for w in workflows],
            "default_id": default.id if default else None}
