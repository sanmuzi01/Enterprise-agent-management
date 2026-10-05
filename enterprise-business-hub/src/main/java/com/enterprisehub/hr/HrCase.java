package com.enterprisehub.hr;

import jakarta.persistence.*;

import java.time.Instant;
import java.time.LocalDate;

@Entity
@Table(name = "hr_case")
public class HrCase {
    public static final String PENDING_APPROVAL = "PENDING_APPROVAL";
    public static final String IN_PROGRESS = "IN_PROGRESS";
    public static final String COMPLETED = "COMPLETED";
    public static final String REJECTED = "REJECTED";
    public static final String CANCELLED = "CANCELLED";

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Enumerated(EnumType.STRING)
    @Column(name = "case_type", nullable = false, length = 20)
    private HrCaseType caseType;

    @Column(name = "employee_user_id", nullable = false)
    private long employeeUserId;

    @Column(name = "team_id", nullable = false)
    private long teamId;

    @Column(name = "target_team_id")
    private Long targetTeamId;

    @Column(name = "position", length = 80)
    private String position;

    @Column(name = "effective_date", nullable = false)
    private LocalDate effectiveDate;

    @Column(name = "reason", length = 500)
    private String reason;

    @Column(name = "status", nullable = false, length = 20)
    private String status;

    @Column(name = "initiator_user_id", nullable = false)
    private long initiatorUserId;

    @Column(name = "approver_user_id")
    private Long approverUserId;

    @Column(name = "decision_note", length = 500)
    private String decisionNote;

    @Column(name = "employee_is_head", nullable = false)
    private boolean employeeIsHead;

    @Column(name = "check_json", columnDefinition = "TEXT")
    private String checkJson;

    @Column(name = "effect_pending", nullable = false)
    private boolean effectPending;

    @Column(name = "created_at", nullable = false)
    private Instant createdAt;

    @Column(name = "updated_at", nullable = false)
    private Instant updatedAt;

    @Column(name = "completed_at")
    private Instant completedAt;

    protected HrCase() {
    }

    public HrCase(HrCaseType caseType, long employeeUserId, long teamId, Long targetTeamId, String position,
                  LocalDate effectiveDate, String reason, long initiatorUserId, boolean employeeIsHead) {
        this.caseType = caseType;
        this.employeeUserId = employeeUserId;
        this.teamId = teamId;
        this.targetTeamId = targetTeamId;
        this.position = position;
        this.effectiveDate = effectiveDate;
        this.reason = reason;
        this.initiatorUserId = initiatorUserId;
        this.employeeIsHead = employeeIsHead;
        this.status = PENDING_APPROVAL;
        this.createdAt = Instant.now();
        this.updatedAt = this.createdAt;
    }

    public void setChecks(String json) {
        this.checkJson = json;
        this.updatedAt = Instant.now();
    }

    public void approve(long approver, String note) {
        this.status = IN_PROGRESS;
        this.approverUserId = approver;
        this.decisionNote = note;
        this.updatedAt = Instant.now();
    }

    public void reject(long approver, String note) {
        this.status = REJECTED;
        this.approverUserId = approver;
        this.decisionNote = note;
        this.updatedAt = Instant.now();
    }

    public void cancel() {
        this.status = CANCELLED;
        this.updatedAt = Instant.now();
    }

    public void complete() {
        this.status = COMPLETED;
        this.effectPending = caseType == HrCaseType.TRANSFER || caseType == HrCaseType.OFFBOARDING;
        this.completedAt = Instant.now();
        this.updatedAt = this.completedAt;
    }

    public void effectApplied() {
        this.effectPending = false;
        this.updatedAt = Instant.now();
    }

    public Long getId() { return id; }
    public HrCaseType getCaseType() { return caseType; }
    public long getEmployeeUserId() { return employeeUserId; }
    public long getTeamId() { return teamId; }
    public Long getTargetTeamId() { return targetTeamId; }
    public String getPosition() { return position; }
    public LocalDate getEffectiveDate() { return effectiveDate; }
    public String getReason() { return reason; }
    public String getStatus() { return status; }
    public long getInitiatorUserId() { return initiatorUserId; }
    public Long getApproverUserId() { return approverUserId; }
    public String getDecisionNote() { return decisionNote; }
    public boolean isEmployeeIsHead() { return employeeIsHead; }
    public String getCheckJson() { return checkJson; }
    public boolean isEffectPending() { return effectPending; }
    public Instant getCreatedAt() { return createdAt; }
    public Instant getUpdatedAt() { return updatedAt; }
    public Instant getCompletedAt() { return completedAt; }
}
