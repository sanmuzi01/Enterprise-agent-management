package com.enterprisehub.finance;

import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;

public interface ExpenseClaimRepository extends JpaRepository<ExpenseClaim, Long> {
    List<ExpenseClaim> findByApplicantUserIdOrderByCreatedAtDesc(long applicantUserId);

    List<ExpenseClaim> findByTeamIdAndStatusOrderByCreatedAtDesc(long teamId, ExpenseStatus status);
}
