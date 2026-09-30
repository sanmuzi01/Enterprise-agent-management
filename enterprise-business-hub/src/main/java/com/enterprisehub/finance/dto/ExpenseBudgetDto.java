package com.enterprisehub.finance.dto;

import com.enterprisehub.finance.ExpenseBudget;

import java.math.BigDecimal;

public record ExpenseBudgetDto(long teamId, int year, BigDecimal remainingAmount) {
    public static ExpenseBudgetDto from(ExpenseBudget budget) {
        return new ExpenseBudgetDto(budget.getTeamId(), budget.getYear(), budget.getRemainingAmount());
    }
}
