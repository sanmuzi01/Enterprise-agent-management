package com.enterprisehub.responsibility;

import jakarta.persistence.*;

import java.time.Instant;

@Entity
@Table(name = "responsibility_plan")
public class RespPlan {
    public static final String DRAFT = "DRAFT";
    public static final String PUBLISHED = "PUBLISHED";
    public static final String COMPLETED = "COMPLETED";
    public static final String CANCELLED = "CANCELLED";

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "team_id", nullable = false)
    private long teamId;

    @Column(name = "title", nullable = false, length = 160)
    private String title;

    @Column(name = "source_type", nullable = false, length = 20)
    private String sourceType;

    @Column(name = "source_text", columnDefinition = "MEDIUMTEXT")
    private String sourceText;

    @Column(name = "summary", length = 1000)
    private String summary;

    @Column(name = "decisions_json", columnDefinition = "TEXT")
    private String decisionsJson;

    @Column(name = "unresolved_json", columnDefinition = "TEXT")
    private String unresolvedJson;

    @Column(name = "status", nullable = false, length = 20)
    private String status;

    @Column(name = "created_by", nullable = false)
    private long createdBy;

    @Column(name = "published_by")
    private Long publishedBy;

    @Column(name = "automation_work_id", length = 40)
    private String automationWorkId;

    @Column(name = "missing_at_creation", nullable = false)
    private int missingAtCreation;

    @Column(name = "created_at", nullable = false)
    private Instant createdAt;

    @Column(name = "updated_at", nullable = false)
    private Instant updatedAt;

    @Column(name = "published_at")
    private Instant publishedAt;

    protected RespPlan() {
    }

    public RespPlan(long teamId, String title, String sourceType, String sourceText, String summary, String decisionsJson,
                    String unresolvedJson, long createdBy, String automationWorkId) {
        this.teamId = teamId;
        this.title = title;
        this.sourceType = sourceType;
        this.sourceText = sourceText;
        this.summary = summary;
        this.decisionsJson = decisionsJson;
        this.unresolvedJson = unresolvedJson;
        this.status = DRAFT;
        this.createdBy = createdBy;
        this.automationWorkId = automationWorkId;
        this.createdAt = Instant.now();
        this.updatedAt = this.createdAt;
    }

    public void edit(String title, String summary, String unresolvedJson) {
        if (title != null) {
            this.title = title;
        }
        this.summary = summary;
        this.unresolvedJson = unresolvedJson;
        touch();
    }

    public void publish(long by) {
        this.status = PUBLISHED;
        this.publishedBy = by;
        this.publishedAt = Instant.now();
        touch();
    }

    public void setStatus(String status) {
        this.status = status;
        touch();
    }

    public void setMissingAtCreation(int missing) {
        this.missingAtCreation = missing;
    }

    public void touch() {
        this.updatedAt = Instant.now();
    }

    public Long getId() { return id; }
    public long getTeamId() { return teamId; }
    public String getTitle() { return title; }
    public String getSourceType() { return sourceType; }
    public String getSourceText() { return sourceText; }
    public String getSummary() { return summary; }
    public String getDecisionsJson() { return decisionsJson; }
    public String getUnresolvedJson() { return unresolvedJson; }
    public String getStatus() { return status; }
    public long getCreatedBy() { return createdBy; }
    public Long getPublishedBy() { return publishedBy; }
    public String getAutomationWorkId() { return automationWorkId; }
    public int getMissingAtCreation() { return missingAtCreation; }
    public Instant getCreatedAt() { return createdAt; }
    public Instant getUpdatedAt() { return updatedAt; }
    public Instant getPublishedAt() { return publishedAt; }
}
