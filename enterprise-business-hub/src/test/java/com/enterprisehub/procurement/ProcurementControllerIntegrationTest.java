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
import java.time.Instant;
import java.util.Base64;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ThreadLocalRandom;

import static org.assertj.core.api.Assertions.assertThat;

/** 采购闭环的真实 HTTP 集成测试，跟 LeaveControllerIntegrationTest 是同一套骨架
 * （签名 header + 真实 MySQL），只是换了业务域。 */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
@ActiveProfiles("test")
class ProcurementControllerIntegrationTest {

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
    private String sku;

    @BeforeEach
    void setUp() {
        userId = ThreadLocalRandom.current().nextLong(8_000_000L, 8_999_000L);
        approverId = userId + 1;
        teamId = userId; // 每个测试自己的 team_id 段，避免跟别的测试撞预算行
        sku = "SKU-" + userId;

        jdbc.update("INSERT INTO product (sku, name, unit, unit_price, on_hand_qty, safety_stock_qty, supplier_code) "
                + "VALUES (?, ?, '个', 100.00, 5, 20, 'SUP-001')", sku, "测试产品-" + userId);
        jdbc.update("INSERT INTO department_budget (team_id, year, remaining_amount) VALUES (?, ?, 1000.00)",
                teamId, Instant.now().atZone(java.time.ZoneOffset.UTC).getYear());
    }

    @AfterEach
    void tearDown() {
        jdbc.update("DELETE FROM purchase_order WHERE purchase_request_id IN "
                + "(SELECT id FROM purchase_request WHERE requester_user_id = ?)", userId);
        jdbc.update("DELETE FROM purchase_request_line WHERE purchase_request_id IN "
                + "(SELECT id FROM purchase_request WHERE requester_user_id = ?)", userId);
        jdbc.update("DELETE FROM purchase_request WHERE requester_user_id = ?", userId);
        jdbc.update("DELETE FROM department_budget WHERE team_id = ?", teamId);
        jdbc.update("DELETE FROM product WHERE sku = ?", sku);
        jdbc.update("DELETE FROM audit_event WHERE user_id IN (?, ?)", userId, approverId);
    }

    private HttpHeaders signedHeaders(long uid, List<String> scopes, String operation) {
        return signedHeaders(uid, teamId, scopes, operation, false, false);
    }

