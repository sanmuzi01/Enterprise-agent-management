"""采购需求 → 采购申请草稿（仅采购部门）。金额由业务系统按产品目录计算，模型不给价格。"""
from typing import List

from pydantic import Field

from service.exceptions import InvalidInput
from service.workflows.base import StrictModel, WorkflowDefinition, WriteRequest


class PurchaseItem(StrictModel):
    sku: str | None = Field(default=None, min_length=1, max_length=40)
    quantity: int | None = Field(default=None, strict=True, gt=0, le=1000000)
    evidence: str = Field(min_length=1, max_length=500)


class ProcurementProposal(StrictModel):
    items: List[PurchaseItem] = Field(min_length=1, max_length=50)
    warnings: List[str] = Field(default_factory=list, max_length=20)


def _check(proposal, for_save):
    skus = [item.sku for item in proposal.items if item.sku]
    if len(skus) != len(set(skus)):
        raise InvalidInput("采购清单存在重复 SKU，请合并数量后保存")
    if for_save and any(item.sku is None or item.quantity is None for item in proposal.items):
        raise InvalidInput("请补全每条采购需求的真实 SKU 和数量")


def _write(data, work):
    return WriteRequest("/procurement/requests", "procurement.write", "create_purchase_draft", {
        "lines": [{"sku": item["sku"], "quantity": item["quantity"]} for item in data["items"]]})


WORKFLOW = WorkflowDefinition(
    id="procurement", title="采购需求 → 采购申请草稿", name="采购需求整理", draft_name="采购",
    schema=ProcurementProposal,
    instructions=("items 只提取原文明确的 SKU 和整数数量；缺失则填 null 并在 warnings 提醒用户补全，"
                  "禁止猜测 SKU、价格和库存。"),
    evidence=lambda p: [item.evidence for item in p.items],
    write=_write,
    form=[
        {"type": "list", "key": "items", "label": "采购需求", "item": "采购需求", "min": 1,
         "unique_by": "sku", "unique_message": "存在重复 SKU，请合并数量后删除重复行。", "fields": [
            {"type": "text", "key": "sku", "label": "产品 SKU", "max": 40, "required": True,
             "placeholder": "填写业务系统中的真实 SKU"},
            {"type": "integer", "key": "quantity", "label": "采购数量", "min": 1, "max": 1000000, "required": True},
            {"type": "evidence", "key": "evidence", "label": "原文依据"},
        ]},
        {"type": "note", "text": "采购价格由业务系统的产品目录计算；预算和审批以业务系统为准。"},
    ],
    source_label="粘贴采购需求、补货清单",
    example="办公室需要采购产品 SKU PAPER-A4 共10件，用于打印合同。",
    hint="提取产品 SKU 与数量；缺失信息会留空，由你补全后保存。",
    departments=frozenset({"procurement"}), preferred_for=frozenset({"procurement"}),
    check=_check, order=30,
)
