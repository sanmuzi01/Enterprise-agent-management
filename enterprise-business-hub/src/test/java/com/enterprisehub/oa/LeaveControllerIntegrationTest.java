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

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.Instant;
import java.util.Base64;
import java.util.HexFormat;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.Callable;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.ThreadLocalRandom;
import java.util.concurrent.TimeUnit;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * OA 请假闭环的真实 HTTP 集成测试：走完整的 SignedRequestContextFilter →
 * Controller → Service → 真实 MySQL 全链路，不是单测 mock。
 *
 * 跟主项目 Python 测试同一个思路：不建独立测试库，复用 enterprise_business，
 * 每个用户 ID 随机生成避免相互冲突，测试结束自己清理建的数据。
 *
 * P1-8（第四轮审计）之后，签名必须绑定 method/path/body_sha256——`signedHeaders`
 * 现在要求调用方明确传入这次请求真正的方法、路径和请求体，不能再独立于请求
 * 本身构造头。请求体一律先用 {@link #writeJson} 序列化成确定的字符串，再用
 * 这份完全一样的字符串去算哈希、签名、以及作为 {@code rest.exchange} 真正发出去
 * 的 body——不能对同一个逻辑上"一样"的 Map 分别序列化两次（哪怕内容相同，
 * 字段顺序不保证一样，body_sha256 就会跟服务端重新算的对不上）。
 */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
@ActiveProfiles("test")
class LeaveControllerIntegrationTest {

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
        // 每个测试方法用一段不会撞到别的测试/别的 worker 的用户 ID 区间。
        userId = ThreadLocalRandom.current().nextLong(9_000_000L, 9_999_000L);
        approverId = userId + 1;
        teamId = 2L;
        jdbc.update("INSERT INTO leave_balance (user_id, leave_type_id, year, remaining_days) VALUES (?, 1, ?, 10)",
                userId, Instant.now().atZone(java.time.ZoneOffset.UTC).getYear());
    }

    @AfterEach
    void tearDown() {
        jdbc.update("DELETE FROM leave_request WHERE applicant_user_id = ?", userId);
        jdbc.update("DELETE FROM leave_balance WHERE user_id = ?", userId);
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

    /** {@code bodyJson} 为 null 表示这次请求没有请求体（GET/无参数的 POST）。 */
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
            byte[] sig = mac.doFinal(contextB64.getBytes(StandardCharsets.UTF_8));
            String sigHex = HexFormat.of().formatHex(sig);

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
    void fullHappyPath_balanceCheck_draft_submit_approve_deductsBalance() {
        String balancePath = "/oa/leave/balance";
        HttpHeaders readHeaders = signedHeaders(HttpMethod.GET, balancePath, null, userId,
                List.of("oa.leave.read"), "get_leave_balance");
        ResponseEntity<Map[]> balanceResp = rest.exchange(url(balancePath), HttpMethod.GET,
                new HttpEntity<>(readHeaders), Map[].class);
        assertThat(balanceResp.getStatusCode()).isEqualTo(HttpStatus.OK);
        assertThat(balanceResp.getBody()[0].get("remainingDays")).isEqualTo(10.0);

        String requestsPath = "/oa/leave/requests";
        String draftBodyJson = writeJson(Map.of(
                "leaveTypeCode", "annual", "startDate", "2026-11-01", "endDate", "2026-11-02", "reason", "集成测试"
        ));
        HttpHeaders writeHeaders = signedHeaders(HttpMethod.POST, requestsPath, draftBodyJson, userId,
                List.of("oa.leave.write"), "create_leave_draft");
        writeHeaders.set("Idempotency-Key", UUID.randomUUID().toString());
        ResponseEntity<Map> draftResp = rest.exchange(url(requestsPath), HttpMethod.POST,
                new HttpEntity<>(draftBodyJson, writeHeaders), Map.class);
        assertThat(draftResp.getStatusCode()).isEqualTo(HttpStatus.OK);
        assertThat(draftResp.getBody().get("status")).isEqualTo("DRAFT");
        long requestId = ((Number) draftResp.getBody().get("id")).longValue();

        String submitPath = "/oa/leave/requests/" + requestId + "/submit";
        HttpHeaders submitHeaders = signedHeaders(HttpMethod.POST, submitPath, null, userId,
                List.of("oa.leave.write"), "submit_leave_request");
        submitHeaders.set("Idempotency-Key", UUID.randomUUID().toString());
        ResponseEntity<Map> submitResp = rest.exchange(url(submitPath),
                HttpMethod.POST, new HttpEntity<>(submitHeaders), Map.class);
        assertThat(submitResp.getBody().get("status")).isEqualTo("SUBMITTED");

        String approvePath = "/oa/leave/requests/" + requestId + "/approve";
        String approveBodyJson = writeJson(Map.of("note", "同意"));
        HttpHeaders approveHeaders = signedHeaders(HttpMethod.POST, approvePath, approveBodyJson, approverId,
                teamId, List.of("oa.leave.approve"), "approve_leave_request", false, true);
        approveHeaders.set("Idempotency-Key", UUID.randomUUID().toString());
        ResponseEntity<Map> approveResp = rest.exchange(url(approvePath),
                HttpMethod.POST, new HttpEntity<>(approveBodyJson, approveHeaders), Map.class);
        assertThat(approveResp.getBody().get("status")).isEqualTo("APPROVED");

        HttpHeaders afterBalanceHeaders = signedHeaders(HttpMethod.GET, balancePath, null, userId,
                List.of("oa.leave.read"), "get_leave_balance");
        ResponseEntity<Map[]> afterBalance = rest.exchange(url(balancePath), HttpMethod.GET,
                new HttpEntity<>(afterBalanceHeaders), Map[].class);
        assertThat(afterBalance.getBody()[0].get("remainingDays")).isEqualTo(8.0);
    }

    @Test
    void missingScope_returns403() {
        String path = "/oa/leave/balance";
        HttpHeaders headers = signedHeaders(HttpMethod.GET, path, null, userId,
                List.of("some.other.scope"), "get_leave_balance");
        ResponseEntity<String> resp = rest.exchange(url(path), HttpMethod.GET,
                new HttpEntity<>(headers), String.class);
        assertThat(resp.getStatusCode()).isEqualTo(HttpStatus.FORBIDDEN);
    }

    @Test
    void badSignature_returns401() {
        String path = "/oa/leave/balance";
        HttpHeaders headers = signedHeaders(HttpMethod.GET, path, null, userId,
                List.of("oa.leave.read"), "get_leave_balance");
        headers.set("X-Signature", "0".repeat(64));
        ResponseEntity<String> resp = rest.exchange(url(path), HttpMethod.GET,
                new HttpEntity<>(headers), String.class);
        assertThat(resp.getStatusCode()).isEqualTo(HttpStatus.UNAUTHORIZED);
    }

    @Test
    void tamperedBodyAfterSigning_returns401() {
        // P1-8 的核心场景：签名是对"原来那份请求体"算的，中途把请求体换掉之后
        // （签名/头原样不动），必须被拒绝——这是之前的漏洞，截获一份合法头能在
        // 请求体上做手脚。
        //
        // JDK 的 HttpURLConnection 对"POST 请求体还在流式发送时服务端就返回
        // 401"这种时序有个已知限制（会抛 HttpRetryException: cannot retry due
        // to server authentication, in streaming mode——401 会触发 JDK 内建的
        // 认证重试机制，跟本项目的鉴权逻辑无关，纯粹是 TestRestTemplate 默认用的
        // 客户端实现细节），实测会在这个场景下把响应体读取失败包装成
        // ResourceAccessException，读不到真正的状态码。这里两种表现都当作
        // "请求被拒绝、没有被服务端当成合法请求处理"的证据：能正常拿到响应就该是
        // 401，拿不到就必须是这个已知的客户端异常，不能是请求"成功"了。
        String path = "/oa/leave/requests";
        String signedBodyJson = writeJson(Map.of(
                "leaveTypeCode", "annual", "startDate", "2026-11-01", "endDate", "2026-11-02", "reason", "原始"
        ));
        HttpHeaders headers = signedHeaders(HttpMethod.POST, path, signedBodyJson, userId,
                List.of("oa.leave.write"), "create_leave_draft");
        headers.set("Idempotency-Key", UUID.randomUUID().toString());
        String tamperedBodyJson = writeJson(Map.of(
                "leaveTypeCode", "annual", "startDate", "2026-11-01", "endDate", "2026-11-30", "reason", "被篡改"
        ));
        try {
            ResponseEntity<String> resp = rest.exchange(url(path), HttpMethod.POST,
                    new HttpEntity<>(tamperedBodyJson, headers), String.class);
            assertThat(resp.getStatusCode()).isEqualTo(HttpStatus.UNAUTHORIZED);
        } catch (org.springframework.web.client.ResourceAccessException e) {
            assertThat(e.getMessage()).contains("server authentication", "streaming mode");
        }

        Integer count = jdbc.queryForObject(
                "SELECT COUNT(*) FROM leave_request WHERE applicant_user_id = ? AND reason = ?",
                Integer.class, userId, "被篡改");
        assertThat(count).isEqualTo(0);
    }

    @Test
    void replayingSignatureAgainstDifferentPath_returns401() {
        // 同样是 P1-8 的场景：一份对 A 接口签的有效签名，拿去打 B 接口——即使
        // scope 恰好也满足 B 接口的要求，也必须因为 method/path 对不上而被拒绝。
        String balancePath = "/oa/leave/balance";
        HttpHeaders headersForBalance = signedHeaders(HttpMethod.GET, balancePath, null, userId,
                List.of("oa.leave.read"), "get_leave_balance");
        long requestId = createAndSubmit(userId);
        String statusPath = "/oa/leave/requests/" + requestId;
        ResponseEntity<String> resp = rest.exchange(url(statusPath), HttpMethod.GET,
                new HttpEntity<>(headersForBalance), String.class);
        assertThat(resp.getStatusCode()).isEqualTo(HttpStatus.UNAUTHORIZED);
    }

    @Test
    void insufficientBalance_submitFails() {
        String requestsPath = "/oa/leave/requests";
        // 11 天的年假请求，但账上只有 10 天。
        String draftBodyJson = writeJson(Map.of(
                "leaveTypeCode", "annual", "startDate", "2026-11-01", "endDate", "2026-11-11", "reason", "超额测试"
        ));
        HttpHeaders writeHeaders = signedHeaders(HttpMethod.POST, requestsPath, draftBodyJson, userId,
                List.of("oa.leave.write"), "create_leave_draft");
        writeHeaders.set("Idempotency-Key", UUID.randomUUID().toString());
        ResponseEntity<Map> draftResp = rest.exchange(url(requestsPath), HttpMethod.POST,
                new HttpEntity<>(draftBodyJson, writeHeaders), Map.class);
        long requestId = ((Number) draftResp.getBody().get("id")).longValue();

        String submitPath = "/oa/leave/requests/" + requestId + "/submit";
        HttpHeaders submitHeaders = signedHeaders(HttpMethod.POST, submitPath, null, userId,
                List.of("oa.leave.write"), "submit_leave_request");
        submitHeaders.set("Idempotency-Key", UUID.randomUUID().toString());
        ResponseEntity<String> submitResp = rest.exchange(url(submitPath),
                HttpMethod.POST, new HttpEntity<>(submitHeaders), String.class);
        assertThat(submitResp.getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
    }

    @Test
    void replayingIdempotencyKey_doesNotCreateSecondDraft() {
        String requestsPath = "/oa/leave/requests";
        String key = UUID.randomUUID().toString();
        String draftBodyJson = writeJson(Map.of(
                "leaveTypeCode", "annual", "startDate", "2026-11-01", "endDate", "2026-11-02", "reason", "幂等测试"
        ));
        HttpHeaders writeHeaders = signedHeaders(HttpMethod.POST, requestsPath, draftBodyJson, userId,
                List.of("oa.leave.write"), "create_leave_draft");
        writeHeaders.set("Idempotency-Key", key);

        ResponseEntity<Map> first = rest.exchange(url(requestsPath), HttpMethod.POST,
                new HttpEntity<>(draftBodyJson, writeHeaders), Map.class);
        // 第二次用完全一样的 Idempotency-Key（context/签名/nonce 都重新生成，只有这个 key 复用）。
        HttpHeaders writeHeaders2 = signedHeaders(HttpMethod.POST, requestsPath, draftBodyJson, userId,
                List.of("oa.leave.write"), "create_leave_draft");
        writeHeaders2.set("Idempotency-Key", key);
        ResponseEntity<Map> second = rest.exchange(url(requestsPath), HttpMethod.POST,
                new HttpEntity<>(draftBodyJson, writeHeaders2), Map.class);

        assertThat(second.getBody().get("id")).isEqualTo(first.getBody().get("id"));

        Integer count = jdbc.queryForObject(
                "SELECT COUNT(*) FROM leave_request WHERE applicant_user_id = ?", Integer.class, userId);
        assertThat(count).isEqualTo(1);
    }

    @Test
    void concurrentSameIdempotencyKey_onlyOneSucceeds() throws Exception {
        // P1 并发修复的真实验证（第四轮审计）：两个请求用同一个 Idempotency-Key
        // 真正并发打进来（两个线程同时发，用 CountDownLatch 卡住让它们尽量同时
        // 起跑，不是顺序调用），必须只有一个真正执行了业务逻辑创建请假单，
        // 另一个要么 409（并发中），要么拿到同一张单据的缓存重放，绝不能再建一条。
        String requestsPath = "/oa/leave/requests";
        String key = UUID.randomUUID().toString();
        String draftBodyJson = writeJson(Map.of(
                "leaveTypeCode", "annual", "startDate", "2026-11-03", "endDate", "2026-11-04",
                "reason", "并发幂等测试"
        ));
        HttpHeaders writeHeaders1 = signedHeaders(HttpMethod.POST, requestsPath, draftBodyJson, userId,
                List.of("oa.leave.write"), "create_leave_draft");
        writeHeaders1.set("Idempotency-Key", key);
        HttpHeaders writeHeaders2 = signedHeaders(HttpMethod.POST, requestsPath, draftBodyJson, userId,
                List.of("oa.leave.write"), "create_leave_draft");
        writeHeaders2.set("Idempotency-Key", key);

        CountDownLatch ready = new CountDownLatch(2);
        CountDownLatch go = new CountDownLatch(1);
        Callable<ResponseEntity<Map>> task1 = () -> {
            ready.countDown();
            go.await();
            return rest.exchange(url(requestsPath), HttpMethod.POST,
                    new HttpEntity<>(draftBodyJson, writeHeaders1), Map.class);
        };
        Callable<ResponseEntity<Map>> task2 = () -> {
            ready.countDown();
            go.await();
            return rest.exchange(url(requestsPath), HttpMethod.POST,
                    new HttpEntity<>(draftBodyJson, writeHeaders2), Map.class);
        };

        ExecutorService pool = Executors.newFixedThreadPool(2);
        try {
            Future<ResponseEntity<Map>> f1 = pool.submit(task1);
            Future<ResponseEntity<Map>> f2 = pool.submit(task2);
            ready.await();
            go.countDown();
            ResponseEntity<Map> r1 = f1.get(10, TimeUnit.SECONDS);
            ResponseEntity<Map> r2 = f2.get(10, TimeUnit.SECONDS);

            List<HttpStatus> statuses = List.of(
                    HttpStatus.valueOf(r1.getStatusCode().value()), HttpStatus.valueOf(r2.getStatusCode().value()));
            // 不变式：只创建一条草稿，没有 5xx。两个请求的先后是不确定的：同时到达时后者拿 409；
            // 后者晚于前者完成时拿到的是同一个 key 缓存的 200（幂等重放，返回同一张单据），
            // 这是正确行为而不是重复执行——所以不能断言"恰好一个 200 一个 409"。
            assertThat(statuses).allMatch(st -> st == HttpStatus.OK || st == HttpStatus.CONFLICT);
            assertThat(statuses).contains(HttpStatus.OK);
            if (statuses.stream().filter(st -> st == HttpStatus.OK).count() == 2) {
                assertThat(r1.getBody().get("id")).isEqualTo(r2.getBody().get("id"));
            }
        } finally {
            pool.shutdown();
        }

        Integer count = jdbc.queryForObject(
                "SELECT COUNT(*) FROM leave_request WHERE applicant_user_id = ? AND reason = ?",
                Integer.class, userId, "并发幂等测试");
        assertThat(count).isEqualTo(1);
    }

    @Test
    void rejectedRequest_doesNotDeductBalance() {
        String requestsPath = "/oa/leave/requests";
        String draftBodyJson = writeJson(Map.of(
                "leaveTypeCode", "annual", "startDate", "2026-11-01", "endDate", "2026-11-02", "reason", "拒绝测试"
        ));
        HttpHeaders writeHeaders = signedHeaders(HttpMethod.POST, requestsPath, draftBodyJson, userId,
                List.of("oa.leave.write"), "create_leave_draft");
        writeHeaders.set("Idempotency-Key", UUID.randomUUID().toString());
        ResponseEntity<Map> draftResp = rest.exchange(url(requestsPath), HttpMethod.POST,
                new HttpEntity<>(draftBodyJson, writeHeaders), Map.class);
        long requestId = ((Number) draftResp.getBody().get("id")).longValue();

        String submitPath = "/oa/leave/requests/" + requestId + "/submit";
        HttpHeaders submitHeaders = signedHeaders(HttpMethod.POST, submitPath, null, userId,
                List.of("oa.leave.write"), "submit_leave_request");
        submitHeaders.set("Idempotency-Key", UUID.randomUUID().toString());
        rest.exchange(url(submitPath), HttpMethod.POST, new HttpEntity<>(submitHeaders), Map.class);

        String rejectPath = "/oa/leave/requests/" + requestId + "/reject";
        String rejectBodyJson = writeJson(Map.of("note", "人手不够"));
        HttpHeaders rejectHeaders = signedHeaders(HttpMethod.POST, rejectPath, rejectBodyJson, approverId, teamId,
                List.of("oa.leave.approve"), "reject_leave_request", false, true);
        rejectHeaders.set("Idempotency-Key", UUID.randomUUID().toString());
        ResponseEntity<Map> rejectResp = rest.exchange(url(rejectPath),
                HttpMethod.POST, new HttpEntity<>(rejectBodyJson, rejectHeaders), Map.class);
        assertThat(rejectResp.getBody().get("status")).isEqualTo("REJECTED");

        String balancePath = "/oa/leave/balance";
        HttpHeaders afterBalanceHeaders = signedHeaders(HttpMethod.GET, balancePath, null, userId,
                List.of("oa.leave.read"), "get_leave_balance");
        ResponseEntity<Map[]> afterBalance = rest.exchange(url(balancePath), HttpMethod.GET,
                new HttpEntity<>(afterBalanceHeaders), Map[].class);
        assertThat(afterBalance.getBody()[0].get("remainingDays")).isEqualTo(10.0);
    }

    /** 建草稿 + 提交，返回请假单 id——下面几个越权测试都要先有一条已提交的申请。 */
    private long createAndSubmit(long applicantUid) {
        String requestsPath = "/oa/leave/requests";
        String draftBodyJson = writeJson(Map.of(
                "leaveTypeCode", "annual", "startDate", "2026-11-01", "endDate", "2026-11-02", "reason", "越权测试"
        ));
        HttpHeaders writeHeaders = signedHeaders(HttpMethod.POST, requestsPath, draftBodyJson, applicantUid,
                List.of("oa.leave.write"), "create_leave_draft");
        writeHeaders.set("Idempotency-Key", UUID.randomUUID().toString());
        ResponseEntity<Map> draftResp = rest.exchange(url(requestsPath), HttpMethod.POST,
                new HttpEntity<>(draftBodyJson, writeHeaders), Map.class);
        long requestId = ((Number) draftResp.getBody().get("id")).longValue();

        String submitPath = "/oa/leave/requests/" + requestId + "/submit";
        HttpHeaders submitHeaders = signedHeaders(HttpMethod.POST, submitPath, null, applicantUid,
                List.of("oa.leave.write"), "submit_leave_request");
        submitHeaders.set("Idempotency-Key", UUID.randomUUID().toString());
        rest.exchange(url(submitPath), HttpMethod.POST, new HttpEntity<>(submitHeaders), Map.class);
        return requestId;
    }

    @Test
    void approvingOwnRequest_isRejected() {
        // applicant 自己碰巧也持有部门负责人角色（is_team_admin=true），但审批的是自己提交的单——
        // 双人审批要求这种情况也要拒绝，不能靠"角色够了"就放过。
        long requestId = createAndSubmit(userId);
        String approvePath = "/oa/leave/requests/" + requestId + "/approve";
        String bodyJson = writeJson(Map.of("note", "自己批自己"));
        HttpHeaders selfApproveHeaders = signedHeaders(HttpMethod.POST, approvePath, bodyJson, userId, teamId,
                List.of("oa.leave.approve"), "approve_leave_request", false, true);
        selfApproveHeaders.set("Idempotency-Key", UUID.randomUUID().toString());
        ResponseEntity<String> resp = rest.exchange(url(approvePath),
                HttpMethod.POST, new HttpEntity<>(bodyJson, selfApproveHeaders), String.class);
        assertThat(resp.getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
    }

    @Test
    void regularMember_cannotApprove_returns404() {
        // 有 scope（工具签了 oa.leave.approve），但既不是部门负责人也不是企业管理员——
        // 这正是报告里说的"工具能直接签发审批权限"那个漏洞，修复后必须被拒绝。
        long requestId = createAndSubmit(userId);
        String approvePath = "/oa/leave/requests/" + requestId + "/approve";
        String bodyJson = writeJson(Map.of("note", "我也想批"));
        HttpHeaders regularMemberHeaders = signedHeaders(HttpMethod.POST, approvePath, bodyJson, approverId, teamId,
                List.of("oa.leave.approve"), "approve_leave_request", false, false);
        regularMemberHeaders.set("Idempotency-Key", UUID.randomUUID().toString());
        ResponseEntity<String> resp = rest.exchange(url(approvePath),
                HttpMethod.POST, new HttpEntity<>(bodyJson, regularMemberHeaders), String.class);
        assertThat(resp.getStatusCode()).isEqualTo(HttpStatus.NOT_FOUND);
    }

    @Test
    void teamAdminOfDifferentDepartment_cannotApprove_returns404() {
        long requestId = createAndSubmit(userId); // 申请单的 teamId 是 teamId(=2)
        long otherTeamId = teamId + 100;
        String approvePath = "/oa/leave/requests/" + requestId + "/approve";
        String bodyJson = writeJson(Map.of("note", "越权审批"));
        HttpHeaders otherDeptHeaders = signedHeaders(HttpMethod.POST, approvePath, bodyJson, approverId, otherTeamId,
                List.of("oa.leave.approve"), "approve_leave_request", false, true); // 是别的部门的负责人
        otherDeptHeaders.set("Idempotency-Key", UUID.randomUUID().toString());
        ResponseEntity<String> resp = rest.exchange(url(approvePath),
                HttpMethod.POST, new HttpEntity<>(bodyJson, otherDeptHeaders), String.class);
        assertThat(resp.getStatusCode()).isEqualTo(HttpStatus.NOT_FOUND);
    }

    @Test
    void orgAdmin_canApproveAcrossDepartments() {
        long requestId = createAndSubmit(userId);
        String approvePath = "/oa/leave/requests/" + requestId + "/approve";
        String bodyJson = writeJson(Map.of("note", "企业管理员批准"));
        HttpHeaders orgAdminHeaders = signedHeaders(HttpMethod.POST, approvePath, bodyJson, approverId, null,
                List.of("oa.leave.approve"), "approve_leave_request", true, false); // 企业管理员，不属于任何具体部门
        orgAdminHeaders.set("Idempotency-Key", UUID.randomUUID().toString());
        ResponseEntity<Map> resp = rest.exchange(url(approvePath),
                HttpMethod.POST, new HttpEntity<>(bodyJson, orgAdminHeaders), Map.class);
        assertThat(resp.getStatusCode()).isEqualTo(HttpStatus.OK);
        assertThat(resp.getBody().get("status")).isEqualTo("APPROVED");
    }

    @Test
    void getStatus_ownerCanViewOwnRequest() {
        long requestId = createAndSubmit(userId);
        String statusPath = "/oa/leave/requests/" + requestId;
        HttpHeaders headers = signedHeaders(HttpMethod.GET, statusPath, null, userId,
                List.of("oa.leave.read"), "get_leave_status");
        ResponseEntity<Map> resp = rest.exchange(url(statusPath), HttpMethod.GET,
                new HttpEntity<>(headers), Map.class);
        assertThat(resp.getStatusCode()).isEqualTo(HttpStatus.OK);
    }

    @Test
    void getStatus_strangerWithoutRole_returns404() {
        long requestId = createAndSubmit(userId);
        String statusPath = "/oa/leave/requests/" + requestId;
        HttpHeaders strangerHeaders = signedHeaders(HttpMethod.GET, statusPath, null, approverId, teamId,
                List.of("oa.leave.read"), "get_leave_status", false, false);
        ResponseEntity<String> resp = rest.exchange(url(statusPath), HttpMethod.GET,
                new HttpEntity<>(strangerHeaders), String.class);
        assertThat(resp.getStatusCode()).isEqualTo(HttpStatus.NOT_FOUND);
    }

    @Test
    void getStatus_teamAdminOfSameDepartment_canView() {
        long requestId = createAndSubmit(userId);
        String statusPath = "/oa/leave/requests/" + requestId;
        HttpHeaders teamAdminHeaders = signedHeaders(HttpMethod.GET, statusPath, null, approverId, teamId,
                List.of("oa.leave.read"), "get_leave_status", false, true);
        ResponseEntity<Map> resp = rest.exchange(url(statusPath), HttpMethod.GET,
                new HttpEntity<>(teamAdminHeaders), Map.class);
        assertThat(resp.getStatusCode()).isEqualTo(HttpStatus.OK);
    }
}
