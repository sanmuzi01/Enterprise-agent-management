package com.enterprisehub.audit;

import com.zaxxer.hikari.HikariDataSource;
import jakarta.annotation.PreDestroy;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;

import java.sql.Timestamp;
import java.time.Instant;

/**
 * 业务审计：只追加，没有 update/delete 方法——跟 FastAPI 侧 audit_event 是同一个模式，
 * 分别落在各自的库里（这边归业务中心自己管，不共库）。
 *
 * 写入走独立的 HikariDataSource/JdbcTemplate（自己 new 出来，不注册成 Spring
 * {@code DataSource}/{@code JdbcTemplate} bean），接一个只被授予 audit_event 表
 * INSERT/SELECT 权限的 MySQL 账号（建号脚本见
 * deploy/mysql-init/02-create-audit-user.sh）——不能复用主 JPA 数据源，那个账号
 * 对所有表都有完整读写权限，等于审计账号也能改/删自己写过的记录。不注册成
 * Spring bean是为了不触发 {@code DataSourceAutoConfiguration}/
 * {@code JdbcTemplateAutoConfiguration} 的 {@code @ConditionalOnMissingBean}
 * 退让逻辑，避免影响主数据源和现有测试里未加限定符的 {@code JdbcTemplate} 注入。
 * 见 docs/enterprise-rbac-plan.md 第21节。
 *
 * 本地开发/测试没有单独建这个账号时，audit.datasource.username/password
 * （application.yml）退回主账号，不是新增的硬依赖。
 */
@Service
public class AuditService {

    private static final String INSERT_SQL =
            "INSERT INTO audit_event (user_id, action, resource_type, resource_id, detail, trace_id, created_at) "
                    + "VALUES (?, ?, ?, ?, ?, ?, ?)";

    private final HikariDataSource auditDataSource;
    private final JdbcTemplate auditJdbcTemplate;

    public AuditService(
            @Value("${spring.datasource.url}") String jdbcUrl,
            @Value("${audit.datasource.username}") String username,
            @Value("${audit.datasource.password}") String password) {
        HikariDataSource ds = new HikariDataSource();
        ds.setJdbcUrl(jdbcUrl);
        ds.setUsername(username);
        ds.setPassword(password);
        // 审计写入量小、只追加，不需要跟主业务连接池一样大。
        ds.setMaximumPoolSize(3);
        ds.setPoolName("audit-writer");
        this.auditDataSource = ds;
        this.auditJdbcTemplate = new JdbcTemplate(ds);
    }

    public void record(long userId, String action, String resourceType, Long resourceId, String detail, String traceId) {
        auditJdbcTemplate.update(INSERT_SQL, userId, action, resourceType, resourceId, detail, traceId,
                Timestamp.from(Instant.now()));
    }

    @PreDestroy
    void shutdown() {
        auditDataSource.close();
    }
}
