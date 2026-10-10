package com.enterprisehub.oa;

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

import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.Instant;
import java.util.*;
import java.util.concurrent.ThreadLocalRandom;

import static org.assertj.core.api.Assertions.assertThat;

/** /oa/leave/requests/approved：考勤异常判断用的“已批准请假”只读接口——只返回签名路径里 scopeTeamIds 范围内、与区间重叠的已批准请假。 */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
@ActiveProfiles("test")
class LeaveApprovedEndpointIntegrationTest {
    private static final String HMAC_SECRET = "test-secret-not-for-production";
    private static final ObjectMapper MAPPER = new ObjectMapper();

    @LocalServerPort
    private int port;
    @Autowired
    private TestRestTemplate rest;
    @Autowired
    private JdbcTemplate jdbc;

    private long user, team, otherTeam;

    @BeforeEach
    void setUp() {
        user = ThreadLocalRandom.current().nextLong(8_000_000L, 8_900_000L);
        team = ThreadLocalRandom.current().nextLong(30_000L, 39_000L);
        otherTeam = team + 1;
    }

    @AfterEach
    void tearDown() {
        jdbc.update("DELETE FROM leave_request WHERE applicant_user_id IN (?, ?)", user, user + 1);
    }

    private void insert(long applicant, long teamId, String start, String end, String status) {
        long type = jdbc.queryForObject("SELECT id FROM leave_type WHERE code='annual'", Long.class);
        jdbc.update("INSERT INTO leave_request (applicant_user_id, team_id, leave_type_id, start_date, end_date, days, status, created_at) "
                + "VALUES (?, ?, ?, ?, ?, 1, ?, UTC_TIMESTAMP())", applicant, teamId, type, start, end, status);
    }

    private ResponseEntity<String> get(String path, List<String> scopes) {
        try {
            Map<String, Object> ctx = new HashMap<>();
            ctx.put("user_id", user);
            ctx.put("team_id", team);
            ctx.put("scopes", scopes);
            ctx.put("operation", "leave_approved_test");
            ctx.put("is_org_admin", false);
            ctx.put("is_team_admin", false);
            ctx.put("trace_id", UUID.randomUUID().toString());
            ctx.put("timestamp", Instant.now().getEpochSecond());
            ctx.put("nonce", UUID.randomUUID().toString().replace("-", ""));
            ctx.put("method", "GET");
            ctx.put("path", path);
            ctx.put("body_sha256", HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(new byte[0])));
            String b64 = Base64.getEncoder().encodeToString(MAPPER.writeValueAsString(ctx).getBytes(StandardCharsets.UTF_8));
            javax.crypto.Mac mac = javax.crypto.Mac.getInstance("HmacSHA256");
            mac.init(new javax.crypto.spec.SecretKeySpec(HMAC_SECRET.getBytes(StandardCharsets.UTF_8), "HmacSHA256"));
            HttpHeaders headers = new HttpHeaders();
            headers.set("X-Context", b64);
            headers.set("X-Signature", HexFormat.of().formatHex(mac.doFinal(b64.getBytes(StandardCharsets.UTF_8))));
            return rest.exchange(URI.create("http://127.0.0.1:" + port + path), HttpMethod.GET, new HttpEntity<>(headers), String.class);
        } catch (Exception e) {
            throw new RuntimeException(e);
        }
    }

    @SuppressWarnings("unchecked")
    private List<Map<String, Object>> list(ResponseEntity<String> r) throws Exception {
        return MAPPER.readValue(r.getBody(), List.class);
    }

    @Test
    void returnsOnlyApprovedOverlappingLeaveInScope() throws Exception {
        insert(user, team, "2026-10-05", "2026-10-07", "APPROVED");        // 与区间重叠
        insert(user + 1, team, "2026-09-01", "2026-09-02", "APPROVED");    // 区间之外
        insert(user + 1, team, "2026-10-06", "2026-10-06", "SUBMITTED");   // 还没批准
        insert(user + 1, team, "2026-10-06", "2026-10-06", "REJECTED");
        insert(user + 1, otherTeam, "2026-10-06", "2026-10-06", "APPROVED");   // 不在范围内的部门
        ResponseEntity<String> r = get("/oa/leave/requests/approved?scopeTeamIds=" + team + "&from=2026-10-06&to=2026-10-20", List.of("oa.leave.read"));
        assertThat(r.getStatusCode()).isEqualTo(HttpStatus.OK);
        List<Map<String, Object>> rows = list(r);
        assertThat(rows).hasSize(1);
        assertThat(((Number) rows.get(0).get("applicantUserId")).longValue()).isEqualTo(user);
        assertThat(rows.get(0)).containsEntry("leaveTypeCode", "annual").containsEntry("status", "APPROVED");
        // 范围放宽到两个部门，才看得到另一个部门的
        assertThat(list(get("/oa/leave/requests/approved?scopeTeamIds=" + team + "," + otherTeam + "&from=2026-10-06&to=2026-10-20", List.of("oa.leave.read")))).hasSize(2);
    }

    @Test
    void needsScopeAndAValidRangeAndTheReadPermission() {
        assertThat(get("/oa/leave/requests/approved?from=2026-10-06&to=2026-10-20", List.of("oa.leave.read")).getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
        assertThat(get("/oa/leave/requests/approved?scopeTeamIds=" + team + "&from=2026-10-20&to=2026-10-06", List.of("oa.leave.read")).getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
        assertThat(get("/oa/leave/requests/approved?scopeTeamIds=" + team + "&from=2026-01-01&to=2026-12-31", List.of("oa.leave.read")).getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
        assertThat(get("/oa/leave/requests/approved?scopeTeamIds=" + team + "&from=2026-10-06&to=2026-10-20", List.of("oa.leave.write")).getStatusCode()).isEqualTo(HttpStatus.FORBIDDEN);
    }
}
