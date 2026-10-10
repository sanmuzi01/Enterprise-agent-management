package com.enterprisehub.finance;

import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.util.Collection;
import java.util.List;

public interface ExpenseLineRepository extends JpaRepository<ExpenseLine, Long> {
    List<ExpenseLine> findByExpenseClaimId(long expenseClaimId);

    /** 同一申请人在 since 之后、别的未驳回报销单里，同类别同金额的费用：[claimId]。用来提示疑似重复报销。 */
    @Query("SELECT DISTINCT c.id FROM ExpenseLine l, ExpenseClaim c WHERE l.expenseClaimId = c.id "
            + "AND c.applicantUserId = :applicant AND c.id <> :claimId AND l.category = :category "
            + "AND l.amount = :amount AND c.createdAt >= :since "
            + "AND c.status <> com.enterprisehub.finance.ExpenseStatus.REJECTED ORDER BY c.id")
    List<Long> findSimilarClaims(@Param("applicant") long applicant, @Param("claimId") long claimId,
                                 @Param("category") ExpenseCategory category, @Param("amount") java.math.BigDecimal amount,
                                 @Param("since") java.time.Instant since);

    /** 发票号在未被驳回的报销单里的使用情况：[invoiceNo, claimId, status]。驳回的单据不占用发票。 */
    @Query("SELECT l.invoiceNo, c.id, c.status FROM ExpenseLine l, ExpenseClaim c "
            + "WHERE l.expenseClaimId = c.id AND l.invoiceNo IN :numbers "
            + "AND c.status <> com.enterprisehub.finance.ExpenseStatus.REJECTED ORDER BY c.id")
    List<Object[]> findActiveInvoiceUsages(@Param("numbers") Collection<String> numbers);
}
