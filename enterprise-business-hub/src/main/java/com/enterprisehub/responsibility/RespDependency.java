package com.enterprisehub.responsibility;

import jakarta.persistence.*;

@Entity
@Table(name = "responsibility_dependency")
public class RespDependency {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "task_id", nullable = false)
    private long taskId;

    @Column(name = "depends_on_task_id", nullable = false)
    private long dependsOnTaskId;

    protected RespDependency() {
    }

    public RespDependency(long taskId, long dependsOnTaskId) {
        this.taskId = taskId;
        this.dependsOnTaskId = dependsOnTaskId;
    }

    public long getTaskId() { return taskId; }
    public long getDependsOnTaskId() { return dependsOnTaskId; }
}
