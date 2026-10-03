package com.enterprisehub.it;

import jakarta.persistence.*;

import java.time.Instant;

@Entity
@Table(name = "it_device_event")
public class ItDeviceEvent {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "device_id", nullable = false)
    private long deviceId;

    @Column(name = "event_type", nullable = false, length = 20)
    private String eventType;

    @Column(name = "actor_user_id", nullable = false)
    private long actorUserId;

    @Column(name = "subject_user_id")
    private Long subjectUserId;

    @Column(name = "ticket_id")
    private Long ticketId;

    @Column(name = "note", length = 300)
    private String note;

    @Column(name = "created_at", nullable = false)
    private Instant createdAt;

    protected ItDeviceEvent() {
    }

    public ItDeviceEvent(long deviceId, String eventType, long actorUserId, Long subjectUserId, Long ticketId,
                         String note) {
        this.deviceId = deviceId;
        this.eventType = eventType;
        this.actorUserId = actorUserId;
        this.subjectUserId = subjectUserId;
        this.ticketId = ticketId;
        this.note = note;
        this.createdAt = Instant.now();
    }

    public long getDeviceId() { return deviceId; }
    public String getEventType() { return eventType; }
    public long getActorUserId() { return actorUserId; }
    public Long getSubjectUserId() { return subjectUserId; }
    public Long getTicketId() { return ticketId; }
    public String getNote() { return note; }
    public Instant getCreatedAt() { return createdAt; }
}
