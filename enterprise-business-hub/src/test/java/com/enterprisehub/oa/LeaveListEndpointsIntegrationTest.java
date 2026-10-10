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
import java.util.concurrent.ThreadLocalRandom;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * 部门工作台里程碑1新增：/oa/leave/requests/mine 和 /oa/leave/requests/team-pending 两个
 * 列表接口——把 {@link LeaveRequestRepository} 里一直存在但没被任何 Controller/Service
 * 调用过的 findByApplicantUserIdOrderByCreatedAtDesc/findByTeamIdAndStatusOrderByCreatedAtDesc
 * 接上 REST。跟 {@link LeaveControllerIntegrationTest} 是同一种真实 HTTP + 真实 MySQL 集成
 * 测试，helper 方法故意重新写一份而不是共享/继承——这个项目里每个测试类的 signedHeaders
 * 都是独立的 private 方法，不是共享基类（历史上验证过这一点，不是疏漏）。
 */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
@ActiveProfiles("test")
class LeaveListEndpointsIntegrationTest {

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

    @BeforeEach
    void setUp() {
        userId = ThreadLocalRandom.current().nextLong(9_000_000L, 9_999_000L);
        approverId = userId + 1;
        // 随机取两个互不相同的 teamId——Java 侧不持有 team 数据本身（只是个不透明的
        // Long 标识，权限来源只在 FastAPI 一侧），不需要它们真的存在于任何表里，
        // 但要跟别的测试类可能用的固定值（比如 LeaveControllerIntegrationTest 的 2L）
        // 错开，避免并发跑测试时互相看到对方建的数据。
        teamId = ThreadLocalRandom.current().nextLong(20_000L, 29_000L);
        otherTeamId = teamId + 1;
    }

