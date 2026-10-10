package com.enterprisehub.finance;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.Table;

import java.math.BigDecimal;

/** 凭证分录：借方来自报销明细（每条一行），贷方一行汇总到“其他应付款-员工报销款”。 */
@Entity
@Table(name = "voucher_entry")
public class VoucherEntry {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "voucher_id", nullable = false)
    private long voucherId;

    @Column(name = "line_no", nullable = false)
    private int lineNo;

    @Column(name = "subject_code", nullable = false, length = 20)
    private String subjectCode;

    @Column(name = "subject_name", nullable = false, length = 60)
    private String subjectName;

    @Column(name = "direction", nullable = false, length = 1)
    private String direction;

    @Column(name = "amount", nullable = false, precision = 14, scale = 2)
    private BigDecimal amount;

    @Column(name = "summary", nullable = false, length = 200)
    private String summary;

    @Column(name = "basis", nullable = false, length = 300)
    private String basis;

    @Column(name = "confidence", nullable = false, length = 10)
    private String confidence;

    @Column(name = "manual_override", nullable = false)
    private boolean manualOverride;

    @Column(name = "claim_line_id")
    private Long claimLineId;

    protected VoucherEntry() {
    }

    public VoucherEntry(long voucherId, int lineNo, String subjectCode, String subjectName, String direction,
                        BigDecimal amount, String summary, String basis, String confidence, Long claimLineId) {
        this.voucherId = voucherId;
        this.lineNo = lineNo;
        this.subjectCode = subjectCode;
        this.subjectName = subjectName;
        this.direction = direction;
        this.amount = amount;
        this.summary = summary;
        this.basis = basis;
        this.confidence = confidence;
        this.claimLineId = claimLineId;
    }

    /** 人工改科目：金额不动，依据改成人工说明，置信度记为 MANUAL。 */
    public void overrideSubject(String code, String name, String reason) {
        this.subjectCode = code;
        this.subjectName = name;
        this.basis = reason;
        this.confidence = "MANUAL";
        this.manualOverride = true;
    }

    public Long getId() {
        return id;
    }

    public long getVoucherId() {
        return voucherId;
    }

    public int getLineNo() {
        return lineNo;
    }

    public String getSubjectCode() {
        return subjectCode;
    }

    public String getSubjectName() {
        return subjectName;
    }

    public String getDirection() {
        return direction;
    }

    public BigDecimal getAmount() {
        return amount;
    }

    public String getSummary() {
        return summary;
    }

    public String getBasis() {
        return basis;
    }

    public String getConfidence() {
        return confidence;
    }

    public boolean isManualOverride() {
        return manualOverride;
    }

    public Long getClaimLineId() {
        return claimLineId;
    }
}
