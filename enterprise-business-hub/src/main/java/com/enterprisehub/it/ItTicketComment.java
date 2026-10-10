package com.enterprisehub.it;

import jakarta.persistence.*;

import java.time.Instant;

@Entity
@Table(name = "it_ticket_comment")
public class ItTicketComment {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "ticket_id", nullable = false)
    private long ticketId;

    @Column(name = "author_user_id", nullable = false)
    private long authorUserId;

    @Column(name = "internal", nullable = false)
    private boolean internal;

    @Column(name = "kind", nullable = false, length = 20)
    private String kind;

    @Column(name = "body", nullable = false, length = 2000)
    private String body;

    @Column(name = "created_at", nullable = false)
    private Instant createdAt;

    protected ItTicketComment() {
    }

    public ItTicketComment(long ticketId, long authorUserId, boolean internal, String kind, String body) {
        this.ticketId = ticketId;
        this.authorUserId = authorUserId;
        this.internal = internal;
        this.kind = kind;
        this.body = body.length() > 2000 ? body.substring(0, 2000) : body;
        this.createdAt = Instant.now();
    }

    public Long getId() { return id; }
    public long getTicketId() { return ticketId; }
    public long getAuthorUserId() { return authorUserId; }
    public boolean isInternal() { return internal; }
    public String getKind() { return kind; }
    public String getBody() { return body; }
    public Instant getCreatedAt() { return createdAt; }
}
