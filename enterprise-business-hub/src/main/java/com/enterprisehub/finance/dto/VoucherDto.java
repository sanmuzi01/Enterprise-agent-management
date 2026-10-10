package com.enterprisehub.finance.dto;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.List;

/** 记账凭证详情：分录、风险项、来源报销单明细一起给，核对页不需要再查别的接口。 */
public record VoucherDto(
        long id,
        long expenseClaimId,
        long teamId,
        long applicantUserId,
        String status,
        String expenseClass,
        String voucherDate,
        String period,
        String summary,
        BigDecimal totalAmount,
        String voucherNo,
        String riskLevel,
        List<RiskItem> risks,
        List<EntryDto> entries,
        List<ExpenseClaimDto.LineDto> claimLines,
        ClaimInfo claim,
        Instant createdAt,
        Long confirmedBy,
        String confirmNote,
        boolean warningsAcknowledged,
        Instant postedAt,
        Long voidedBy,
        String voidReason,
        Instant voidedAt
) {
    /** level: INFO / WARN / BLOCK。BLOCK 不处理就不能入账；WARN 需要财务确认时明确勾选已核对。 */
    public record RiskItem(String code, String level, String message) {
    }

    public record EntryDto(long id, int lineNo, String subjectCode, String subjectName, String direction,
                           BigDecimal amount, String summary, String basis, String confidence,
                           boolean manualOverride) {
    }

    public record ClaimInfo(Long approverUserId, String decisionNote, Instant decidedAt, String departmentCode,
                            Instant createdAt) {
    }
}
