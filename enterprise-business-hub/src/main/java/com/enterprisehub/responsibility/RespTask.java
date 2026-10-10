package com.enterprisehub.responsibility;

import jakarta.persistence.*;

import java.time.Instant;
import java.time.LocalDate;

@Entity
@Table(name = "responsibility_task")
public class RespTask {
    public static final String DRAFT = "DRAFT";
    public static final String PENDING_ACCEPT = "PENDING_ACCEPT";
    public static final String NEGOTIATING = "NEGOTIATING";
    public static final String IN_PROGRESS = "IN_PROGRESS";
    public static final String BLOCKED = "BLOCKED";
    public static final String PENDING_REVIEW = "PENDING_REVIEW";
    public static final String DONE = "DONE";
    public static final String CANCELLED = "CANCELLED";

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "plan_id", nullable = false)
    private long planId;

    @Column(name = "team_id", nullable = false)
    private long teamId;

    @Column(name = "seq", nullable = false)
    private int seq;

    @Column(name = "title", nullable = false, length = 160)
    private String title;

    @Column(name = "responsible_user_id")
    private Long responsibleUserId;

    @Column(name = "reviewer_user_id")
    private Long reviewerUserId;

    @Column(name = "assigned_by_user_id")
    private Long assignedByUserId;

    @Column(name = "deliverable", length = 300)
    private String deliverable;

    @Column(name = "acceptance_criteria", length = 500)
    private String acceptanceCriteria;

    @Column(name = "priority", nullable = false, length = 10)
    private String priority = "NORMAL";

    @Column(name = "due_date")
    private LocalDate dueDate;

    @Column(name = "status", nullable = false, length = 20)
    private String status;

    @Column(name = "source_evidence", length = 500)
    private String sourceEvidence;

    @Column(name = "blocked_reason", length = 300)
    private String blockedReason;

    @Column(name = "blocked_since")
    private Instant blockedSince;

    @Column(name = "waiting_on_user_id")
    private Long waitingOnUserId;

    @Column(name = "last_progress", length = 500)
    private String lastProgress;

    @Column(name = "progress_percent")
    private Integer progressPercent;

    @Column(name = "last_progress_at")
    private Instant lastProgressAt;

    @Column(name = "objection_note", length = 500)
    private String objectionNote;

    @Column(name = "pending_due_date")
    private LocalDate pendingDueDate;

    @Column(name = "extension_reason", length = 300)
    private String extensionReason;

    @Column(name = "transfer_note", length = 300)
    private String transferNote;

    @Column(name = "rework_count", nullable = false)
    private int reworkCount;

    @Column(name = "ai_responsible_user_id")
    private Long aiResponsibleUserId;

    @Column(name = "assigned_at")
    private Instant assignedAt;

    @Column(name = "accepted_at")
    private Instant acceptedAt;

    @Column(name = "first_submitted_at")
    private Instant firstSubmittedAt;

    @Column(name = "submitted_at")
    private Instant submittedAt;

    @Column(name = "verified_at")
    private Instant verifiedAt;

    @Column(name = "completed_at")
    private Instant completedAt;

    @Column(name = "created_at", nullable = false)
    private Instant createdAt;

    @Column(name = "updated_at", nullable = false)
    private Instant updatedAt;

    protected RespTask() {
    }

    public RespTask(long planId, long teamId, int seq, String title) {
        this.planId = planId;
        this.teamId = teamId;
        this.seq = seq;
        this.title = title;
        this.status = DRAFT;
        this.createdAt = Instant.now();
        this.updatedAt = this.createdAt;
    }

    public boolean isTerminal() {
        return DONE.equals(status) || CANCELLED.equals(status);
    }

    /** 还在履责中的状态（会算逾期）。 */
    public boolean isOpenForWork() {
        return PENDING_ACCEPT.equals(status) || NEGOTIATING.equals(status) || IN_PROGRESS.equals(status) || BLOCKED.equals(status);
    }

    public void touch() {
        this.updatedAt = Instant.now();
    }

    public void assign(long by) {
        this.assignedByUserId = by;
        this.assignedAt = Instant.now();
        this.acceptedAt = null;
        this.status = PENDING_ACCEPT;
        this.objectionNote = null;
        touch();
    }

    public void accept() {
        this.status = IN_PROGRESS;
        this.acceptedAt = Instant.now();
        this.objectionNote = null;
        touch();
    }

    public void object(String note) {
        this.status = NEGOTIATING;
        this.objectionNote = note;
        touch();
    }

    public void progress(String note, Integer percent) {
        this.lastProgress = note;
        this.progressPercent = percent;
        this.lastProgressAt = Instant.now();
        touch();
    }

    public void block(String reason, Long waitingOn) {
        this.status = BLOCKED;
        this.blockedReason = reason;
        this.blockedSince = Instant.now();
        this.waitingOnUserId = waitingOn;
        touch();
    }

    public void unblock() {
        this.status = IN_PROGRESS;
        this.blockedReason = null;
        this.blockedSince = null;
        this.waitingOnUserId = null;
        touch();
    }

    public void submit() {
        Instant now = Instant.now();
        this.status = PENDING_REVIEW;
        this.submittedAt = now;
        if (this.firstSubmittedAt == null) {
            this.firstSubmittedAt = now;
        }
        touch();
    }

    public void verify() {
        Instant now = Instant.now();
        this.status = DONE;
        this.verifiedAt = now;
        this.completedAt = now;
        touch();
    }

    public void rework() {
        this.status = IN_PROGRESS;
        this.reworkCount++;
        this.submittedAt = null;
        touch();
    }

    public void cancel() {
        this.status = CANCELLED;
        touch();
    }

    public void requestExtension(LocalDate proposed, String reason) {
        this.pendingDueDate = proposed;
        this.extensionReason = reason;
        touch();
    }

    public void clearExtension() {
        this.pendingDueDate = null;
        this.extensionReason = null;
        touch();
    }

    public void requestTransfer(String note) {
        this.transferNote = note;
        touch();
    }

    public void clearTransfer() {
        this.transferNote = null;
    }

    public void setStatus(String status) { this.status = status; touch(); }
    public void setTitle(String title) { this.title = title; }
    public void setResponsibleUserId(Long id) { this.responsibleUserId = id; }
    public void setReviewerUserId(Long id) { this.reviewerUserId = id; }
    public void setDeliverable(String deliverable) { this.deliverable = deliverable; }
    public void setAcceptanceCriteria(String criteria) { this.acceptanceCriteria = criteria; }
    public void setPriority(String priority) { this.priority = priority; }
    public void setDueDate(LocalDate dueDate) { this.dueDate = dueDate; }
    public void setSourceEvidence(String evidence) { this.sourceEvidence = evidence; }
    public void setAiResponsibleUserId(Long id) { this.aiResponsibleUserId = id; }
    public void setSeq(int seq) { this.seq = seq; }

    public Long getId() { return id; }
    public long getPlanId() { return planId; }
    public long getTeamId() { return teamId; }
    public int getSeq() { return seq; }
    public String getTitle() { return title; }
    public Long getResponsibleUserId() { return responsibleUserId; }
    public Long getReviewerUserId() { return reviewerUserId; }
    public Long getAssignedByUserId() { return assignedByUserId; }
    public String getDeliverable() { return deliverable; }
    public String getAcceptanceCriteria() { return acceptanceCriteria; }
    public String getPriority() { return priority; }
    public LocalDate getDueDate() { return dueDate; }
    public String getStatus() { return status; }
    public String getSourceEvidence() { return sourceEvidence; }
    public String getBlockedReason() { return blockedReason; }
    public Instant getBlockedSince() { return blockedSince; }
    public Long getWaitingOnUserId() { return waitingOnUserId; }
    public String getLastProgress() { return lastProgress; }
    public Integer getProgressPercent() { return progressPercent; }
    public Instant getLastProgressAt() { return lastProgressAt; }
    public String getObjectionNote() { return objectionNote; }
    public LocalDate getPendingDueDate() { return pendingDueDate; }
    public String getExtensionReason() { return extensionReason; }
    public String getTransferNote() { return transferNote; }
    public int getReworkCount() { return reworkCount; }
    public Long getAiResponsibleUserId() { return aiResponsibleUserId; }
    public Instant getAssignedAt() { return assignedAt; }
    public Instant getAcceptedAt() { return acceptedAt; }
    public Instant getFirstSubmittedAt() { return firstSubmittedAt; }
    public Instant getSubmittedAt() { return submittedAt; }
    public Instant getVerifiedAt() { return verifiedAt; }
    public Instant getCompletedAt() { return completedAt; }
    public Instant getCreatedAt() { return createdAt; }
    public Instant getUpdatedAt() { return updatedAt; }
}
