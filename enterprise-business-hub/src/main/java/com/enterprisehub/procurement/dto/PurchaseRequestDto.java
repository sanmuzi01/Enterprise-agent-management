package com.enterprisehub.procurement.dto;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.List;

public record PurchaseRequestDto(
        long id,
        long requesterUserId,
        long teamId,
        String status,
        BigDecimal totalAmount,
        List<LineDto> lines,
        Long approverUserId,
        String decisionNote,
        Instant createdAt,
        Instant submittedAt,
        Instant decidedAt,
        PurchaseOrderDto purchaseOrder,
        String reason
) {
    /** 目录外物品 sku 为空、nonCatalog=true，unitPrice 是申请人给的预估单价。 */
    public record LineDto(String sku, String productName, int quantity, BigDecimal unitPrice, boolean nonCatalog) {
    }

    public record PurchaseOrderDto(String supplierCode, String supplierName, String status) {
    }
}
