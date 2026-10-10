package com.enterprisehub.finance.dto;

import java.math.BigDecimal;
import java.time.Instant;

/** 凭证列表行：不带分录，列表一屏能放下几十条。 */
public record VoucherSummaryDto(
        long id,
        long expenseClaimId,
        long teamId,
        long applicantUserId,
        String status,
        String voucherDate,
        String period,
        String summary,
        BigDecimal totalAmount,
        String voucherNo,
        String riskLevel,
        Instant createdAt
) {
}
