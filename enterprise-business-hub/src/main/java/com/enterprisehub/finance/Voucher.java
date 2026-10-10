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
import java.time.LocalDate;

/** 记账凭证头。分录在 {@link VoucherEntry}（独立表，不用 JPA 级联，跟报销单同一风格）。 */
@Entity
@Table(name = "voucher")
public class Voucher {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "expense_claim_id", nullable = false)
    private long expenseClaimId;

    @Column(name = "team_id", nullable = false)
    private long teamId;

    @Column(name = "applicant_user_id", nullable = false)
    private long applicantUserId;

    @Enumerated(EnumType.STRING)
    @Column(name = "status", nullable = false, length = 20)
    private VoucherStatus status;

    @Column(name = "expense_class", nullable = false, length = 10)
    private String expenseClass;

    @Column(name = "voucher_date", nullable = false)
    private LocalDate voucherDate;

    @Column(name = "period", nullable = false, length = 7)
    private String period;

    @Column(name = "summary", nullable = false, length = 200)
    private String summary;

    @Column(name = "total_amount", nullable = false, precision = 14, scale = 2)
    private BigDecimal totalAmount;

    @Column(name = "voucher_no", length = 30)
    private String voucherNo;

    @Column(name = "risk_level", nullable = false, length = 10)
    private String riskLevel;

    @Column(name = "risk_json", columnDefinition = "TEXT")
    private String riskJson;

    @Column(name = "created_by", nullable = false)
    private long createdBy;

    @Column(name = "created_at", nullable = false)
    private Instant createdAt;

    @Column(name = "updated_at", nullable = false)
    private Instant updatedAt;

    @Column(name = "confirmed_by")
    private Long confirmedBy;

    @Column(name = "confirm_note", length = 500)
    private String confirmNote;

    @Column(name = "warnings_acknowledged", nullable = false)
    private boolean warningsAcknowledged;

    @Column(name = "posted_at")
    private Instant postedAt;

    @Column(name = "voided_by")
    private Long voidedBy;

    @Column(name = "void_reason", length = 300)
    private String voidReason;

    @Column(name = "voided_at")
    private Instant voidedAt;

    protected Voucher() {
    }

    public Voucher(long expenseClaimId, long teamId, long applicantUserId, String expenseClass,
                   LocalDate voucherDate, String summary, BigDecimal totalAmount, long createdBy) {
        this.expenseClaimId = expenseClaimId;
        this.teamId = teamId;
        this.applicantUserId = applicantUserId;
        this.expenseClass = expenseClass;
        this.voucherDate = voucherDate;
        this.period = voucherDate.toString().substring(0, 7);
        this.summary = summary;
        this.totalAmount = totalAmount;
        this.createdBy = createdBy;
        this.status = VoucherStatus.DRAFT;
        this.riskLevel = "NONE";
        this.createdAt = Instant.now();
        this.updatedAt = this.createdAt;
    }

    public void setRisk(String level, String json) {
        this.riskLevel = level;
        this.riskJson = json;
        this.updatedAt = Instant.now();
    }

    public void touch() {
        this.updatedAt = Instant.now();
    }

    public void post(long confirmer, String note, boolean acknowledged, String voucherNo) {
        this.status = VoucherStatus.POSTED;
        this.confirmedBy = confirmer;
        this.confirmNote = note;
        this.warningsAcknowledged = acknowledged;
        this.voucherNo = voucherNo;
        this.postedAt = Instant.now();
        this.updatedAt = this.postedAt;
    }

    public void voidIt(long operator, String reason) {
        this.status = VoucherStatus.VOID;
        this.voidedBy = operator;
        this.voidReason = reason;
        this.voidedAt = Instant.now();
        this.updatedAt = this.voidedAt;
    }

    public void setVoucherDate(LocalDate voucherDate) {
        this.voucherDate = voucherDate;
        this.period = voucherDate.toString().substring(0, 7);
        this.updatedAt = Instant.now();
    }

    public Long getId() {
        return id;
    }

    public long getExpenseClaimId() {
        return expenseClaimId;
    }

    public long getTeamId() {
        return teamId;
    }

    public long getApplicantUserId() {
        return applicantUserId;
    }

    public VoucherStatus getStatus() {
        return status;
    }

    public String getExpenseClass() {
        return expenseClass;
    }

    public LocalDate getVoucherDate() {
        return voucherDate;
    }

    public String getPeriod() {
        return period;
    }

    public String getSummary() {
        return summary;
    }

    public BigDecimal getTotalAmount() {
        return totalAmount;
    }

    public String getVoucherNo() {
        return voucherNo;
    }

    public String getRiskLevel() {
        return riskLevel;
    }

    public String getRiskJson() {
        return riskJson;
    }

    public long getCreatedBy() {
        return createdBy;
    }

    public Instant getCreatedAt() {
        return createdAt;
    }

    public Instant getUpdatedAt() {
        return updatedAt;
    }

    public Long getConfirmedBy() {
        return confirmedBy;
    }

    public String getConfirmNote() {
        return confirmNote;
    }

    public boolean isWarningsAcknowledged() {
        return warningsAcknowledged;
    }

    public Instant getPostedAt() {
        return postedAt;
    }

    public Long getVoidedBy() {
        return voidedBy;
    }

    public String getVoidReason() {
        return voidReason;
    }

    public Instant getVoidedAt() {
        return voidedAt;
    }
}
