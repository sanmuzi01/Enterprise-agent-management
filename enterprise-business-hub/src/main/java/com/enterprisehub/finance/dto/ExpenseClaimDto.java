package com.enterprisehub.finance.dto;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.List;

public record ExpenseClaimDto(
        long id,
        long applicantUserId,
        long teamId,
        String status,
        BigDecimal totalAmount,
        List<LineDto> lines,
        Long approverUserId,
        String decisionNote,
        Instant createdAt,
        Instant submittedAt,
        Instant decidedAt
) {
    public record LineDto(String category, BigDecimal amount, String description, String invoiceNo) {
    }
}
