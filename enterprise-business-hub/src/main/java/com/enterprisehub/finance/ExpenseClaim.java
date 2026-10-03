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
import java.time.Instant;

/** 报销单头。明细在 {@link ExpenseLine}（独立表 + repository，不用 JPA 级联，
 * 跟 procurement.PurchaseRequestLine 是同一种风格）。 */
@Entity
@Table(name = "expense_claim")
public class ExpenseClaim {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "applicant_user_id", nullable = false)
    private long applicantUserId;

    @Column(name = "team_id", nullable = false)
    private long teamId;

    @Enumerated(EnumType.STRING)
    @Column(name = "status", nullable = false, length = 20)
    private ExpenseStatus status;

    @Column(name = "total_amount", nullable = false, precision = 14, scale = 2)
    private BigDecimal totalAmount;

    @Column(name = "approver_user_id")
    private Long approverUserId;

    @Column(name = "decision_note", length = 500)
    private String decisionNote;

    @Column(name = "department_code", length = 20)
    private String departmentCode;

    @Column(name = "created_at", nullable = false)
    private Instant createdAt;

    @Column(name = "submitted_at")
    private Instant submittedAt;

    @Column(name = "decided_at")
    private Instant decidedAt;

    protected ExpenseClaim() {
    }

    public ExpenseClaim(long applicantUserId, long teamId, BigDecimal totalAmount) {
        this.applicantUserId = applicantUserId;
        this.teamId = teamId;
        this.totalAmount = totalAmount;
        this.status = ExpenseStatus.DRAFT;
        this.createdAt = Instant.now();
    }

    public void submit() {
        this.status = ExpenseStatus.SUBMITTED;
        this.submittedAt = Instant.now();
    }

    public void approve(long approverUserId, String note) {
        this.status = ExpenseStatus.APPROVED;
        this.approverUserId = approverUserId;
        this.decisionNote = note;
        this.decidedAt = Instant.now();
    }

    public void reject(long approverUserId, String note) {
        this.status = ExpenseStatus.REJECTED;
        this.approverUserId = approverUserId;
        this.decisionNote = note;
        this.decidedAt = Instant.now();
    }

    public Long getId() {
        return id;
    }

    public String getDepartmentCode() {
        return departmentCode;
    }

    public void setDepartmentCode(String departmentCode) {
        this.departmentCode = departmentCode;
    }

    public long getApplicantUserId() {
        return applicantUserId;
    }

    public long getTeamId() {
        return teamId;
    }

    public ExpenseStatus getStatus() {
        return status;
    }

    public BigDecimal getTotalAmount() {
        return totalAmount;
    }

    public Long getApproverUserId() {
        return approverUserId;
    }

    public String getDecisionNote() {
        return decisionNote;
    }

    public Instant getCreatedAt() {
        return createdAt;
    }

    public Instant getSubmittedAt() {
        return submittedAt;
    }

    public Instant getDecidedAt() {
        return decidedAt;
    }
}
