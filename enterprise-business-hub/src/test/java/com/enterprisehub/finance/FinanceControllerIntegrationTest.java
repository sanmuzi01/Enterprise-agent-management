package com.enterprisehub.finance;

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

/** 财务报销闭环的真实 HTTP 集成测试，跟 LeaveControllerIntegrationTest/
 * ProcurementControllerIntegrationTest 是同一套骨架（签名 header + 真实 MySQL）。
 * 跟采购不同的地方：报销明细的金额是用户直接填的，setUp 不需要种任何"产品"这类
 * 主数据，只需要种 expense_budget。ID 段用 5_000_000L-5_999_000L，避开
 * crm(6xxxxxx)/procurement(7-8xxxxxx)/leave(9xxxxxx) 已用段。 */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
@ActiveProfiles("test")
class FinanceControllerIntegrationTest {

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

    @BeforeEach
    void setUp() {
        userId = ThreadLocalRandom.current().nextLong(5_000_000L, 5_999_000L);
        approverId = userId + 1;
        teamId = userId; // 每个测试自己的 team_id 段，避免跟别的测试撞预算行

        jdbc.update("INSERT INTO expense_budget (team_id, year, remaining_amount) VALUES (?, ?, 1000.00)",
                teamId, Instant.now().atZone(java.time.ZoneOffset.UTC).getYear());
    }

