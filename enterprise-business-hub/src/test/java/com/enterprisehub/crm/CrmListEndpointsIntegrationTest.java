package com.enterprisehub.crm;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.client.TestRestTemplate;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpMethod;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.ActiveProfiles;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.Instant;
import java.util.Base64;
import java.util.HexFormat;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ThreadLocalRandom;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * 部门工作台里程碑3新增：GET /crm/customers 客户列表接口——跟采购模块一样，
 * {@link CustomerRepository} 之前是空壳，这个查询方法是从零新增的，不是"接死代码"。
 * CRM 没有审批/负责人概念（全程只有 crm.read/crm.write 两个 scope），所以这里没有
 * 类似 team-pending 那种权限边界测试，核心只验证"部门隔离"。ID 段用
 * 6_000_000L-6_999_000L，避开 CrmControllerIntegrationTest/ProcurementControllerIntegrationTest
 * 用的 7_000_000L 段。
 */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
@ActiveProfiles("test")
class CrmListEndpointsIntegrationTest {

    private static final String HMAC_SECRET = "test-secret-not-for-production";
    private static final ObjectMapper MAPPER = new ObjectMapper();

    @LocalServerPort
    private int port;

    @Autowired
    private TestRestTemplate rest;

    @Autowired
    private JdbcTemplate jdbc;

    private long userId;
    private long teamId;
    private long otherTeamId;

    @BeforeEach
    void setUp() {
        userId = ThreadLocalRandom.current().nextLong(6_000_000L, 6_999_000L);
        teamId = userId;
        otherTeamId = userId + 500_000L;
    }

    @AfterEach
    void tearDown() {
        jdbc.update("DELETE FROM customer WHERE team_id IN (?, ?)", teamId, otherTeamId);
        jdbc.update("DELETE FROM audit_event WHERE user_id = ?", userId);
    }

    // customer.created_at 是 DATETIME（秒级精度，见 V3__init_crm.sql），两次紧邻的
    // NOW() 插入很容易落在同一秒里，导致 ORDER BY created_at DESC 的相对顺序变得
    // 不确定——显式传入相差超过 1 秒的时间戳，不依赖真实时间流逝。
    private void seedCustomer(long forTeamId, String name, Instant createdAt) {
        jdbc.update("INSERT INTO customer (name, industry, owner_user_id, team_id, created_at) "
                + "VALUES (?, '制造业', ?, ?, ?)", name, userId, forTeamId, java.sql.Timestamp.from(createdAt));
    }

    private static String sha256Hex(byte[] bytes) {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            return HexFormat.of().formatHex(digest.digest(bytes));
        } catch (Exception e) {
            throw new RuntimeException(e);
        }
    }

    private HttpHeaders signedHeaders(HttpMethod method, String path, String bodyJson, long uid, Long teamIdForContext,
                                       List<String> scopes, String operation) {
        try {
            byte[] bodyBytes = bodyJson == null ? new byte[0] : bodyJson.getBytes(StandardCharsets.UTF_8);
            Map<String, Object> context = new java.util.HashMap<>();
            context.put("user_id", uid);
            context.put("team_id", teamIdForContext);
            context.put("scopes", scopes);
            context.put("operation", operation);
            context.put("trace_id", UUID.randomUUID().toString());
            context.put("timestamp", Instant.now().getEpochSecond());
            context.put("nonce", UUID.randomUUID().toString().replace("-", ""));
            context.put("method", method.name());
            context.put("path", path);
            context.put("body_sha256", sha256Hex(bodyBytes));
            String json = MAPPER.writeValueAsString(context);
            String contextB64 = Base64.getEncoder().encodeToString(json.getBytes(StandardCharsets.UTF_8));
            javax.crypto.Mac mac = javax.crypto.Mac.getInstance("HmacSHA256");
            mac.init(new javax.crypto.spec.SecretKeySpec(HMAC_SECRET.getBytes(StandardCharsets.UTF_8), "HmacSHA256"));
            String sigHex = HexFormat.of().formatHex(mac.doFinal(contextB64.getBytes(StandardCharsets.UTF_8)));
            HttpHeaders headers = new HttpHeaders();
            headers.set("X-Context", contextB64);
            headers.set("X-Signature", sigHex);
            headers.setContentType(org.springframework.http.MediaType.APPLICATION_JSON);
            return headers;
        } catch (Exception e) {
            throw new RuntimeException(e);
        }
    }

    private String url(String path) {
        return "http://127.0.0.1:" + port + path;
    }

    @Test
    void listCustomers_returnsOnlyOwnTeamCustomers_orderedNewestFirst() {
        Instant now = Instant.now();
        seedCustomer(teamId, "本部门客户A", now.minusSeconds(10));
        seedCustomer(teamId, "本部门客户B", now);
        seedCustomer(otherTeamId, "别的部门客户", now); // 不该出现

        String path = "/crm/customers";
        HttpHeaders headers = signedHeaders(HttpMethod.GET, path, null, userId, teamId,
                List.of("crm.read"), "list_team_customers");
        ResponseEntity<Map[]> resp = rest.exchange(url(path), HttpMethod.GET, new HttpEntity<>(headers), Map[].class);

        assertThat(resp.getStatusCode()).isEqualTo(HttpStatus.OK);
        List<String> names = List.of(resp.getBody()).stream().map(m -> (String) m.get("name")).toList();
        assertThat(names).containsExactly("本部门客户B", "本部门客户A"); // 新建的排前面
    }

    @Test
    void listCustomers_emptyList_whenNoCustomers() {
        String path = "/crm/customers";
        HttpHeaders headers = signedHeaders(HttpMethod.GET, path, null, userId, teamId,
                List.of("crm.read"), "list_team_customers");
        ResponseEntity<Map[]> resp = rest.exchange(url(path), HttpMethod.GET, new HttpEntity<>(headers), Map[].class);

        assertThat(resp.getStatusCode()).isEqualTo(HttpStatus.OK);
        assertThat(resp.getBody()).isEmpty();
    }

    @Test
    void listCustomers_missingScope_returns403() {
        seedCustomer(teamId, "本部门客户", Instant.now());
        String path = "/crm/customers";
        HttpHeaders headers = signedHeaders(HttpMethod.GET, path, null, userId, teamId,
                List.of("other.scope"), "list_team_customers");
        ResponseEntity<String> resp = rest.exchange(url(path), HttpMethod.GET, new HttpEntity<>(headers), String.class);
        assertThat(resp.getStatusCode()).isEqualTo(HttpStatus.FORBIDDEN);
    }
}
