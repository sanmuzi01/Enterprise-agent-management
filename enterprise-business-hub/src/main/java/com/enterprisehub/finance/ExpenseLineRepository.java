package com.enterprisehub.finance;

import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.util.Collection;
import java.util.List;

public interface ExpenseLineRepository extends JpaRepository<ExpenseLine, Long> {
    List<ExpenseLine> findByExpenseClaimId(long expenseClaimId);

    /** 发票号在未被驳回的报销单里的使用情况：[invoiceNo, claimId, status]。驳回的单据不占用发票。 */
    @Query("SELECT l.invoiceNo, c.id, c.status FROM ExpenseLine l, ExpenseClaim c "
            + "WHERE l.expenseClaimId = c.id AND l.invoiceNo IN :numbers "
            + "AND c.status <> com.enterprisehub.finance.ExpenseStatus.REJECTED ORDER BY c.id")
    List<Object[]> findActiveInvoiceUsages(@Param("numbers") Collection<String> numbers);
}
