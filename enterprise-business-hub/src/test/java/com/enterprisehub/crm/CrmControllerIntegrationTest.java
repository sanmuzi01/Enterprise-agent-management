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

/** CRM 闭环的真实 HTTP 集成测试，跟 Leave/Procurement 是同一套骨架——签名要求见
 * LeaveControllerIntegrationTest 类注释里 P1-8 的说明。 */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
@ActiveProfiles("test")
class CrmControllerIntegrationTest {

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
    private long customerId;
    private long otherTeamCustomerId;

    @BeforeEach
    void setUp() {
        userId = ThreadLocalRandom.current().nextLong(7_000_000L, 7_999_000L);
        teamId = userId;

        jdbc.update("INSERT INTO customer (name, industry, owner_user_id, team_id, created_at) "
                + "VALUES (?, '制造业', ?, ?, NOW())", "测试客户-" + userId, userId, teamId);
        customerId = jdbc.queryForObject("SELECT id FROM customer WHERE team_id=? ORDER BY id DESC LIMIT 1",
                Long.class, teamId);
        jdbc.update("INSERT INTO contact (customer_id, name, title, phone, email) VALUES (?, ?, ?, ?, ?)",
                customerId, "张经理", "采购总监", "13800000000", "zhang@example.com");

        jdbc.update("INSERT INTO customer (name, industry, owner_user_id, team_id, created_at) "
                + "VALUES (?, '零售业', ?, ?, NOW())", "别的部门客户-" + userId, userId, teamId + 1);
        otherTeamCustomerId = jdbc.queryForObject(
                "SELECT id FROM customer WHERE team_id=? ORDER BY id DESC LIMIT 1", Long.class, teamId + 1);
    }

    @AfterEach
    void tearDown() {
        jdbc.update("DELETE FROM opportunity WHERE customer_id IN (?, ?)", customerId, otherTeamCustomerId);
        jdbc.update("DELETE FROM follow_up WHERE customer_id IN (?, ?)", customerId, otherTeamCustomerId);
        jdbc.update("DELETE FROM contact WHERE customer_id IN (?, ?)", customerId, otherTeamCustomerId);
        jdbc.update("DELETE FROM customer WHERE id IN (?, ?)", customerId, otherTeamCustomerId);
        jdbc.update("DELETE FROM audit_event WHERE user_id = ?", userId);
    }

    private String writeJson(Object obj) {
        try {
            return MAPPER.writeValueAsString(obj);
        } catch (Exception e) {
            throw new RuntimeException(e);
        }
    }