    @AfterEach
    void tearDown() {
        jdbc.update("DELETE FROM voucher_entry WHERE voucher_id IN "
                + "(SELECT id FROM voucher WHERE applicant_user_id = ?)", userId);
        jdbc.update("DELETE FROM voucher WHERE applicant_user_id = ?", userId);
        jdbc.update("DELETE FROM expense_line WHERE expense_claim_id IN "
                + "(SELECT id FROM expense_claim WHERE applicant_user_id = ?)", userId);
        jdbc.update("DELETE FROM expense_claim WHERE applicant_user_id = ?", userId);
        jdbc.update("DELETE FROM expense_budget WHERE team_id = ?", teamId);
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
    /** 非 null 时覆盖签进上下文的 org_team_ids（模拟别的企业的企业管理员）。 */
    private java.util.List<Long> orgTeamIdsOverride;

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
            // 企业管理员只管本企业的部门：签进 org_team_ids（测试里用到的部门编号）；orgTeamIdsOverride 用来模拟“别的企业的管理员”
            context.put("org_team_ids", orgTeamIdsOverride != null ? orgTeamIdsOverride : (isOrgAdmin
                    ? java.util.stream.Stream.of(headerTeamId, (Long) teamId, (Long) (teamId + 100), (Long) (teamId + 200)).filter(java.util.Objects::nonNull).distinct().toList()
                    : java.util.List.<Long>of()));
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
    void fullHappyPath_budget_draft_submit_approve_deductsBudget() {
        String budgetPath = "/finance/budget";
        ResponseEntity<Map> budget = rest.exchange(url(budgetPath), HttpMethod.GET,
                new HttpEntity<>(signedHeaders(HttpMethod.GET, budgetPath, null, userId,
                        List.of("finance.read"), "get_expense_budget")), Map.class);
        assertThat(((Number) budget.getBody().get("remainingAmount")).doubleValue()).isEqualTo(1000.00);

        String requestsPath = "/finance/expenses";
        String draftBodyJson = writeJson(Map.of("lines", List.of(
                Map.of("category", "TRAVEL", "amount", 200, "description", "机票"),
                Map.of("category", "MEAL", "amount", 100, "description", "客户宴请"))));
        HttpHeaders writeHeaders = signedHeaders(HttpMethod.POST, requestsPath, draftBodyJson, userId,
                List.of("finance.write"), "create_expense_draft");
        writeHeaders.set("Idempotency-Key", UUID.randomUUID().toString());
        ResponseEntity<Map> draft = rest.exchange(url(requestsPath), HttpMethod.POST,
                new HttpEntity<>(draftBodyJson, writeHeaders), Map.class);
        assertThat(draft.getBody().get("status")).isEqualTo("DRAFT");
        assertThat(((Number) draft.getBody().get("totalAmount")).doubleValue()).isEqualTo(300.00);
        long requestId = ((Number) draft.getBody().get("id")).longValue();

        String submitPath = "/finance/expenses/" + requestId + "/submit";
        HttpHeaders submitHeaders = signedHeaders(HttpMethod.POST, submitPath, null, userId,
                List.of("finance.write"), "submit_expense_claim");
        submitHeaders.set("Idempotency-Key", UUID.randomUUID().toString());
        ResponseEntity<Map> submitted = rest.exchange(url(submitPath),
                HttpMethod.POST, new HttpEntity<>(submitHeaders), Map.class);
        assertThat(submitted.getBody().get("status")).isEqualTo("SUBMITTED");

        String approvePath = "/finance/expenses/" + requestId + "/approve";
        String approveBodyJson = writeJson(Map.of("note", "同意报销"));
        HttpHeaders approveHeaders = signedHeaders(HttpMethod.POST, approvePath, approveBodyJson, approverId,
                teamId, List.of("finance.approve"), "approve_expense_claim", false, true);
        approveHeaders.set("Idempotency-Key", UUID.randomUUID().toString());
        ResponseEntity<Map> approved = rest.exchange(url(approvePath),
                HttpMethod.POST, new HttpEntity<>(approveBodyJson, approveHeaders), Map.class);
        assertThat(approved.getBody().get("status")).isEqualTo("APPROVED");

        ResponseEntity<Map> afterBudget = rest.exchange(url(budgetPath), HttpMethod.GET,
                new HttpEntity<>(signedHeaders(HttpMethod.GET, budgetPath, null, userId,
                        List.of("finance.read"), "get_expense_budget")), Map.class);
        assertThat(((Number) afterBudget.getBody().get("remainingAmount")).doubleValue()).isEqualTo(700.00);
    }

    @Test
    void missingScope_returns403() {
        String path = "/finance/budget";
        ResponseEntity<String> resp = rest.exchange(url(path), HttpMethod.GET,
                new HttpEntity<>(signedHeaders(HttpMethod.GET, path, null, userId,
                        List.of("other.scope"), "get_expense_budget")), String.class);
        assertThat(resp.getStatusCode()).isEqualTo(HttpStatus.FORBIDDEN);
    }

    @Test
    void tamperedBodyAfterSigning_returns401() {
        String path = "/finance/expenses";
        String signedBodyJson = writeJson(Map.of("lines", List.of(Map.of("category", "TRAVEL", "amount", 100))));
        HttpHeaders headers = signedHeaders(HttpMethod.POST, path, signedBodyJson, userId,
                List.of("finance.write"), "create_expense_draft");
        headers.set("Idempotency-Key", UUID.randomUUID().toString());
        String tamperedBodyJson = writeJson(Map.of("lines", List.of(Map.of("category", "TRAVEL", "amount", 999))));
        try {
            ResponseEntity<String> resp = rest.exchange(url(path), HttpMethod.POST,
                    new HttpEntity<>(tamperedBodyJson, headers), String.class);
            assertThat(resp.getStatusCode()).isEqualTo(HttpStatus.UNAUTHORIZED);
        } catch (org.springframework.web.client.ResourceAccessException e) {
            assertThat(e.getMessage()).contains("server authentication", "streaming mode");
        }
        Integer count = jdbc.queryForObject(
                "SELECT COUNT(*) FROM expense_claim WHERE applicant_user_id = ?", Integer.class, userId);
        assertThat(count).isEqualTo(0);
    }

    @Test
    void insufficientBudget_submitFails() {
        String requestsPath = "/finance/expenses";
        String draftBodyJson = writeJson(Map.of("lines", List.of(Map.of("category", "TRAVEL", "amount", 2000))));
        HttpHeaders writeHeaders = signedHeaders(HttpMethod.POST, requestsPath, draftBodyJson, userId,
                List.of("finance.write"), "create_expense_draft");
        writeHeaders.set("Idempotency-Key", UUID.randomUUID().toString());
        ResponseEntity<Map> draft = rest.exchange(url(requestsPath), HttpMethod.POST,
                new HttpEntity<>(draftBodyJson, writeHeaders), Map.class);
        long requestId = ((Number) draft.getBody().get("id")).longValue();

        String submitPath = "/finance/expenses/" + requestId + "/submit";
        HttpHeaders submitHeaders = signedHeaders(HttpMethod.POST, submitPath, null, userId,
                List.of("finance.write"), "submit_expense_claim");
        submitHeaders.set("Idempotency-Key", UUID.randomUUID().toString());
        ResponseEntity<String> resp = rest.exchange(url(submitPath),
                HttpMethod.POST, new HttpEntity<>(submitHeaders), String.class);
        assertThat(resp.getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
    }

    @Test
    void rejectedRequest_doesNotDeductBudget() {
        String requestsPath = "/finance/expenses";
        String draftBodyJson = writeJson(Map.of("lines", List.of(Map.of("category", "MEAL", "amount", 150))));
        HttpHeaders writeHeaders = signedHeaders(HttpMethod.POST, requestsPath, draftBodyJson, userId,
                List.of("finance.write"), "create_expense_draft");
        writeHeaders.set("Idempotency-Key", UUID.randomUUID().toString());
        ResponseEntity<Map> draft = rest.exchange(url(requestsPath), HttpMethod.POST,
                new HttpEntity<>(draftBodyJson, writeHeaders), Map.class);
        long requestId = ((Number) draft.getBody().get("id")).longValue();

        String submitPath = "/finance/expenses/" + requestId + "/submit";
        HttpHeaders submitHeaders = signedHeaders(HttpMethod.POST, submitPath, null, userId,
                List.of("finance.write"), "submit_expense_claim");
        submitHeaders.set("Idempotency-Key", UUID.randomUUID().toString());
        rest.exchange(url(submitPath), HttpMethod.POST, new HttpEntity<>(submitHeaders), Map.class);

        String rejectPath = "/finance/expenses/" + requestId + "/reject";
        String rejectBodyJson = writeJson(Map.of("note", "缺发票"));
        HttpHeaders rejectHeaders = signedHeaders(HttpMethod.POST, rejectPath, rejectBodyJson, approverId, teamId,
                List.of("finance.approve"), "reject_expense_claim", false, true);
        rejectHeaders.set("Idempotency-Key", UUID.randomUUID().toString());
        ResponseEntity<Map> rejected = rest.exchange(url(rejectPath),
                HttpMethod.POST, new HttpEntity<>(rejectBodyJson, rejectHeaders), Map.class);
        assertThat(rejected.getBody().get("status")).isEqualTo("REJECTED");

        String budgetPath = "/finance/budget";
        ResponseEntity<Map> afterBudget = rest.exchange(url(budgetPath), HttpMethod.GET,
                new HttpEntity<>(signedHeaders(HttpMethod.GET, budgetPath, null, userId,
                        List.of("finance.read"), "get_expense_budget")), Map.class);
        assertThat(((Number) afterBudget.getBody().get("remainingAmount")).doubleValue()).isEqualTo(1000.00);
    }

    private ResponseEntity<String> draftWithInvoices(String... invoiceNos) {
        String path = "/finance/expenses";
        List<Map<String, Object>> lines = new java.util.ArrayList<>();
        for (String no : invoiceNos) {
            lines.add(Map.of("category", "TRAVEL", "amount", 10, "description", "测试", "invoiceNo", no));
        }
        String body = writeJson(Map.of("lines", lines));
        HttpHeaders headers = signedHeaders(HttpMethod.POST, path, body, userId, List.of("finance.write"),
                "create_expense_draft");
        headers.set("Idempotency-Key", UUID.randomUUID().toString());
        return rest.exchange(url(path), HttpMethod.POST, new HttpEntity<>(body, headers), String.class);
    }

    @Test
    void sameInvoiceCannotBeClaimedTwice_untilFirstClaimIsRejected() throws Exception {
        String invoice = "INV-" + UUID.randomUUID().toString().substring(0, 8);
        ResponseEntity<String> first = draftWithInvoices(invoice);
        assertThat(first.getStatusCode()).isEqualTo(HttpStatus.OK);
        long firstId = ((Number) MAPPER.readValue(first.getBody(), Map.class).get("id")).longValue();

        ResponseEntity<String> duplicate = draftWithInvoices(invoice);
        assertThat(duplicate.getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
        assertThat(duplicate.getBody()).contains("发票号 " + invoice + " 已在报销单 #" + firstId);

        String usagePath = "/finance/invoices/usage?numbers=" + invoice;
        ResponseEntity<List> usage = rest.exchange(url(usagePath), HttpMethod.GET,
                new HttpEntity<>(signedHeaders(HttpMethod.GET, usagePath, null, userId, List.of("finance.read"),
                        "check_invoice_usage")), List.class);
        assertThat(usage.getBody()).hasSize(1);
        assertThat(((Map<?, ?>) usage.getBody().get(0)).get("status")).isEqualTo("DRAFT");

        String submitPath = "/finance/expenses/" + firstId + "/submit";
        HttpHeaders submitHeaders = signedHeaders(HttpMethod.POST, submitPath, null, userId,
                List.of("finance.write"), "submit_expense_claim");
        submitHeaders.set("Idempotency-Key", UUID.randomUUID().toString());
        rest.exchange(url(submitPath), HttpMethod.POST, new HttpEntity<>(submitHeaders), Map.class);
        String rejectPath = "/finance/expenses/" + firstId + "/reject";
        String rejectBody = writeJson(Map.of("note", "发票抬头错误"));
        HttpHeaders rejectHeaders = signedHeaders(HttpMethod.POST, rejectPath, rejectBody, approverId, teamId,
                List.of("finance.approve"), "reject_expense_claim", false, true);
        rejectHeaders.set("Idempotency-Key", UUID.randomUUID().toString());
        rest.exchange(url(rejectPath), HttpMethod.POST, new HttpEntity<>(rejectBody, rejectHeaders), Map.class);

        assertThat(draftWithInvoices(invoice).getStatusCode()).isEqualTo(HttpStatus.OK);
    }

    @Test
    void duplicateInvoiceWithinOneClaim_isRejected() {
        String invoice = "INV-" + UUID.randomUUID().toString().substring(0, 8);
        ResponseEntity<String> resp = draftWithInvoices(invoice, invoice);
        assertThat(resp.getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
        assertThat(resp.getBody()).contains("同一报销单中发票号重复");
    }

    @Test
    void replayingIdempotencyKey_doesNotCreateSecondDraft() {
        String requestsPath = "/finance/expenses";
        String key = UUID.randomUUID().toString();
        String draftBodyJson = writeJson(Map.of("lines", List.of(Map.of("category", "TRAVEL", "amount", 50))));
        HttpHeaders writeHeaders = signedHeaders(HttpMethod.POST, requestsPath, draftBodyJson, userId,
                List.of("finance.write"), "create_expense_draft");
        writeHeaders.set("Idempotency-Key", key);

        ResponseEntity<Map> first = rest.exchange(url(requestsPath), HttpMethod.POST,
                new HttpEntity<>(draftBodyJson, writeHeaders), Map.class);
        HttpHeaders writeHeaders2 = signedHeaders(HttpMethod.POST, requestsPath, draftBodyJson, userId,
                List.of("finance.write"), "create_expense_draft");
        writeHeaders2.set("Idempotency-Key", key);
        ResponseEntity<Map> second = rest.exchange(url(requestsPath), HttpMethod.POST,
                new HttpEntity<>(draftBodyJson, writeHeaders2), Map.class);

        assertThat(second.getBody().get("id")).isEqualTo(first.getBody().get("id"));
        Integer count = jdbc.queryForObject(
                "SELECT COUNT(*) FROM expense_claim WHERE applicant_user_id = ?", Integer.class, userId);
        assertThat(count).isEqualTo(1);
    }

    /** 建草稿 + 提交，返回报销单 id——下面几个越权测试都要先有一条已提交的申请。 */
    private long createAndSubmit(long requesterUid) {
        String requestsPath = "/finance/expenses";
        String draftBodyJson = writeJson(Map.of("lines", List.of(Map.of("category", "TRAVEL", "amount", 50))));
        HttpHeaders writeHeaders = signedHeaders(HttpMethod.POST, requestsPath, draftBodyJson, requesterUid,
                List.of("finance.write"), "create_expense_draft");
        writeHeaders.set("Idempotency-Key", UUID.randomUUID().toString());
        ResponseEntity<Map> draft = rest.exchange(url(requestsPath), HttpMethod.POST,
                new HttpEntity<>(draftBodyJson, writeHeaders), Map.class);
        long requestId = ((Number) draft.getBody().get("id")).longValue();

        String submitPath = "/finance/expenses/" + requestId + "/submit";
        HttpHeaders submitHeaders = signedHeaders(HttpMethod.POST, submitPath, null, requesterUid,
                List.of("finance.write"), "submit_expense_claim");
        submitHeaders.set("Idempotency-Key", UUID.randomUUID().toString());
        rest.exchange(url(submitPath), HttpMethod.POST, new HttpEntity<>(submitHeaders), Map.class);
        return requestId;
    }

    @Test
    void approvingOwnRequest_isRejected() {
        long requestId = createAndSubmit(userId);
        String approvePath = "/finance/expenses/" + requestId + "/approve";
        String bodyJson = writeJson(Map.of("note", "自己批自己"));
        HttpHeaders selfApproveHeaders = signedHeaders(HttpMethod.POST, approvePath, bodyJson, userId, teamId,
                List.of("finance.approve"), "approve_expense_claim", false, true);
        selfApproveHeaders.set("Idempotency-Key", UUID.randomUUID().toString());
        ResponseEntity<String> resp = rest.exchange(url(approvePath),
                HttpMethod.POST, new HttpEntity<>(bodyJson, selfApproveHeaders), String.class);
        assertThat(resp.getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
    }

    @Test
    void regularMember_cannotApprove_returns404() {
        long requestId = createAndSubmit(userId);
        String approvePath = "/finance/expenses/" + requestId + "/approve";
        String bodyJson = writeJson(Map.of("note", "我也想批"));
        HttpHeaders regularMemberHeaders = signedHeaders(HttpMethod.POST, approvePath, bodyJson, approverId, teamId,
                List.of("finance.approve"), "approve_expense_claim", false, false);
        regularMemberHeaders.set("Idempotency-Key", UUID.randomUUID().toString());
        ResponseEntity<String> resp = rest.exchange(url(approvePath),
                HttpMethod.POST, new HttpEntity<>(bodyJson, regularMemberHeaders), String.class);
        assertThat(resp.getStatusCode()).isEqualTo(HttpStatus.NOT_FOUND);
    }

    @Test
    void teamAdminOfDifferentDepartment_cannotApprove_returns404() {
        long requestId = createAndSubmit(userId); // 申请单的 teamId 是本测试的 teamId
        long otherTeamId = teamId + 100;
        String approvePath = "/finance/expenses/" + requestId + "/approve";
        String bodyJson = writeJson(Map.of("note", "越权审批"));
        HttpHeaders otherDeptHeaders = signedHeaders(HttpMethod.POST, approvePath, bodyJson, approverId, otherTeamId,
                List.of("finance.approve"), "approve_expense_claim", false, true);
        otherDeptHeaders.set("Idempotency-Key", UUID.randomUUID().toString());
        ResponseEntity<String> resp = rest.exchange(url(approvePath),
                HttpMethod.POST, new HttpEntity<>(bodyJson, otherDeptHeaders), String.class);
        assertThat(resp.getStatusCode()).isEqualTo(HttpStatus.NOT_FOUND);
    }

    @Test
    void orgAdmin_canApproveAcrossDepartments() {
        long requestId = createAndSubmit(userId);
        String approvePath = "/finance/expenses/" + requestId + "/approve";
        String bodyJson = writeJson(Map.of("note", "企业管理员批准"));
        HttpHeaders orgAdminHeaders = signedHeaders(HttpMethod.POST, approvePath, bodyJson, approverId, null,
                List.of("finance.approve"), "approve_expense_claim", true, false);
        orgAdminHeaders.set("Idempotency-Key", UUID.randomUUID().toString());
        ResponseEntity<Map> resp = rest.exchange(url(approvePath),
                HttpMethod.POST, new HttpEntity<>(bodyJson, orgAdminHeaders), Map.class);
        assertThat(resp.getStatusCode()).isEqualTo(HttpStatus.OK);
        assertThat(resp.getBody().get("status")).isEqualTo("APPROVED");
    }

    @Test
    void getStatus_strangerWithoutRole_returns404() {
        long requestId = createAndSubmit(userId);
        String statusPath = "/finance/expenses/" + requestId;
        HttpHeaders strangerHeaders = signedHeaders(HttpMethod.GET, statusPath, null, approverId, teamId,
                List.of("finance.read"), "get_expense_status", false, false);
        ResponseEntity<String> resp = rest.exchange(url(statusPath), HttpMethod.GET,
                new HttpEntity<>(strangerHeaders), String.class);
        assertThat(resp.getStatusCode()).isEqualTo(HttpStatus.NOT_FOUND);
    }

    @Test
    void myRequests_returnsOnlyOwnRequests_orderedNewestFirst() throws InterruptedException {
        // expense_claim.created_at 是 DATETIME（秒级精度），两次紧邻的 HTTP 调用容易
        // 落在同一秒里导致 ORDER BY created_at DESC 的相对顺序不确定——显式跨越一次
        // 秒边界，不依赖运气（跟 CRM 列表测试踩过的同一类坑）。
        long myId = createAndSubmit(userId);
        Thread.sleep(1100);
        long myId2 = createAndSubmit(userId);
        createAndSubmit(approverId); // 同一个部门，但申请人不同

        String path = "/finance/expenses/mine";
        HttpHeaders headers = signedHeaders(HttpMethod.GET, path, null, userId, teamId,
                List.of("finance.read"), "get_my_expense_claims", false, false);
        ResponseEntity<Map[]> resp = rest.exchange(url(path), HttpMethod.GET, new HttpEntity<>(headers), Map[].class);

        assertThat(resp.getStatusCode()).isEqualTo(HttpStatus.OK);
        List<Long> ids = List.of(resp.getBody()).stream().map(m -> ((Number) m.get("id")).longValue()).toList();
        assertThat(ids).containsExactly(myId2, myId);
    }

    @Test
    void myRequests_emptyList_whenNoRequests() {
        String path = "/finance/expenses/mine";
        HttpHeaders headers = signedHeaders(HttpMethod.GET, path, null, userId, teamId,
                List.of("finance.read"), "get_my_expense_claims", false, false);
        ResponseEntity<Map[]> resp = rest.exchange(url(path), HttpMethod.GET, new HttpEntity<>(headers), Map[].class);

        assertThat(resp.getStatusCode()).isEqualTo(HttpStatus.OK);
        assertThat(resp.getBody()).isEmpty();
    }

    @Test
    void teamPending_excludesOtherTeams_andExcludesNonSubmittedStatuses() {
        long pendingInMyTeam = createAndSubmit(userId);
        // 别的部门也建一条已提交的，需要先给它建预算行
        long otherTeamId = teamId + 200;
        jdbc.update("INSERT INTO expense_budget (team_id, year, remaining_amount) VALUES (?, ?, 1000.00)",
                otherTeamId, Instant.now().atZone(java.time.ZoneOffset.UTC).getYear());
        String requestsPath = "/finance/expenses";
        String otherDraftJson = writeJson(Map.of("lines", List.of(Map.of("category", "MEAL", "amount", 30))));
        HttpHeaders otherWriteHeaders = signedHeaders(HttpMethod.POST, requestsPath, otherDraftJson, userId,
                otherTeamId, List.of("finance.write"), "create_expense_draft", false, false);
        otherWriteHeaders.set("Idempotency-Key", UUID.randomUUID().toString());
        ResponseEntity<Map> otherDraft = rest.exchange(url(requestsPath), HttpMethod.POST,
                new HttpEntity<>(otherDraftJson, otherWriteHeaders), Map.class);
        long otherRequestId = ((Number) otherDraft.getBody().get("id")).longValue();
        String otherSubmitPath = "/finance/expenses/" + otherRequestId + "/submit";
        HttpHeaders otherSubmitHeaders = signedHeaders(HttpMethod.POST, otherSubmitPath, null, userId, otherTeamId,
                List.of("finance.write"), "submit_expense_claim", false, false);
        otherSubmitHeaders.set("Idempotency-Key", UUID.randomUUID().toString());
        rest.exchange(url(otherSubmitPath), HttpMethod.POST, new HttpEntity<>(otherSubmitHeaders), Map.class);

        // 本部门再建一条草稿（不提交），不该出现
        String draftOnlyJson = writeJson(Map.of("lines", List.of(Map.of("category", "OTHER", "amount", 10))));
        HttpHeaders draftOnlyHeaders = signedHeaders(HttpMethod.POST, requestsPath, draftOnlyJson, userId,
                List.of("finance.write"), "create_expense_draft");
        draftOnlyHeaders.set("Idempotency-Key", UUID.randomUUID().toString());
        rest.exchange(url(requestsPath), HttpMethod.POST, new HttpEntity<>(draftOnlyJson, draftOnlyHeaders), Map.class);

        String path = "/finance/expenses/team-pending?teamId=" + teamId;
        HttpHeaders headers = signedHeaders(HttpMethod.GET, path, null, approverId, teamId,
                List.of("finance.read"), "get_team_pending_expense_claims", false, true);
        ResponseEntity<Map[]> resp = rest.exchange(url(path), HttpMethod.GET, new HttpEntity<>(headers), Map[].class);

        assertThat(resp.getStatusCode()).isEqualTo(HttpStatus.OK);
        List<Long> ids = List.of(resp.getBody()).stream().map(m -> ((Number) m.get("id")).longValue()).toList();
        assertThat(ids).containsExactly(pendingInMyTeam);

        jdbc.update("DELETE FROM expense_line WHERE expense_claim_id = ?", otherRequestId);
        jdbc.update("DELETE FROM expense_claim WHERE id = ?", otherRequestId);
        jdbc.update("DELETE FROM expense_budget WHERE team_id = ?", otherTeamId);
    }

    @Test
    void teamPending_regularMemberWithoutTeamAdminRole_returns404() {
        createAndSubmit(userId);
        String path = "/finance/expenses/team-pending?teamId=" + teamId;
        HttpHeaders headers = signedHeaders(HttpMethod.GET, path, null, approverId, teamId,
                List.of("finance.read"), "get_team_pending_expense_claims", false, false);
        ResponseEntity<String> resp = rest.exchange(url(path), HttpMethod.GET, new HttpEntity<>(headers), String.class);
        assertThat(resp.getStatusCode()).isEqualTo(HttpStatus.NOT_FOUND);
    }

    @Test
    void teamPending_orgAdminCanQueryAnyTeam_viaTeamIdParam() {
        long pending = createAndSubmit(userId);
        String path = "/finance/expenses/team-pending?teamId=" + teamId;
        HttpHeaders headers = signedHeaders(HttpMethod.GET, path, null, approverId, null,
                List.of("finance.read"), "get_team_pending_expense_claims", true, false);
        ResponseEntity<Map[]> resp = rest.exchange(url(path), HttpMethod.GET, new HttpEntity<>(headers), Map[].class);

        assertThat(resp.getStatusCode()).isEqualTo(HttpStatus.OK);
        List<Long> ids = List.of(resp.getBody()).stream().map(m -> ((Number) m.get("id")).longValue()).toList();
        assertThat(ids).containsExactly(pending);
    }
}
