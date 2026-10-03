package com.enterprisehub.it;

import jakarta.persistence.*;

import java.time.Duration;
import java.time.Instant;

@Entity
@Table(name = "it_ticket")
public class ItTicket {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "requester_user_id", nullable = false)
    private long requesterUserId;

    @Column(name = "team_id", nullable = false)
    private long teamId;

    @Enumerated(EnumType.STRING)
    @Column(name = "category", nullable = false, length = 20)
    private TicketCategory category;

    @Enumerated(EnumType.STRING)
    @Column(name = "priority", nullable = false, length = 10)
    private TicketPriority priority;

    @Column(name = "title", nullable = false, length = 120)
    private String title;

    @Column(name = "description", nullable = false, columnDefinition = "TEXT")
    private String description;

    @Enumerated(EnumType.STRING)
    @Column(name = "status", nullable = false, length = 20)
    private TicketStatus status;

    @Column(name = "assignee_user_id")
    private Long assigneeUserId;

    @Column(name = "approver_user_id")
    private Long approverUserId;

    @Column(name = "decision_note", length = 500)
    private String decisionNote;

    @Column(name = "suggested_category", length = 20)
    private String suggestedCategory;

    @Column(name = "suggested_priority", length = 10)
    private String suggestedPriority;

    @Column(name = "classify_reason", length = 300)
    private String classifyReason;

    @Column(name = "created_at", nullable = false)
    private Instant createdAt;

    @Column(name = "updated_at", nullable = false)
    private Instant updatedAt;

    @Column(name = "first_response_at")
    private Instant firstResponseAt;

    @Column(name = "sla_due_at", nullable = false)
    private Instant slaDueAt;

    @Column(name = "resolved_at")
    private Instant resolvedAt;

    @Column(name = "resolution", length = 1000)
    private String resolution;

    @Column(name = "closed_at")
    private Instant closedAt;

    @Column(name = "reopen_count", nullable = false)
    private int reopenCount;

    @Column(name = "waiting_since")
    private Instant waitingSince;

    protected ItTicket() {
    }

    public ItTicket(long requesterUserId, long teamId, TicketCategory category, TicketPriority priority,
                    String title, String description) {
        this.requesterUserId = requesterUserId;
        this.teamId = teamId;
        this.category = category;
        this.priority = priority;
        this.title = title;
        this.description = description;
        this.status = category.needsApproval() ? TicketStatus.PENDING_APPROVAL : TicketStatus.OPEN;
        this.createdAt = Instant.now();
        this.updatedAt = this.createdAt;
        restartSla(this.createdAt);
    }

    public void restartSla(Instant from) {
        this.slaDueAt = from.plus(Duration.ofHours(priority.slaHours()));
    }

    public void enterWaiting() {
        this.status = TicketStatus.WAITING_USER;
        this.waitingSince = Instant.now();
        touch();
    }

    /** 离开“等待用户”：等待的时长不计入处理时限，截止时间顺延。 */
    public void leaveWaiting() {
        if (waitingSince != null) {
            Duration waited = Duration.between(waitingSince, Instant.now());
            if (!waited.isNegative()) {
                this.slaDueAt = slaDueAt.plus(waited);
            }
            this.waitingSince = null;
        }
    }

    public void touch() {
        this.updatedAt = Instant.now();
    }

    public void setStatus(TicketStatus status) {
        this.status = status;
        touch();
    }

    public void setSuggestion(String category, String priority, String reason) {
        this.suggestedCategory = category;
        this.suggestedPriority = priority;
        this.classifyReason = reason;
    }

    public void recordFirstResponse() {
        if (firstResponseAt == null) {
            firstResponseAt = Instant.now();
        }
    }

    public void approve(long approver, String note) {
        this.approverUserId = approver;
        this.decisionNote = note;
        this.status = TicketStatus.OPEN;
        restartSla(Instant.now());
        touch();
    }

    public void reject(long approver, String note) {
        this.approverUserId = approver;
        this.decisionNote = note;
        this.status = TicketStatus.REJECTED;
        touch();
    }

    public void assignTo(Long userId) {
        this.assigneeUserId = userId;
        touch();
    }

    public void resolve(String resolution) {
        this.status = TicketStatus.RESOLVED;
        this.resolution = resolution;
        this.resolvedAt = Instant.now();
        touch();
    }

    public void close() {
        this.status = TicketStatus.CLOSED;
        this.closedAt = Instant.now();
        touch();
    }

    public void reopen() {
        this.status = assigneeUserId != null ? TicketStatus.IN_PROGRESS : TicketStatus.OPEN;
        this.reopenCount++;
        this.resolvedAt = null;
        this.closedAt = null;
        restartSla(Instant.now());
        touch();
    }

    /** 调整分类/优先级。升级优先级时从现在起按新时限计时；降级不延长已有的截止时间（避免靠降级躲过超时）。 */
    public void reclassify(TicketCategory category, TicketPriority priority) {
        boolean upgraded = priority.slaHours() < this.priority.slaHours();
        this.category = category;
        this.priority = priority;
        if (upgraded) {
            Instant candidate = Instant.now().plus(Duration.ofHours(priority.slaHours()));
            if (candidate.isBefore(slaDueAt)) {
                this.slaDueAt = candidate;
            }
        }
        touch();
    }

    public Long getId() { return id; }
    public long getRequesterUserId() { return requesterUserId; }
    public long getTeamId() { return teamId; }
    public TicketCategory getCategory() { return category; }
    public TicketPriority getPriority() { return priority; }
    public String getTitle() { return title; }
    public String getDescription() { return description; }
    public TicketStatus getStatus() { return status; }
    public Long getAssigneeUserId() { return assigneeUserId; }
    public Long getApproverUserId() { return approverUserId; }
    public String getDecisionNote() { return decisionNote; }
    public String getSuggestedCategory() { return suggestedCategory; }
    public String getSuggestedPriority() { return suggestedPriority; }
    public String getClassifyReason() { return classifyReason; }
    public Instant getCreatedAt() { return createdAt; }
    public Instant getUpdatedAt() { return updatedAt; }
    public Instant getFirstResponseAt() { return firstResponseAt; }
    public Instant getSlaDueAt() { return slaDueAt; }
    public Instant getResolvedAt() { return resolvedAt; }
    public String getResolution() { return resolution; }
    public Instant getClosedAt() { return closedAt; }
    public int getReopenCount() { return reopenCount; }
}
