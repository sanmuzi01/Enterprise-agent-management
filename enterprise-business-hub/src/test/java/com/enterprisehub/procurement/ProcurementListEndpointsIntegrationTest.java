package com.enterprisehub.procurement;

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
 * 部门工作台里程碑2新增：/procurement/requests/mine 和 /procurement/requests/team-pending
 * 两个列表接口——跟请假模块不同，{@link PurchaseRequestRepository} 之前是空壳，这两个查询
 * 方法是从零新增的，不是"接死代码"。跟 {@link ProcurementControllerIntegrationTest} 是同一套
 * 真实 HTTP + 真实 MySQL 集成测试，helper 方法故意重新写一份，不共享（跟本项目其它测试类
 * 的既有约定一致）。ID 段用 7_000_000L-7_999_000L，避开 ProcurementControllerIntegrationTest
 * 的 8_000_000L-8_999_000L。
 */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
@ActiveProfiles("test")
class ProcurementListEndpointsIntegrationTest {

    private static final String HMAC_SECRET = "test-secret-not-for-production";
    private static final ObjectMapper MAPPER = new ObjectMapper();

    @LocalServerPort
    private int port;

    @Autowired
    private TestRestTemplate rest;

    @Autowired
    private JdbcTemplate jdbc;

    private long userId;
    private long approverId;
    private long teamId;
    private long otherTeamId;
    private String sku;

    @BeforeEach
    void setUp() {
        userId = ThreadLocalRandom.current().nextLong(7_000_000L, 7_499_000L);
        approverId = userId + 1;
        teamId = userId;
        otherTeamId = userId + 500_000L;
        sku = "SKU-LIST-" + userId;

        jdbc.update("INSERT INTO product (sku, name, unit, unit_price, on_hand_qty, safety_stock_qty, supplier_code) "
                + "VALUES (?, ?, '个', 10.00, 100, 5, 'SUP-001')", sku, "列表测试产品-" + userId);
        seedBudget(teamId);
        seedBudget(otherTeamId);
    }

    @AfterEach
    void tearDown() {
        jdbc.update("DELETE FROM purchase_order WHERE purchase_request_id IN "
                + "(SELECT id FROM purchase_request WHERE requester_user_id IN (?, ?))", userId, approverId);
        jdbc.update("DELETE FROM purchase_request_line WHERE purchase_request_id IN "
                + "(SELECT id FROM purchase_request WHERE requester_user_id IN (?, ?))", userId, approverId);
        jdbc.update("DELETE FROM purchase_request WHERE requester_user_id IN (?, ?)", userId, approverId);
        jdbc.update("DELETE FROM department_budget WHERE team_id IN (?, ?)", teamId, otherTeamId);
        jdbc.update("DELETE FROM product WHERE sku = ?", sku);
        jdbc.update("DELETE FROM audit_event WHERE user_id IN (?, ?)", userId, approverId);
    }