    @AfterEach
    void tearDown() {
        jdbc.update("DELETE FROM leave_request WHERE applicant_user_id IN (?, ?)", userId, approverId);
        jdbc.update("DELETE FROM leave_balance WHERE user_id IN (?, ?)", userId, approverId);
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

    /** 非 null 时覆盖签进上下文的 org_team_ids（模拟别的企业的企业管理员）。 */
    private java.util.List<Long> orgTeamIdsOverride;

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

    /** 建草稿 + 提交，返回请假单 id。 */
    private long createAndSubmit(long applicantUid, long forTeamId, String reason) {
        String requestsPath = "/oa/leave/requests";
        String draftBodyJson = writeJson(Map.of(
                "leaveTypeCode", "annual", "startDate", "2026-11-01", "endDate", "2026-11-02", "reason", reason
        ));
        HttpHeaders writeHeaders = signedHeaders(HttpMethod.POST, requestsPath, draftBodyJson, applicantUid,
                forTeamId, List.of("oa.leave.write"), "create_leave_draft", false, false);
        writeHeaders.set(LeaveController.IDEMPOTENCY_HEADER, UUID.randomUUID().toString());
        jdbc.update("INSERT INTO leave_balance (user_id, leave_type_id, year, remaining_days) VALUES (?, 1, ?, 10) "
                        + "ON DUPLICATE KEY UPDATE remaining_days = 10",
                applicantUid, Instant.now().atZone(java.time.ZoneOffset.UTC).getYear());
        ResponseEntity<Map> draftResp = rest.exchange(url(requestsPath), HttpMethod.POST,
                new HttpEntity<>(draftBodyJson, writeHeaders), Map.class);
        long requestId = ((Number) draftResp.getBody().get("id")).longValue();

        String submitPath = "/oa/leave/requests/" + requestId + "/submit";
        HttpHeaders submitHeaders = signedHeaders(HttpMethod.POST, submitPath, null, applicantUid,
                forTeamId, List.of("oa.leave.write"), "submit_leave_request", false, false);
        submitHeaders.set(LeaveController.IDEMPOTENCY_HEADER, UUID.randomUUID().toString());
        rest.exchange(url(submitPath), HttpMethod.POST, new HttpEntity<>(submitHeaders), Map.class);
        return requestId;
    }

    private long createDraftOnly(long applicantUid, long forTeamId, String reason) {
        String requestsPath = "/oa/leave/requests";
        String draftBodyJson = writeJson(Map.of(
                "leaveTypeCode", "annual", "startDate", "2026-11-01", "endDate", "2026-11-02", "reason", reason
        ));
        HttpHeaders writeHeaders = signedHeaders(HttpMethod.POST, requestsPath, draftBodyJson, applicantUid,
                forTeamId, List.of("oa.leave.write"), "create_leave_draft", false, false);
        writeHeaders.set(LeaveController.IDEMPOTENCY_HEADER, UUID.randomUUID().toString());
        ResponseEntity<Map> draftResp = rest.exchange(url(requestsPath), HttpMethod.POST,
                new HttpEntity<>(draftBodyJson, writeHeaders), Map.class);
        return ((Number) draftResp.getBody().get("id")).longValue();
    }

    @Test
    void myRequests_returnsOnlyOwnRequests_orderedNewestFirst() {
        long myId = createAndSubmit(userId, teamId, "我的第一条");
        long myId2 = createAndSubmit(userId, teamId, "我的第二条");
        createAndSubmit(approverId, teamId, "别人的申请"); // 同一个部门，但申请人不同

        String path = "/oa/leave/requests/mine";
        HttpHeaders headers = signedHeaders(HttpMethod.GET, path, null, userId, teamId,
                List.of("oa.leave.read"), "get_my_leave_requests", false, false);
        ResponseEntity<Map[]> resp = rest.exchange(url(path), HttpMethod.GET, new HttpEntity<>(headers), Map[].class);

        assertThat(resp.getStatusCode()).isEqualTo(HttpStatus.OK);
        List<Long> ids = List.of(resp.getBody()).stream().map(m -> ((Number) m.get("id")).longValue()).toList();
        assertThat(ids).containsExactly(myId2, myId); // 按创建时间倒序，且只有自己的两条
    }

    @Test
    void myRequests_emptyList_whenNoRequests() {
        String path = "/oa/leave/requests/mine";
        HttpHeaders headers = signedHeaders(HttpMethod.GET, path, null, userId, teamId,
                List.of("oa.leave.read"), "get_my_leave_requests", false, false);
        ResponseEntity<Map[]> resp = rest.exchange(url(path), HttpMethod.GET, new HttpEntity<>(headers), Map[].class);

        assertThat(resp.getStatusCode()).isEqualTo(HttpStatus.OK);
        assertThat(resp.getBody()).isEmpty();
    }

    @Test
    void teamPending_excludesOtherTeams_andExcludesNonSubmittedStatuses() {
        // 核心断言：别的部门的单子、还没提交的草稿，都不该出现在结果里——不是只看
        // "自己部门的在不在"，这样以后过滤条件被误删/写错，测试真的会失败。
        long pendingInMyTeam = createAndSubmit(userId, teamId, "本部门待审批");
        createAndSubmit(userId, otherTeamId, "别的部门待审批"); // 同一个申请人，但部门不同
        createDraftOnly(userId, teamId, "本部门还没提交的草稿"); // 同部门但状态是 DRAFT，不该出现

        String path = "/oa/leave/requests/team-pending?teamId=" + teamId;
        HttpHeaders headers = signedHeaders(HttpMethod.GET, path, null, approverId, teamId,
                List.of("oa.leave.read"), "get_team_pending_leave_requests", false, true);
        ResponseEntity<Map[]> resp = rest.exchange(url(path), HttpMethod.GET, new HttpEntity<>(headers), Map[].class);

        assertThat(resp.getStatusCode()).isEqualTo(HttpStatus.OK);
        List<Long> ids = List.of(resp.getBody()).stream().map(m -> ((Number) m.get("id")).longValue()).toList();
        assertThat(ids).containsExactly(pendingInMyTeam);
    }

    @Test
    void teamPending_regularMemberWithoutTeamAdminRole_returns404() {
        createAndSubmit(userId, teamId, "待审批");
        String path = "/oa/leave/requests/team-pending?teamId=" + teamId;
        HttpHeaders headers = signedHeaders(HttpMethod.GET, path, null, approverId, teamId,
                List.of("oa.leave.read"), "get_team_pending_leave_requests", false, false); // 有 scope，但不是负责人
        ResponseEntity<String> resp = rest.exchange(url(path), HttpMethod.GET, new HttpEntity<>(headers), String.class);
        assertThat(resp.getStatusCode()).isEqualTo(HttpStatus.NOT_FOUND);
    }

    @Test
    void teamPending_teamAdminOfDifferentDepartment_returns404() {
        createAndSubmit(userId, teamId, "待审批");
        String path = "/oa/leave/requests/team-pending?teamId=" + teamId;
        // approverId 是 otherTeamId 的负责人，不是 teamId 的负责人——不能查 teamId 的待审批。
        HttpHeaders headers = signedHeaders(HttpMethod.GET, path, null, approverId, otherTeamId,
                List.of("oa.leave.read"), "get_team_pending_leave_requests", false, true);
        ResponseEntity<String> resp = rest.exchange(url(path), HttpMethod.GET, new HttpEntity<>(headers), String.class);
        assertThat(resp.getStatusCode()).isEqualTo(HttpStatus.NOT_FOUND);
    }

    @Test
    void teamPending_orgAdminCanQueryAnyTeam_viaTeamIdParam() {
        long pending = createAndSubmit(userId, teamId, "企业管理员应该能看到");
        String path = "/oa/leave/requests/team-pending?teamId=" + teamId;
        HttpHeaders headers = signedHeaders(HttpMethod.GET, path, null, approverId, null,
                List.of("oa.leave.read"), "get_team_pending_leave_requests", true, false); // 企业管理员，不属于任何具体部门
        ResponseEntity<Map[]> resp = rest.exchange(url(path), HttpMethod.GET, new HttpEntity<>(headers), Map[].class);

        assertThat(resp.getStatusCode()).isEqualTo(HttpStatus.OK);
        List<Long> ids = List.of(resp.getBody()).stream().map(m -> ((Number) m.get("id")).longValue()).toList();
        assertThat(ids).containsExactly(pending);
    }
}
