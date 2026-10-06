package com.enterprisehub.responsibility;

import jakarta.persistence.*;

import java.time.Instant;

@Entity
@Table(name = "responsibility_deliverable")
public class RespDeliverable {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "task_id", nullable = false)
    private long taskId;

    @Column(name = "submission_no", nullable = false)
    private int submissionNo;

    @Column(name = "summary", nullable = false, length = 1000)
    private String summary;

    @Column(name = "link", length = 500)
    private String link;

    @Column(name = "submitted_by", nullable = false)
    private long submittedBy;

    @Column(name = "submitted_at", nullable = false)
    private Instant submittedAt;

    protected RespDeliverable() {
    }

    public RespDeliverable(long taskId, int submissionNo, String summary, String link, long submittedBy) {
        this.taskId = taskId;
        this.submissionNo = submissionNo;
        this.summary = summary;
        this.link = link;
        this.submittedBy = submittedBy;
        this.submittedAt = Instant.now();
    }

    public Long getId() { return id; }
    public long getTaskId() { return taskId; }
    public int getSubmissionNo() { return submissionNo; }
    public String getSummary() { return summary; }
    public String getLink() { return link; }
    public long getSubmittedBy() { return submittedBy; }
    public Instant getSubmittedAt() { return submittedAt; }
}
