package com.enterprisehub.idempotency;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;

import java.time.Instant;

/** 写操作的幂等记录：同一个 `Idempotency-Key` 第二次打过来，直接把第一次的结果原样返回，
 * 不重新执行业务逻辑——防止网络重试/用户连点造成重复请假单/重复采购单。
 *
 * {@code completed=false} 是"占位"阶段（{@link IdempotencyService} 刚抢到这个 key，
 * 业务逻辑还没跑完），{@code statusCode}/{@code responseBody} 这时候是 null；
 * {@code completed=true} 才是真正的结果，可以原样返回给重复请求。 */
@Entity
@Table(name = "idempotency_record")
public class IdempotencyRecord {
    @Id
    @Column(name = "idempotency_key", length = 100)
    private String idempotencyKey;

    @Column(name = "status_code")
    private Integer statusCode;

    // 同 AuditEvent.detail 那条注释：不用 @Lob，避免 Hibernate 默认类型映射跟
    // Flyway 建的 MEDIUMTEXT 不一致。
    @Column(name = "response_body", columnDefinition = "MEDIUMTEXT")
    private String responseBody;

    @Column(name = "completed", nullable = false)
    private boolean completed;

    @Column(name = "created_at", nullable = false)
    private Instant createdAt;

    protected IdempotencyRecord() {
    }

    /** 占位记录：抢到 key 但业务逻辑还没跑完。 */
    public IdempotencyRecord(String idempotencyKey) {
        this.idempotencyKey = idempotencyKey;
        this.completed = false;
        this.createdAt = Instant.now();
    }

    public void markCompleted(int statusCode, String responseBody) {
        this.statusCode = statusCode;
        this.responseBody = responseBody;
        this.completed = true;
    }

    public String getIdempotencyKey() {
        return idempotencyKey;
    }

    public Integer getStatusCode() {
        return statusCode;
    }

    public String getResponseBody() {
        return responseBody;
    }

    public boolean isCompleted() {
        return completed;
    }

    public Instant getCreatedAt() {
        return createdAt;
    }
}
