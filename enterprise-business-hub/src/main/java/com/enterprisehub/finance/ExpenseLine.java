package com.enterprisehub.finance;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.Table;

import java.math.BigDecimal;

/** 报销单明细：跟 procurement.PurchaseRequestLine 不同的地方——这里的金额是
 * 用户直接填的，不是按某个主数据（比如 SKU 单价）查出来再乘算的，所以不需要
 * "快照单价"这个概念，字段更简单。 */
@Entity
@Table(name = "expense_line")
public class ExpenseLine {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "expense_claim_id", nullable = false)
    private long expenseClaimId;

    @Enumerated(EnumType.STRING)
    @Column(name = "category", nullable = false, length = 40)
    private ExpenseCategory category;

    @Column(name = "amount", nullable = false, precision = 14, scale = 2)
    private BigDecimal amount;

    @Column(name = "description", length = 200)
    private String description;

    @Column(name = "invoice_no", length = 80)
    private String invoiceNo;

    protected ExpenseLine() {
    }

    public ExpenseLine(long expenseClaimId, ExpenseCategory category, BigDecimal amount, String description,
                        String invoiceNo) {
        this.expenseClaimId = expenseClaimId;
        this.category = category;
        this.amount = amount;
        this.description = description;
        this.invoiceNo = invoiceNo;
    }

    public Long getId() {
        return id;
    }

    public long getExpenseClaimId() {
        return expenseClaimId;
    }

    public ExpenseCategory getCategory() {
        return category;
    }

    public BigDecimal getAmount() {
        return amount;
    }

    public String getDescription() {
        return description;
    }

    public String getInvoiceNo() {
        return invoiceNo;
    }
}
