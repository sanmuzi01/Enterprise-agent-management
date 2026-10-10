package com.enterprisehub.finance;

import jakarta.persistence.LockModeType;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Lock;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.util.Collection;
import java.util.List;
import java.util.Optional;

public interface ExpenseClaimRepository extends JpaRepository<ExpenseClaim, Long> {
    /** 审批/生成凭证前锁住报销单行：两个审批人同时批准同一张单据时，第二个会等第一个提交后看到已批准状态。 */
    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("SELECT c FROM ExpenseClaim c WHERE c.id = :id")
    Optional<ExpenseClaim> findForUpdate(@Param("id") long id);

    /** 已批准但还没有凭证的报销单（范围内），供补生成凭证。 */
    @Query("SELECT c FROM ExpenseClaim c WHERE c.teamId IN :teamIds AND c.status = "
            + "com.enterprisehub.finance.ExpenseStatus.APPROVED AND NOT EXISTS "
            + "(SELECT 1 FROM Voucher v WHERE v.expenseClaimId = c.id) ORDER BY c.id")
    List<ExpenseClaim> findApprovedWithoutVoucher(@Param("teamIds") Collection<Long> teamIds);

    List<ExpenseClaim> findByApplicantUserIdOrderByCreatedAtDesc(long applicantUserId);

    List<ExpenseClaim> findByTeamIdAndStatusOrderByCreatedAtDesc(long teamId, ExpenseStatus status);
}
