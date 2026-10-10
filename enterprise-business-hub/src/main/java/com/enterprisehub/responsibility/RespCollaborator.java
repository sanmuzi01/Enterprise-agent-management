package com.enterprisehub.responsibility;

import jakarta.persistence.*;

@Entity
@Table(name = "responsibility_collaborator")
public class RespCollaborator {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "task_id", nullable = false)
    private long taskId;

    @Column(name = "user_id", nullable = false)
    private long userId;

    protected RespCollaborator() {
    }

    public RespCollaborator(long taskId, long userId) {
        this.taskId = taskId;
        this.userId = userId;
    }

    public long getTaskId() { return taskId; }
    public long getUserId() { return userId; }
}
