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

/** 采购闭环的真实 HTTP 集成测试，跟 LeaveControllerIntegrationTest 是同一套骨架
 * （签名 header + 真实 MySQL），只是换了业务域。签名要求见那边类注释里 P1-8 的说明。 */
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
                                       List<String> scopes, String operation) {
        return signedHeaders(method, path, bodyJson, uid, teamId, scopes, operation, false, false);
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

    @Test
    void fullHappyPath_stock_budget_draft_submit_approve_deductsBudgetAndCreatesOrder() {
        String productPath = "/procurement/products/" + sku;
        ResponseEntity<Map> product = rest.exchange(url(productPath), HttpMethod.GET,
                new HttpEntity<>(signedHeaders(HttpMethod.GET, productPath, null, userId,
                        List.of("procurement.read"), "get_inventory_status")), Map.class);
        assertThat(product.getBody().get("belowSafetyStock")).isEqualTo(true); // 5 < 20

        String budgetPath = "/procurement/budget";
        ResponseEntity<Map> budget = rest.exchange(url(budgetPath), HttpMethod.GET,
                new HttpEntity<>(signedHeaders(HttpMethod.GET, budgetPath, null, userId,
                        List.of("procurement.read"), "get_department_budget")), Map.class);
        assertThat(((Number) budget.getBody().get("remainingAmount")).doubleValue()).isEqualTo(1000.00);

        String requestsPath = "/procurement/requests";
        String draftBodyJson = writeJson(Map.of("lines", List.of(Map.of("sku", sku, "quantity", 3))));
        HttpHeaders writeHeaders = signedHeaders(HttpMethod.POST, requestsPath, draftBodyJson, userId,
                List.of("procurement.write"), "create_purchase_draft");
        writeHeaders.set("Idempotency-Key", UUID.randomUUID().toString());
        ResponseEntity<Map> draft = rest.exchange(url(requestsPath), HttpMethod.POST,
                new HttpEntity<>(draftBodyJson, writeHeaders), Map.class);
        assertThat(draft.getBody().get("status")).isEqualTo("DRAFT");
        assertThat(((Number) draft.getBody().get("totalAmount")).doubleValue()).isEqualTo(300.00); // 3 * 100
        long requestId = ((Number) draft.getBody().get("id")).longValue();

        String submitPath = "/procurement/requests/" + requestId + "/submit";
        HttpHeaders submitHeaders = signedHeaders(HttpMethod.POST, submitPath, null, userId,
                List.of("procurement.write"), "submit_purchase_request");
        submitHeaders.set("Idempotency-Key", UUID.randomUUID().toString());
        ResponseEntity<Map> submitted = rest.exchange(url(submitPath),
                HttpMethod.POST, new HttpEntity<>(submitHeaders), Map.class);
        assertThat(submitted.getBody().get("status")).isEqualTo("SUBMITTED");

        String approvePath = "/procurement/requests/" + requestId + "/approve";
        String approveBodyJson = writeJson(Map.of("note", "同意采购"));
        HttpHeaders approveHeaders = signedHeaders(HttpMethod.POST, approvePath, approveBodyJson, approverId,
                teamId, List.of("procurement.approve"), "approve_purchase_request", false, true);
        approveHeaders.set("Idempotency-Key", UUID.randomUUID().toString());
        ResponseEntity<Map> approved = rest.exchange(url(approvePath),
                HttpMethod.POST, new HttpEntity<>(approveBodyJson, approveHeaders), Map.class);
        assertThat(approved.getBody().get("status")).isEqualTo("APPROVED");
        Map<?, ?> order = (Map<?, ?>) approved.getBody().get("purchaseOrder");
        assertThat(order).isNotNull();
        assertThat(order.get("supplierName")).isEqualTo("华东电子元件有限公司"); // MockSapConnector 的 SUP-001

        ResponseEntity<Map> afterBudget = rest.exchange(url(budgetPath), HttpMethod.GET,
                new HttpEntity<>(signedHeaders(HttpMethod.GET, budgetPath, null, userId,
                        List.of("procurement.read"), "get_department_budget")), Map.class);
        assertThat(((Number) afterBudget.getBody().get("remainingAmount")).doubleValue()).isEqualTo(700.00); // 1000-300
    }

    @Test
    void missingScope_returns403() {
        String path = "/procurement/products/" + sku;
        ResponseEntity<String> resp = rest.exchange(url(path), HttpMethod.GET,
                new HttpEntity<>(signedHeaders(HttpMethod.GET, path, null, userId,
                        List.of("other.scope"), "get_inventory_status")), String.class);
        assertThat(resp.getStatusCode()).isEqualTo(HttpStatus.FORBIDDEN);
    }

    @Test
    void tamperedBodyAfterSigning_returns401() {
        // P1-8（第四轮审计）：签名是对"原来那份请求体"算的，中途换掉请求体必须被拒绝。
        // 同 LeaveControllerIntegrationTest 类注释里的说明：POST + 401 会撞 JDK
        // HttpURLConnection 的已知限制（流式发送时服务端提前返回 401，客户端会抛
        // ResourceAccessException 而不是正常拿到状态码），两种表现都算通过，
        // 真正要证明的是"没有被当成合法请求处理"，用数据库状态确认更可靠。
        String path = "/procurement/requests";
        String signedBodyJson = writeJson(Map.of("lines", List.of(Map.of("sku", sku, "quantity", 1))));
        HttpHeaders headers = signedHeaders(HttpMethod.POST, path, signedBodyJson, userId,
                List.of("procurement.write"), "create_purchase_draft");
        headers.set("Idempotency-Key", UUID.randomUUID().toString());
        String tamperedBodyJson = writeJson(Map.of("lines", List.of(Map.of("sku", sku, "quantity", 999))));
        try {
            ResponseEntity<String> resp = rest.exchange(url(path), HttpMethod.POST,
                    new HttpEntity<>(tamperedBodyJson, headers), String.class);
            assertThat(resp.getStatusCode()).isEqualTo(HttpStatus.UNAUTHORIZED);
        } catch (org.springframework.web.client.ResourceAccessException e) {
            assertThat(e.getMessage()).contains("server authentication", "streaming mode");
        }
        Integer count = jdbc.queryForObject(
                "SELECT COUNT(*) FROM purchase_request WHERE requester_user_id = ?", Integer.class, userId);
        assertThat(count).isEqualTo(0);
    }

    @Test
    void insufficientBudget_submitFails() {
        String requestsPath = "/procurement/requests";
        // 单价100 * 20 个 = 2000，超过预算 1000
        String draftBodyJson = writeJson(Map.of("lines", List.of(Map.of("sku", sku, "quantity", 20))));
        HttpHeaders writeHeaders = signedHeaders(HttpMethod.POST, requestsPath, draftBodyJson, userId,
                List.of("procurement.write"), "create_purchase_draft");
        writeHeaders.set("Idempotency-Key", UUID.randomUUID().toString());
        ResponseEntity<Map> draft = rest.exchange(url(requestsPath), HttpMethod.POST,
                new HttpEntity<>(draftBodyJson, writeHeaders), Map.class);
        long requestId = ((Number) draft.getBody().get("id")).longValue();

        String submitPath = "/procurement/requests/" + requestId + "/submit";
        HttpHeaders submitHeaders = signedHeaders(HttpMethod.POST, submitPath, null, userId,
                List.of("procurement.write"), "submit_purchase_request");
        submitHeaders.set("Idempotency-Key", UUID.randomUUID().toString());
        ResponseEntity<String> resp = rest.exchange(url(submitPath),
                HttpMethod.POST, new HttpEntity<>(submitHeaders), String.class);
        assertThat(resp.getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
        assertThat(resp.getBody()).contains("预算不足").doesNotContain("\"path\"");
    }

    @Test
    void rejectedDraft_rollsBackIdempotencyKey_soCorrectedBodyCanReuseIt() {
        // FastAPI 的 AI 工作成果在业务拒绝后允许用户修改内容、沿用同一个幂等键重新保存，
        // 依赖的正是这里：业务异常让占位记录随事务回滚，这个键下没有任何残留。
        String requestsPath = "/procurement/requests";
        String key = UUID.randomUUID().toString();
        String badBody = writeJson(Map.of("lines", List.of(Map.of("sku", "NO-SUCH-SKU", "quantity", 1))));
        HttpHeaders badHeaders = signedHeaders(HttpMethod.POST, requestsPath, badBody, userId,
                List.of("procurement.write"), "create_purchase_draft");
        badHeaders.set("Idempotency-Key", key);
        ResponseEntity<String> rejected = rest.exchange(url(requestsPath), HttpMethod.POST,
                new HttpEntity<>(badBody, badHeaders), String.class);
        assertThat(rejected.getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
        assertThat(rejected.getBody()).contains("未知产品: NO-SUCH-SKU");
        assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM idempotency_record WHERE idempotency_key = ?",
                Integer.class, key)).isZero();

        String goodBody = writeJson(Map.of("lines", List.of(Map.of("sku", sku, "quantity", 1))));
        HttpHeaders goodHeaders = signedHeaders(HttpMethod.POST, requestsPath, goodBody, userId,
                List.of("procurement.write"), "create_purchase_draft");
        goodHeaders.set("Idempotency-Key", key);
        ResponseEntity<Map> created = rest.exchange(url(requestsPath), HttpMethod.POST,
                new HttpEntity<>(goodBody, goodHeaders), Map.class);
        assertThat(created.getStatusCode()).isEqualTo(HttpStatus.OK);
        assertThat(created.getBody().get("status")).isEqualTo("DRAFT");
    }

    @Test
    void rejectedRequest_doesNotDeductBudgetOrCreateOrder() {
        String requestsPath = "/procurement/requests";
        String draftBodyJson = writeJson(Map.of("lines", List.of(Map.of("sku", sku, "quantity", 2))));
        HttpHeaders writeHeaders = signedHeaders(HttpMethod.POST, requestsPath, draftBodyJson, userId,
                List.of("procurement.write"), "create_purchase_draft");
        writeHeaders.set("Idempotency-Key", UUID.randomUUID().toString());
        ResponseEntity<Map> draft = rest.exchange(url(requestsPath), HttpMethod.POST,
                new HttpEntity<>(draftBodyJson, writeHeaders), Map.class);
        long requestId = ((Number) draft.getBody().get("id")).longValue();

        String submitPath = "/procurement/requests/" + requestId + "/submit";
        HttpHeaders submitHeaders = signedHeaders(HttpMethod.POST, submitPath, null, userId,
                List.of("procurement.write"), "submit_purchase_request");
        submitHeaders.set("Idempotency-Key", UUID.randomUUID().toString());
        rest.exchange(url(submitPath), HttpMethod.POST, new HttpEntity<>(submitHeaders), Map.class);

        String rejectPath = "/procurement/requests/" + requestId + "/reject";
        String rejectBodyJson = writeJson(Map.of("note", "预算紧张"));
        HttpHeaders rejectHeaders = signedHeaders(HttpMethod.POST, rejectPath, rejectBodyJson, approverId, teamId,
                List.of("procurement.approve"), "reject_purchase_request", false, true);
        rejectHeaders.set("Idempotency-Key", UUID.randomUUID().toString());
        ResponseEntity<Map> rejected = rest.exchange(url(rejectPath),
                HttpMethod.POST, new HttpEntity<>(rejectBodyJson, rejectHeaders), Map.class);
        assertThat(rejected.getBody().get("status")).isEqualTo("REJECTED");
        assertThat(rejected.getBody().get("purchaseOrder")).isNull();

        String budgetPath = "/procurement/budget";
        ResponseEntity<Map> afterBudget = rest.exchange(url(budgetPath), HttpMethod.GET,
                new HttpEntity<>(signedHeaders(HttpMethod.GET, budgetPath, null, userId,
                        List.of("procurement.read"), "get_department_budget")), Map.class);
        assertThat(((Number) afterBudget.getBody().get("remainingAmount")).doubleValue()).isEqualTo(1000.00);
    }

    @Test
    void replayingIdempotencyKey_doesNotCreateSecondDraft() {
        String requestsPath = "/procurement/requests";
        String key = UUID.randomUUID().toString();
        String draftBodyJson = writeJson(Map.of("lines", List.of(Map.of("sku", sku, "quantity", 1))));
        HttpHeaders writeHeaders = signedHeaders(HttpMethod.POST, requestsPath, draftBodyJson, userId,
                List.of("procurement.write"), "create_purchase_draft");
        writeHeaders.set("Idempotency-Key", key);

        ResponseEntity<Map> first = rest.exchange(url(requestsPath), HttpMethod.POST,
                new HttpEntity<>(draftBodyJson, writeHeaders), Map.class);
        HttpHeaders writeHeaders2 = signedHeaders(HttpMethod.POST, requestsPath, draftBodyJson, userId,
                List.of("procurement.write"), "create_purchase_draft");
        writeHeaders2.set("Idempotency-Key", key);
        ResponseEntity<Map> second = rest.exchange(url(requestsPath), HttpMethod.POST,
                new HttpEntity<>(draftBodyJson, writeHeaders2), Map.class);

        assertThat(second.getBody().get("id")).isEqualTo(first.getBody().get("id"));
        Integer count = jdbc.queryForObject(
                "SELECT COUNT(*) FROM purchase_request WHERE requester_user_id = ?", Integer.class, userId);
        assertThat(count).isEqualTo(1);
    }

    /** 建草稿 + 提交，返回采购申请 id——下面几个越权测试都要先有一条已提交的申请。 */
    private long createAndSubmit(long requesterUid) {
        String requestsPath = "/procurement/requests";
        String draftBodyJson = writeJson(Map.of("lines", List.of(Map.of("sku", sku, "quantity", 1))));
        HttpHeaders writeHeaders = signedHeaders(HttpMethod.POST, requestsPath, draftBodyJson, requesterUid,
                List.of("procurement.write"), "create_purchase_draft");
        writeHeaders.set("Idempotency-Key", UUID.randomUUID().toString());
        ResponseEntity<Map> draft = rest.exchange(url(requestsPath), HttpMethod.POST,
                new HttpEntity<>(draftBodyJson, writeHeaders), Map.class);
        long requestId = ((Number) draft.getBody().get("id")).longValue();

        String submitPath = "/procurement/requests/" + requestId + "/submit";
        HttpHeaders submitHeaders = signedHeaders(HttpMethod.POST, submitPath, null, requesterUid,
                List.of("procurement.write"), "submit_purchase_request");
        submitHeaders.set("Idempotency-Key", UUID.randomUUID().toString());
        rest.exchange(url(submitPath), HttpMethod.POST, new HttpEntity<>(submitHeaders), Map.class);
        return requestId;
    }

    @Test
    void approvingOwnRequest_isRejected() {
        long requestId = createAndSubmit(userId);
        String approvePath = "/procurement/requests/" + requestId + "/approve";
        String bodyJson = writeJson(Map.of("note", "自己批自己"));
        HttpHeaders selfApproveHeaders = signedHeaders(HttpMethod.POST, approvePath, bodyJson, userId, teamId,
                List.of("procurement.approve"), "approve_purchase_request", false, true);
        selfApproveHeaders.set("Idempotency-Key", UUID.randomUUID().toString());
        ResponseEntity<String> resp = rest.exchange(url(approvePath),
                HttpMethod.POST, new HttpEntity<>(bodyJson, selfApproveHeaders), String.class);
        assertThat(resp.getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
    }

    @Test
    void regularMember_cannotApprove_returns404() {
        long requestId = createAndSubmit(userId);
        String approvePath = "/procurement/requests/" + requestId + "/approve";
        String bodyJson = writeJson(Map.of("note", "我也想批"));
        HttpHeaders regularMemberHeaders = signedHeaders(HttpMethod.POST, approvePath, bodyJson, approverId, teamId,
                List.of("procurement.approve"), "approve_purchase_request", false, false);
        regularMemberHeaders.set("Idempotency-Key", UUID.randomUUID().toString());
        ResponseEntity<String> resp = rest.exchange(url(approvePath),
                HttpMethod.POST, new HttpEntity<>(bodyJson, regularMemberHeaders), String.class);
        assertThat(resp.getStatusCode()).isEqualTo(HttpStatus.NOT_FOUND);
    }

    @Test
    void teamAdminOfDifferentDepartment_cannotApprove_returns404() {
        long requestId = createAndSubmit(userId); // 申请单的 teamId 是本测试的 teamId
        long otherTeamId = teamId + 100;
        String approvePath = "/procurement/requests/" + requestId + "/approve";
        String bodyJson = writeJson(Map.of("note", "越权审批"));
        HttpHeaders otherDeptHeaders = signedHeaders(HttpMethod.POST, approvePath, bodyJson, approverId, otherTeamId,
                List.of("procurement.approve"), "approve_purchase_request", false, true);
        otherDeptHeaders.set("Idempotency-Key", UUID.randomUUID().toString());
        ResponseEntity<String> resp = rest.exchange(url(approvePath),
                HttpMethod.POST, new HttpEntity<>(bodyJson, otherDeptHeaders), String.class);
        assertThat(resp.getStatusCode()).isEqualTo(HttpStatus.NOT_FOUND);
    }

    @Test
    void orgAdmin_canApproveAcrossDepartments() {
        long requestId = createAndSubmit(userId);
        String approvePath = "/procurement/requests/" + requestId + "/approve";
        String bodyJson = writeJson(Map.of("note", "企业管理员批准"));
        HttpHeaders orgAdminHeaders = signedHeaders(HttpMethod.POST, approvePath, bodyJson, approverId, null,
                List.of("procurement.approve"), "approve_purchase_request", true, false);
        orgAdminHeaders.set("Idempotency-Key", UUID.randomUUID().toString());
        ResponseEntity<Map> resp = rest.exchange(url(approvePath),
                HttpMethod.POST, new HttpEntity<>(bodyJson, orgAdminHeaders), Map.class);
        assertThat(resp.getStatusCode()).isEqualTo(HttpStatus.OK);
        assertThat(resp.getBody().get("status")).isEqualTo("APPROVED");
    }

    @Test
    void getStatus_strangerWithoutRole_returns404() {
        long requestId = createAndSubmit(userId);
        String statusPath = "/procurement/requests/" + requestId;
        HttpHeaders strangerHeaders = signedHeaders(HttpMethod.GET, statusPath, null, approverId, teamId,
                List.of("procurement.read"), "get_purchase_status", false, false);
        ResponseEntity<String> resp = rest.exchange(url(statusPath), HttpMethod.GET,
                new HttpEntity<>(strangerHeaders), String.class);
        assertThat(resp.getStatusCode()).isEqualTo(HttpStatus.NOT_FOUND);
    }

    @Test
    void getStatus_teamAdminOfSameDepartment_canView() {
        long requestId = createAndSubmit(userId);
        String statusPath = "/procurement/requests/" + requestId;
        HttpHeaders teamAdminHeaders = signedHeaders(HttpMethod.GET, statusPath, null, approverId, teamId,
                List.of("procurement.read"), "get_purchase_status", false, true);
        ResponseEntity<Map> resp = rest.exchange(url(statusPath), HttpMethod.GET,
                new HttpEntity<>(teamAdminHeaders), Map.class);
        assertThat(resp.getStatusCode()).isEqualTo(HttpStatus.OK);
    }
}
