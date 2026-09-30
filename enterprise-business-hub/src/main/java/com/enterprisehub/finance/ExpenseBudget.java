package com.enterprisehub.finance;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import jakarta.persistence.UniqueConstraint;

import java.math.BigDecimal;

/** 部门报销预算——跟 procurement.DepartmentBudget 结构一样，但物理上是独立的表，
 * 不共享同一个 (team_id, year) 额度池：采购预算和报销预算在真实财务管理里是
 * 分开管理的，不应该从同一个数字里扣。 */
@Entity
@Table(name = "expense_budget", uniqueConstraints = {
        @UniqueConstraint(name = "uq_expense_budget_team_year", columnNames = {"team_id", "year"})
})
public class ExpenseBudget {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "team_id", nullable = false)
    private long teamId;

    @Column(name = "year", nullable = false)
    private int year;

    @Column(name = "remaining_amount", nullable = false, precision = 14, scale = 2)
    private BigDecimal remainingAmount;

    protected ExpenseBudget() {
    }

    public ExpenseBudget(long teamId, int year, BigDecimal remainingAmount) {
        this.teamId = teamId;
        this.year = year;
        this.remainingAmount = remainingAmount;
    }

    public Long getId() {
        return id;
    }

    public long getTeamId() {
        return teamId;
    }

    public int getYear() {
        return year;
    }

    public BigDecimal getRemainingAmount() {
        return remainingAmount;
    }

    public void deduct(BigDecimal amount) {
        this.remainingAmount = this.remainingAmount.subtract(amount);
    }
}
