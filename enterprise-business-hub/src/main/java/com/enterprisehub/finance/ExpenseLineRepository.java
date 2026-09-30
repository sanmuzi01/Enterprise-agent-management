package com.enterprisehub.finance;

import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;

public interface ExpenseLineRepository extends JpaRepository<ExpenseLine, Long> {
    List<ExpenseLine> findByExpenseClaimId(long expenseClaimId);
}