    private HttpHeaders signedHeaders(long uid, Long headerTeamId, List<String> scopes, String operation,
                                       boolean isOrgAdmin, boolean isTeamAdmin) {
        try {
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
            String json = MAPPER.writeValueAsString(context);
            String contextB64 = Base64.getEncoder().encodeToString(json.getBytes(StandardCharsets.UTF_8));
            javax.crypto.Mac mac = javax.crypto.Mac.getInstance("HmacSHA256");
            mac.init(new javax.crypto.spec.SecretKeySpec(HMAC_SECRET.getBytes(StandardCharsets.UTF_8), "HmacSHA256"));
            String sigHex = java.util.HexFormat.of().formatHex(
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
    void fullHappyPath_stock_budget_draft_submit_approve_deductsBudgetAndCreatesOrder() {
        ResponseEntity<Map> product = rest.exchange(url("/procurement/products/" + sku), HttpMethod.GET,
                new HttpEntity<>(signedHeaders(userId, List.of("procurement.read"), "get_inventory_status")), Map.class);
        assertThat(product.getBody().get("belowSafetyStock")).isEqualTo(true); // 5 < 20

        ResponseEntity<Map> budget = rest.exchange(url("/procurement/budget"), HttpMethod.GET,
                new HttpEntity<>(signedHeaders(userId, List.of("procurement.read"), "get_department_budget")), Map.class);
        assertThat(((Number) budget.getBody().get("remainingAmount")).doubleValue()).isEqualTo(1000.00);

        HttpHeaders writeHeaders = signedHeaders(userId, List.of("procurement.write"), "create_purchase_draft");
        writeHeaders.set("Idempotency-Key", UUID.randomUUID().toString());
        Map<String, Object> draftBody = Map.of("lines", List.of(Map.of("sku", sku, "quantity", 3)));
        ResponseEntity<Map> draft = rest.exchange(url("/procurement/requests"), HttpMethod.POST,
                new HttpEntity<>(draftBody, writeHeaders), Map.class);
        assertThat(draft.getBody().get("status")).isEqualTo("DRAFT");
        assertThat(((Number) draft.getBody().get("totalAmount")).doubleValue()).isEqualTo(300.00); // 3 * 100
        long requestId = ((Number) draft.getBody().get("id")).longValue();

        HttpHeaders submitHeaders = signedHeaders(userId, List.of("procurement.write"), "submit_purchase_request");
        submitHeaders.set("Idempotency-Key", UUID.randomUUID().toString());
        ResponseEntity<Map> submitted = rest.exchange(url("/procurement/requests/" + requestId + "/submit"),
                HttpMethod.POST, new HttpEntity<>(submitHeaders), Map.class);
        assertThat(submitted.getBody().get("status")).isEqualTo("SUBMITTED");

        HttpHeaders approveHeaders = signedHeaders(approverId, teamId, List.of("procurement.approve"),
                "approve_purchase_request", false, true);
        approveHeaders.set("Idempotency-Key", UUID.randomUUID().toString());
        ResponseEntity<Map> approved = rest.exchange(url("/procurement/requests/" + requestId + "/approve"),
                HttpMethod.POST, new HttpEntity<>(Map.of("note", "同意采购"), approveHeaders), Map.class);
        assertThat(approved.getBody().get("status")).isEqualTo("APPROVED");
        Map<?, ?> order = (Map<?, ?>) approved.getBody().get("purchaseOrder");
        assertThat(order).isNotNull();
        assertThat(order.get("supplierName")).isEqualTo("华东电子元件有限公司"); // MockSapConnector 的 SUP-001

        ResponseEntity<Map> afterBudget = rest.exchange(url("/procurement/budget"), HttpMethod.GET,
                new HttpEntity<>(signedHeaders(userId, List.of("procurement.read"), "get_department_budget")), Map.class);
        assertThat(((Number) afterBudget.getBody().get("remainingAmount")).doubleValue()).isEqualTo(700.00); // 1000-300
    }

    @Test
    void missingScope_returns403() {
        ResponseEntity<String> resp = rest.exchange(url("/procurement/products/" + sku), HttpMethod.GET,
                new HttpEntity<>(signedHeaders(userId, List.of("other.scope"), "get_inventory_status")), String.class);
        assertThat(resp.getStatusCode()).isEqualTo(HttpStatus.FORBIDDEN);
    }

    @Test
    void insufficientBudget_submitFails() {
        HttpHeaders writeHeaders = signedHeaders(userId, List.of("procurement.write"), "create_purchase_draft");
        writeHeaders.set("Idempotency-Key", UUID.randomUUID().toString());
        // 单价100 * 20 个 = 2000，超过预算 1000
        Map<String, Object> draftBody = Map.of("lines", List.of(Map.of("sku", sku, "quantity", 20)));
        ResponseEntity<Map> draft = rest.exchange(url("/procurement/requests"), HttpMethod.POST,
                new HttpEntity<>(draftBody, writeHeaders), Map.class);
        long requestId = ((Number) draft.getBody().get("id")).longValue();

        HttpHeaders submitHeaders = signedHeaders(userId, List.of("procurement.write"), "submit_purchase_request");
        submitHeaders.set("Idempotency-Key", UUID.randomUUID().toString());
        ResponseEntity<String> resp = rest.exchange(url("/procurement/requests/" + requestId + "/submit"),
                HttpMethod.POST, new HttpEntity<>(submitHeaders), String.class);
        assertThat(resp.getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
    }

    @Test
    void rejectedRequest_doesNotDeductBudgetOrCreateOrder() {
        HttpHeaders writeHeaders = signedHeaders(userId, List.of("procurement.write"), "create_purchase_draft");
        writeHeaders.set("Idempotency-Key", UUID.randomUUID().toString());
        Map<String, Object> draftBody = Map.of("lines", List.of(Map.of("sku", sku, "quantity", 2)));
        ResponseEntity<Map> draft = rest.exchange(url("/procurement/requests"), HttpMethod.POST,
                new HttpEntity<>(draftBody, writeHeaders), Map.class);
        long requestId = ((Number) draft.getBody().get("id")).longValue();

        HttpHeaders submitHeaders = signedHeaders(userId, List.of("procurement.write"), "submit_purchase_request");
        submitHeaders.set("Idempotency-Key", UUID.randomUUID().toString());
        rest.exchange(url("/procurement/requests/" + requestId + "/submit"), HttpMethod.POST,
                new HttpEntity<>(submitHeaders), Map.class);

        HttpHeaders rejectHeaders = signedHeaders(approverId, teamId, List.of("procurement.approve"),
                "reject_purchase_request", false, true);
        rejectHeaders.set("Idempotency-Key", UUID.randomUUID().toString());
        ResponseEntity<Map> rejected = rest.exchange(url("/procurement/requests/" + requestId + "/reject"),
                HttpMethod.POST, new HttpEntity<>(Map.of("note", "预算紧张"), rejectHeaders), Map.class);
        assertThat(rejected.getBody().get("status")).isEqualTo("REJECTED");
        assertThat(rejected.getBody().get("purchaseOrder")).isNull();

        ResponseEntity<Map> afterBudget = rest.exchange(url("/procurement/budget"), HttpMethod.GET,
                new HttpEntity<>(signedHeaders(userId, List.of("procurement.read"), "get_department_budget")), Map.class);
        assertThat(((Number) afterBudget.getBody().get("remainingAmount")).doubleValue()).isEqualTo(1000.00);
    }

    @Test
    void replayingIdempotencyKey_doesNotCreateSecondDraft() {
        HttpHeaders writeHeaders = signedHeaders(userId, List.of("procurement.write"), "create_purchase_draft");
        String key = UUID.randomUUID().toString();
        writeHeaders.set("Idempotency-Key", key);
        Map<String, Object> draftBody = Map.of("lines", List.of(Map.of("sku", sku, "quantity", 1)));

        ResponseEntity<Map> first = rest.exchange(url("/procurement/requests"), HttpMethod.POST,
                new HttpEntity<>(draftBody, writeHeaders), Map.class);
        HttpHeaders writeHeaders2 = signedHeaders(userId, List.of("procurement.write"), "create_purchase_draft");
        writeHeaders2.set("Idempotency-Key", key);
        ResponseEntity<Map> second = rest.exchange(url("/procurement/requests"), HttpMethod.POST,
                new HttpEntity<>(draftBody, writeHeaders2), Map.class);

        assertThat(second.getBody().get("id")).isEqualTo(first.getBody().get("id"));
        Integer count = jdbc.queryForObject(
                "SELECT COUNT(*) FROM purchase_request WHERE requester_user_id = ?", Integer.class, userId);
        assertThat(count).isEqualTo(1);
    }

    /** 建草稿 + 提交，返回采购申请 id——下面几个越权测试都要先有一条已提交的申请。 */
    private long createAndSubmit(long requesterUid) {
        HttpHeaders writeHeaders = signedHeaders(requesterUid, List.of("procurement.write"), "create_purchase_draft");
        writeHeaders.set("Idempotency-Key", UUID.randomUUID().toString());
        Map<String, Object> draftBody = Map.of("lines", List.of(Map.of("sku", sku, "quantity", 1)));
        ResponseEntity<Map> draft = rest.exchange(url("/procurement/requests"), HttpMethod.POST,
                new HttpEntity<>(draftBody, writeHeaders), Map.class);
        long requestId = ((Number) draft.getBody().get("id")).longValue();

        HttpHeaders submitHeaders = signedHeaders(requesterUid, List.of("procurement.write"), "submit_purchase_request");
        submitHeaders.set("Idempotency-Key", UUID.randomUUID().toString());
        rest.exchange(url("/procurement/requests/" + requestId + "/submit"), HttpMethod.POST,
                new HttpEntity<>(submitHeaders), Map.class);
        return requestId;
    }

    @Test
    void approvingOwnRequest_isRejected() {
        long requestId = createAndSubmit(userId);
        HttpHeaders selfApproveHeaders = signedHeaders(userId, teamId, List.of("procurement.approve"),
                "approve_purchase_request", false, true);
        selfApproveHeaders.set("Idempotency-Key", UUID.randomUUID().toString());
        ResponseEntity<String> resp = rest.exchange(url("/procurement/requests/" + requestId + "/approve"),
                HttpMethod.POST, new HttpEntity<>(Map.of("note", "自己批自己"), selfApproveHeaders), String.class);
        assertThat(resp.getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
    }

    @Test
    void regularMember_cannotApprove_returns404() {
        long requestId = createAndSubmit(userId);
        HttpHeaders regularMemberHeaders = signedHeaders(approverId, teamId, List.of("procurement.approve"),
                "approve_purchase_request", false, false);
        regularMemberHeaders.set("Idempotency-Key", UUID.randomUUID().toString());
        ResponseEntity<String> resp = rest.exchange(url("/procurement/requests/" + requestId + "/approve"),
                HttpMethod.POST, new HttpEntity<>(Map.of("note", "我也想批"), regularMemberHeaders), String.class);
        assertThat(resp.getStatusCode()).isEqualTo(HttpStatus.NOT_FOUND);
    }

    @Test
    void teamAdminOfDifferentDepartment_cannotApprove_returns404() {
        long requestId = createAndSubmit(userId); // 申请单的 teamId 是本测试的 teamId
        long otherTeamId = teamId + 100;
        HttpHeaders otherDeptHeaders = signedHeaders(approverId, otherTeamId, List.of("procurement.approve"),
                "approve_purchase_request", false, true);
        otherDeptHeaders.set("Idempotency-Key", UUID.randomUUID().toString());
        ResponseEntity<String> resp = rest.exchange(url("/procurement/requests/" + requestId + "/approve"),
                HttpMethod.POST, new HttpEntity<>(Map.of("note", "越权审批"), otherDeptHeaders), String.class);
        assertThat(resp.getStatusCode()).isEqualTo(HttpStatus.NOT_FOUND);
    }

    @Test
    void orgAdmin_canApproveAcrossDepartments() {
        long requestId = createAndSubmit(userId);
        HttpHeaders orgAdminHeaders = signedHeaders(approverId, null, List.of("procurement.approve"),
                "approve_purchase_request", true, false);
        orgAdminHeaders.set("Idempotency-Key", UUID.randomUUID().toString());
        ResponseEntity<Map> resp = rest.exchange(url("/procurement/requests/" + requestId + "/approve"),
                HttpMethod.POST, new HttpEntity<>(Map.of("note", "企业管理员批准"), orgAdminHeaders), Map.class);
        assertThat(resp.getStatusCode()).isEqualTo(HttpStatus.OK);
        assertThat(resp.getBody().get("status")).isEqualTo("APPROVED");
    }

    @Test
    void getStatus_strangerWithoutRole_returns404() {
        long requestId = createAndSubmit(userId);
        HttpHeaders strangerHeaders = signedHeaders(approverId, teamId, List.of("procurement.read"),
                "get_purchase_status", false, false);
        ResponseEntity<String> resp = rest.exchange(url("/procurement/requests/" + requestId), HttpMethod.GET,
                new HttpEntity<>(strangerHeaders), String.class);
        assertThat(resp.getStatusCode()).isEqualTo(HttpStatus.NOT_FOUND);
    }

    @Test
    void getStatus_teamAdminOfSameDepartment_canView() {
        long requestId = createAndSubmit(userId);
        HttpHeaders teamAdminHeaders = signedHeaders(approverId, teamId, List.of("procurement.read"),
                "get_purchase_status", false, true);
        ResponseEntity<Map> resp = rest.exchange(url("/procurement/requests/" + requestId), HttpMethod.GET,
                new HttpEntity<>(teamAdminHeaders), Map.class);
        assertThat(resp.getStatusCode()).isEqualTo(HttpStatus.OK);
    }
}
