package com.enterprisehub.hr;

import jakarta.persistence.*;

import java.time.Instant;
import java.time.LocalDate;

@Entity
@Table(name = "hr_case_task")
public class HrCaseTask {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "case_id", nullable = false)
    private long caseId;

    @Column(name = "seq", nullable = false)
    private int seq;

    @Column(name = "title", nullable = false, length = 120)
    private String title;

    @Column(name = "owner", nullable = false, length = 20)
    private String owner;

    @Column(name = "required", nullable = false)
    private boolean required;

    @Column(name = "status", nullable = false, length = 10)
    private String status;

    @Column(name = "due_date", nullable = false)
    private LocalDate dueDate;

    @Column(name = "done_by")
    private Long doneBy;

    @Column(name = "done_at")
    private Instant doneAt;

    @Column(name = "note", length = 300)
    private String note;

    protected HrCaseTask() {
    }

    public HrCaseTask(long caseId, int seq, String title, String owner, boolean required, LocalDate dueDate) {
        this.caseId = caseId;
        this.seq = seq;
        this.title = title;
        this.owner = owner;
        this.required = required;
        this.dueDate = dueDate;
        this.status = "OPEN";
    }

    public void finish(String status, long by, String note) {
        this.status = status;
        this.doneBy = by;
        this.doneAt = Instant.now();
        this.note = note;
    }

    public Long getId() { return id; }
    public long getCaseId() { return caseId; }
    public int getSeq() { return seq; }
    public String getTitle() { return title; }
    public String getOwner() { return owner; }
    public boolean isRequired() { return required; }
    public String getStatus() { return status; }
    public LocalDate getDueDate() { return dueDate; }
    public Long getDoneBy() { return doneBy; }
    public Instant getDoneAt() { return doneAt; }
    public String getNote() { return note; }
}
