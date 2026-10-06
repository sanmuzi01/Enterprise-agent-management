package com.enterprisehub.responsibility;

import jakarta.persistence.*;

import java.time.Instant;

/** 履责事件：只追加。这个类没有任何 setter，仓库也没有更新/删除入口。 */
@Entity
@Table(name = "responsibility_event")
public class RespEvent {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "plan_id", nullable = false)
    private long planId;

    @Column(name = "task_id")
    private Long taskId;

    @Column(name = "event_type", nullable = false, length = 30)
    private String eventType;

    @Column(name = "actor_user_id", nullable = false)
    private long actorUserId;

    @Column(name = "note", length = 500)
    private String note;

    @Column(name = "detail_json", columnDefinition = "TEXT")
    private String detailJson;

    @Column(name = "created_at", nullable = false)
    private Instant createdAt;

    protected RespEvent() {
    }

    public RespEvent(long planId, Long taskId, String eventType, long actorUserId, String note, String detailJson) {
        this.planId = planId;
        this.taskId = taskId;
        this.eventType = eventType;
        this.actorUserId = actorUserId;
        this.note = note;
        this.detailJson = detailJson;
        this.createdAt = Instant.now();
    }

    public Long getId() { return id; }
    public long getPlanId() { return planId; }
    public Long getTaskId() { return taskId; }
    public String getEventType() { return eventType; }
    public long getActorUserId() { return actorUserId; }
    public String getNote() { return note; }
    public String getDetailJson() { return detailJson; }
    public Instant getCreatedAt() { return createdAt; }
}