    private void seedBudget(long forTeamId) {
        jdbc.update("INSERT INTO department_budget (team_id, year, remaining_amount) VALUES (?, ?, 100000.00) "
                        + "ON DUPLICATE KEY UPDATE remaining_amount = 100000.00",
                forTeamId, Instant.now().atZone(java.time.ZoneOffset.UTC).getYear());
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

    private HttpHeaders signedHeaders(HttpMethod method, String path, String bodyJson, long uid, Long headerTeamId,
                                       List<String> scopes, String operation, boolean isOrgAdmin,
                                       boolean isTeamAdmin) {
        try {
            byte[] bodyBytes = bodyJson == null ? new byte[0] : bodyJson.getBytes(StandardCharsets.UTF_8);
            Map<String, Object> context = new java.util.HashMap<>();
            context.put("user_id", uid);
            context.put("team_id", headerTeamId);
            context.put("scopes", scopes);
            context.put("operation", operation);
            context.put("is_org_admin", isOrgAdmin);
            context.put("is_team_admin", isTeamAdmin);
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

    /** 建草稿 + 提交，返回采购单 id。 */
    private long createAndSubmit(long requesterUid, long forTeamId) {
        long requestId = createDraftOnly(requesterUid, forTeamId);
        String submitPath = "/procurement/requests/" + requestId + "/submit";
        HttpHeaders submitHeaders = signedHeaders(HttpMethod.POST, submitPath, null, requesterUid,
                forTeamId, List.of("procurement.write"), "submit_purchase_request", false, false);
        submitHeaders.set(ProcurementController.IDEMPOTENCY_HEADER, UUID.randomUUID().toString());
        rest.exchange(url(submitPath), HttpMethod.POST, new HttpEntity<>(submitHeaders), Map.class);
        return requestId;
    }

    private long createDraftOnly(long requesterUid, long forTeamId) {
        String requestsPath = "/procurement/requests";
        String draftBodyJson = writeJson(Map.of("lines", List.of(Map.of("sku", sku, "quantity", 1))));
        HttpHeaders writeHeaders = signedHeaders(HttpMethod.POST, requestsPath, draftBodyJson, requesterUid,
                forTeamId, List.of("procurement.write"), "create_purchase_draft", false, false);
        writeHeaders.set(ProcurementController.IDEMPOTENCY_HEADER, UUID.randomUUID().toString());
        ResponseEntity<Map> draftResp = rest.exchange(url(requestsPath), HttpMethod.POST,
                new HttpEntity<>(draftBodyJson, writeHeaders), Map.class);
        return ((Number) draftResp.getBody().get("id")).longValue();
    }

    @Test
    void myRequests_returnsOnlyOwnRequests_orderedNewestFirst() {
        long myId = createAndSubmit(userId, teamId);
        long myId2 = createAndSubmit(userId, teamId);
        createAndSubmit(approverId, teamId); // 同一个部门，但申请人不同

        String path = "/procurement/requests/mine";
        HttpHeaders headers = signedHeaders(HttpMethod.GET, path, null, userId, teamId,
                List.of("procurement.read"), "get_my_purchase_requests", false, false);
        ResponseEntity<Map[]> resp = rest.exchange(url(path), HttpMethod.GET, new HttpEntity<>(headers), Map[].class);

        assertThat(resp.getStatusCode()).isEqualTo(HttpStatus.OK);
        List<Long> ids = List.of(resp.getBody()).stream().map(m -> ((Number) m.get("id")).longValue()).toList();
        assertThat(ids).containsExactly(myId2, myId); // 按创建时间倒序，且只有自己的两条
    }

    @Test
    void myRequests_emptyList_whenNoRequests() {
        String path = "/procurement/requests/mine";
        HttpHeaders headers = signedHeaders(HttpMethod.GET, path, null, userId, teamId,
                List.of("procurement.read"), "get_my_purchase_requests", false, false);
        ResponseEntity<Map[]> resp = rest.exchange(url(path), HttpMethod.GET, new HttpEntity<>(headers), Map[].class);

        assertThat(resp.getStatusCode()).isEqualTo(HttpStatus.OK);
        assertThat(resp.getBody()).isEmpty();
    }

    @Test
    void teamPending_excludesOtherTeams_andExcludesNonSubmittedStatuses() {
        // 核心断言：别的部门的单子、还没提交的草稿，都不该出现在结果里——不是只看
        // "自己部门的在不在"，这样以后过滤条件被误删/写错，测试真的会失败。
        long pendingInMyTeam = createAndSubmit(userId, teamId);
        createAndSubmit(userId, otherTeamId); // 同一个申请人，但部门不同
        createDraftOnly(userId, teamId); // 同部门但状态是 DRAFT，不该出现

        String path = "/procurement/requests/team-pending?teamId=" + teamId;
        HttpHeaders headers = signedHeaders(HttpMethod.GET, path, null, approverId, teamId,
                List.of("procurement.read"), "get_team_pending_purchase_requests", false, true);
        ResponseEntity<Map[]> resp = rest.exchange(url(path), HttpMethod.GET, new HttpEntity<>(headers), Map[].class);

        assertThat(resp.getStatusCode()).isEqualTo(HttpStatus.OK);
        List<Long> ids = List.of(resp.getBody()).stream().map(m -> ((Number) m.get("id")).longValue()).toList();
        assertThat(ids).containsExactly(pendingInMyTeam);
    }

    @Test
    void teamPending_regularMemberWithoutTeamAdminRole_returns404() {
        createAndSubmit(userId, teamId);
        String path = "/procurement/requests/team-pending?teamId=" + teamId;
        HttpHeaders headers = signedHeaders(HttpMethod.GET, path, null, approverId, teamId,
                List.of("procurement.read"), "get_team_pending_purchase_requests", false, false); // 有 scope，但不是负责人
        ResponseEntity<String> resp = rest.exchange(url(path), HttpMethod.GET, new HttpEntity<>(headers), String.class);
        assertThat(resp.getStatusCode()).isEqualTo(HttpStatus.NOT_FOUND);
    }

    @Test
    void teamPending_teamAdminOfDifferentDepartment_returns404() {
        createAndSubmit(userId, teamId);
        String path = "/procurement/requests/team-pending?teamId=" + teamId;
        // approverId 是 otherTeamId 的负责人，不是 teamId 的负责人——不能查 teamId 的待审批。
        HttpHeaders headers = signedHeaders(HttpMethod.GET, path, null, approverId, otherTeamId,
                List.of("procurement.read"), "get_team_pending_purchase_requests", false, true);
        ResponseEntity<String> resp = rest.exchange(url(path), HttpMethod.GET, new HttpEntity<>(headers), String.class);
        assertThat(resp.getStatusCode()).isEqualTo(HttpStatus.NOT_FOUND);
    }

    @Test
    void teamPending_orgAdminCanQueryAnyTeam_viaTeamIdParam() {
        long pending = createAndSubmit(userId, teamId);
        String path = "/procurement/requests/team-pending?teamId=" + teamId;
        HttpHeaders headers = signedHeaders(HttpMethod.GET, path, null, approverId, null,
                List.of("procurement.read"), "get_team_pending_purchase_requests", true, false); // 企业管理员，不属于任何具体部门
        ResponseEntity<Map[]> resp = rest.exchange(url(path), HttpMethod.GET, new HttpEntity<>(headers), Map[].class);

        assertThat(resp.getStatusCode()).isEqualTo(HttpStatus.OK);
        List<Long> ids = List.of(resp.getBody()).stream().map(m -> ((Number) m.get("id")).longValue()).toList();
        assertThat(ids).containsExactly(pending);
    }
}
