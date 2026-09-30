package com.enterprisehub.finance.dto;

import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotEmpty;
import jakarta.validation.constraints.Positive;

import java.math.BigDecimal;
import java.util.List;

public record CreateExpenseClaimRequest(@NotEmpty @Valid List<LineItem> lines) {
    public record LineItem(@NotBlank String category, @Positive BigDecimal amount, String description,
                            String invoiceNo) {
    }
}