    private static String sha256Hex(byte[] bytes) {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            return HexFormat.of().formatHex(digest.digest(bytes));
        } catch (Exception e) {
            throw new RuntimeException(e);
        }
    }

    /** {@code bodyJson} 为 null 表示这次请求没有请求体。 */
    private HttpHeaders signedHeaders(HttpMethod method, String path, String bodyJson, long uid,
                                       Long teamIdForContext, List<String> scopes, String operation) {
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
            String sigHex = HexFormat.of().formatHex(
                    mac.doFinal(contextB64.getBytes(StandardCharsets.UTF_8)));
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
    void fullHappyPath_summary_followup_confirm_opportunity() {
        String summaryPath = "/crm/customers/" + customerId;
        ResponseEntity<Map> summary = rest.exchange(url(summaryPath), HttpMethod.GET,
                new HttpEntity<>(signedHeaders(HttpMethod.GET, summaryPath, null, userId, teamId,
                        List.of("crm.read"), "get_customer_summary")), Map.class);
        assertThat(summary.getStatusCode()).isEqualTo(HttpStatus.OK);
        List<?> contacts = (List<?>) summary.getBody().get("contacts");
        assertThat(contacts).hasSize(1);

        String followupsPath = "/crm/customers/" + customerId + "/followups";
        String draftBodyJson = writeJson(Map.of("content", "拜访了张经理，对方案感兴趣"));
        HttpHeaders writeHeaders = signedHeaders(HttpMethod.POST, followupsPath, draftBodyJson, userId, teamId,
                List.of("crm.write"), "create_followup_draft");
        writeHeaders.set("Idempotency-Key", UUID.randomUUID().toString());
        ResponseEntity<Map> draft = rest.exchange(url(followupsPath),
                HttpMethod.POST, new HttpEntity<>(draftBodyJson, writeHeaders), Map.class);
        assertThat(draft.getBody().get("status")).isEqualTo("DRAFT");
        long followUpId = ((Number) draft.getBody().get("id")).longValue();

        String confirmPath = "/crm/followups/" + followUpId + "/confirm";
        HttpHeaders confirmHeaders = signedHeaders(HttpMethod.POST, confirmPath, null, userId, teamId,
                List.of("crm.write"), "submit_customer_followup");
        confirmHeaders.set("Idempotency-Key", UUID.randomUUID().toString());
        ResponseEntity<Map> confirmed = rest.exchange(url(confirmPath),
                HttpMethod.POST, new HttpEntity<>(confirmHeaders), Map.class);
        assertThat(confirmed.getBody().get("status")).isEqualTo("CONFIRMED");

        String opportunitiesPath = "/crm/customers/" + customerId + "/opportunities";
        String createOppBodyJson = writeJson(Map.of("stage", "QUALIFIED", "amount", 50000));
        HttpHeaders oppHeaders = signedHeaders(HttpMethod.POST, opportunitiesPath, createOppBodyJson, userId, teamId,
                List.of("crm.write"), "create_or_update_opportunity");
        oppHeaders.set("Idempotency-Key", UUID.randomUUID().toString());
        ResponseEntity<Map> created = rest.exchange(url(opportunitiesPath),
                HttpMethod.POST, new HttpEntity<>(createOppBodyJson, oppHeaders), Map.class);
        assertThat(created.getBody().get("stage")).isEqualTo("QUALIFIED");
        long opportunityId = ((Number) created.getBody().get("id")).longValue();

        String updateOppBodyJson = writeJson(
                Map.of("opportunityId", opportunityId, "stage", "NEGOTIATION", "amount", 60000));
        HttpHeaders updateHeaders = signedHeaders(HttpMethod.POST, opportunitiesPath, updateOppBodyJson, userId,
                teamId, List.of("crm.write"), "create_or_update_opportunity");
        updateHeaders.set("Idempotency-Key", UUID.randomUUID().toString());
        ResponseEntity<Map> updated = rest.exchange(url(opportunitiesPath), HttpMethod.POST,
                new HttpEntity<>(updateOppBodyJson, updateHeaders), Map.class);
        assertThat(updated.getBody().get("id")).isEqualTo((int) opportunityId);
        assertThat(updated.getBody().get("stage")).isEqualTo("NEGOTIATION");

        HttpHeaders listHeaders = signedHeaders(HttpMethod.GET, opportunitiesPath, null, userId, teamId,
                List.of("crm.read"), "list");
        ResponseEntity<List> opportunities = rest.exchange(url(opportunitiesPath),
                HttpMethod.GET, new HttpEntity<>(listHeaders), List.class);
        assertThat(opportunities.getBody()).hasSize(1); // 更新了同一条，不是新建了一条
    }

    @Test
    void differentTeam_customerNotFound() {
        // customerId 属于 teamId，用 teamId+1 的上下文去查会被当成不存在（部门数据隔离）。
        String path = "/crm/customers/" + customerId;
        ResponseEntity<String> resp = rest.exchange(url(path), HttpMethod.GET,
                new HttpEntity<>(signedHeaders(HttpMethod.GET, path, null, userId, teamId + 1,
                        List.of("crm.read"), "get_customer_summary")),
                String.class);
        assertThat(resp.getStatusCode()).isEqualTo(HttpStatus.NOT_FOUND);
    }

    @Test
    void missingScope_returns403() {
        String path = "/crm/customers/" + customerId;
        ResponseEntity<String> resp = rest.exchange(url(path), HttpMethod.GET,
                new HttpEntity<>(signedHeaders(HttpMethod.GET, path, null, userId, teamId,
                        List.of("other.scope"), "get_customer_summary")),
                String.class);
        assertThat(resp.getStatusCode()).isEqualTo(HttpStatus.FORBIDDEN);
    }

    @Test
    void tamperedBodyAfterSigning_returns401() {
        // P1-8（第四轮审计），同 Leave/Procurement 测试类的说明：POST + 401 会撞
        // JDK HttpURLConnection 的已知限制，两种客户端表现都算通过，真正要证明的
        // 是"没有被当成合法请求处理"，用数据库状态确认更可靠。
        String path = "/crm/customers/" + customerId + "/followups";
        String signedBodyJson = writeJson(Map.of("content", "原始跟进内容"));
        HttpHeaders headers = signedHeaders(HttpMethod.POST, path, signedBodyJson, userId, teamId,
                List.of("crm.write"), "create_followup_draft");
        headers.set("Idempotency-Key", UUID.randomUUID().toString());
        String tamperedBodyJson = writeJson(Map.of("content", "被篡改的跟进内容"));
        try {
            ResponseEntity<String> resp = rest.exchange(url(path), HttpMethod.POST,
                    new HttpEntity<>(tamperedBodyJson, headers), String.class);
            assertThat(resp.getStatusCode()).isEqualTo(HttpStatus.UNAUTHORIZED);
        } catch (org.springframework.web.client.ResourceAccessException e) {
            assertThat(e.getMessage()).contains("server authentication", "streaming mode");
        }
        Integer count = jdbc.queryForObject(
                "SELECT COUNT(*) FROM follow_up WHERE customer_id = ?", Integer.class, customerId);
        assertThat(count).isEqualTo(0);
    }

    @Test
    void replayingIdempotencyKey_doesNotCreateSecondFollowUp() {
        String path = "/crm/customers/" + customerId + "/followups";
        String key = UUID.randomUUID().toString();
        String bodyJson = writeJson(Map.of("content", "第一次跟进"));
        HttpHeaders writeHeaders = signedHeaders(HttpMethod.POST, path, bodyJson, userId, teamId,
                List.of("crm.write"), "create_followup_draft");
        writeHeaders.set("Idempotency-Key", key);

        ResponseEntity<Map> first = rest.exchange(url(path),
                HttpMethod.POST, new HttpEntity<>(bodyJson, writeHeaders), Map.class);
        HttpHeaders writeHeaders2 = signedHeaders(HttpMethod.POST, path, bodyJson, userId, teamId,
                List.of("crm.write"), "create_followup_draft");
        writeHeaders2.set("Idempotency-Key", key);
        ResponseEntity<Map> second = rest.exchange(url(path),
                HttpMethod.POST, new HttpEntity<>(bodyJson, writeHeaders2), Map.class);

        assertThat(second.getBody().get("id")).isEqualTo(first.getBody().get("id"));
        Integer count = jdbc.queryForObject(
                "SELECT COUNT(*) FROM follow_up WHERE customer_id = ?", Integer.class, customerId);
        assertThat(count).isEqualTo(1);
    }
}
