package com.enterprisehub.crm.dto;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;

import jakarta.validation.constraints.Size;

import java.math.BigDecimal;
import java.time.LocalDate;

/** `opportunityId` 不填就创建新商机，填了就更新那一条（设计稿"创建或更新商机"）。 */
public record UpsertOpportunityRequest(Long opportunityId, @NotBlank String stage, @NotNull BigDecimal amount,
                                       LocalDate expectedCloseDate, @Size(max = 500) String nextStep) {
}
