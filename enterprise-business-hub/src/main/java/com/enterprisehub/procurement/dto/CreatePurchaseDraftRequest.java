package com.enterprisehub.procurement.dto;

import jakarta.validation.Valid;
import jakarta.validation.constraints.NotEmpty;
import jakarta.validation.constraints.Positive;
import jakarta.validation.constraints.Size;

import java.math.BigDecimal;
import java.util.List;

/**
 * 采购申请草稿。每条明细二选一：
 * - 目录内物品：填 sku，单价取产品目录；
 * - 目录外物品（目录里没有，例如新型号显卡）：sku 留空，填 itemName 和 estimatedUnitPrice（申请人给的预估单价）。
 * 二选一的校验在 ProcurementService 里做，错误信息能直接告诉用户缺什么。
 */
public record CreatePurchaseDraftRequest(@NotEmpty @Valid List<LineItem> lines, @Size(max = 500) String reason) {
    public record LineItem(String sku, @Size(max = 200) String itemName, @Positive BigDecimal estimatedUnitPrice,
                           @Positive int quantity) {
        public boolean catalogItem() {
            return sku != null && !sku.isBlank();
        }
    }
}
