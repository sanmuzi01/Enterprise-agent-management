package com.enterprisehub.finance.dto;

import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotEmpty;
import jakarta.validation.constraints.Positive;
import jakarta.validation.constraints.Size;

import java.math.BigDecimal;
import java.util.List;

/** departmentCode 可选：申请部门的业务类型，由 FastAPI 在创建时给出，后续生成凭证时用来选科目大类。 */
public record CreateExpenseClaimRequest(@NotEmpty @Valid List<LineItem> lines, @Size(max = 20) String departmentCode) {
    public record LineItem(@NotBlank String category, @Positive BigDecimal amount, String description,
                            String invoiceNo) {
    }
}
