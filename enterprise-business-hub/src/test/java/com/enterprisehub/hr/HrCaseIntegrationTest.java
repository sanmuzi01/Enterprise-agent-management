package com.enterprisehub.hr;

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
import java.time.LocalDate;
import java.time.ZoneId;
import java.util.*;
import java.util.concurrent.*;

import static org.assertj.core.api.Assertions.assertThat;

/** 人事入转调离的真实 HTTP 集成测试（真实 MySQL + 签名上下文）。ID 段 2_000_000–2_990_000。 */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
@ActiveProfiles("test")
class HrCaseIntegrationTest {
    private static final String HMAC_SECRET = "test-secret-not-for-production";
    private static final ObjectMapper MAPPER = new ObjectMapper();
    private static final List<String> SCOPES = List.of("hr.case.read", "hr.case.write");
    private static final LocalDate TODAY = LocalDate.now(ZoneId.of("Asia/Shanghai"));

    @LocalServerPort
    private int port;
    @Autowired
    private TestRestTemplate rest;
    @Autowired
    private JdbcTemplate jdbc;

    private long hr1, hr2, head, employee, itUser, finUser, stranger;
    private long salesTeam, otherTeam, foreignTeam;

    @BeforeEach
    void setUp() {
        hr1 = ThreadLocalRandom.current().nextLong(2_000_000L, 2_980_000L);
        hr2 = hr1 + 1;
        head = hr1 + 2;
        employee = hr1 + 3;
        itUser = hr1 + 4;
        finUser = hr1 + 5;
        stranger = hr1 + 6;
        salesTeam = hr1;
        otherTeam = hr1 + 1;
        foreignTeam = hr1 + 9;
    }

    @AfterEach
    void tearDown() {
        String teams = salesTeam + "," + otherTeam + "," + foreignTeam;
        jdbc.update("DELETE FROM hr_case_task WHERE case_id IN (SELECT id FROM hr_case WHERE team_id IN (" + teams + "))");
        jdbc.update("DELETE FROM hr_case WHERE team_id IN (" + teams + ")");
        jdbc.update("DELETE FROM it_device_event WHERE device_id IN (SELECT id FROM it_device WHERE managing_team_id IN (" + teams + "))");
        jdbc.update("DELETE FROM it_device WHERE managing_team_id IN (" + teams + ")");
        jdbc.update("DELETE FROM expense_line WHERE expense_claim_id IN (SELECT id FROM expense_claim WHERE applicant_user_id = ?)", employee);
        jdbc.update("DELETE FROM expense_claim WHERE applicant_user_id = ?", employee);
        jdbc.update("DELETE FROM leave_balance WHERE user_id = ?", employee);
        jdbc.update("DELETE FROM audit_event WHERE user_id BETWEEN ? AND ?", hr1, hr1 + 6);
    }

    // ------------------------------------------------------------------ 请求辅助

    /** 身份：who 是用户；roles 是部门办理角色；heads 是负责的部门。 */
    private record Who(long uid, String roles, String heads) {
    }

    private Who hr(long uid) { return new Who(uid, "HR", ""); }
    private Who headOf() { return new Who(head, "", String.valueOf(salesTeam)); }
    private Who plain(long uid) { return new Who(uid, "", ""); }

    private String query(Who who) {
        StringBuilder q = new StringBuilder("scopeTeamIds=" + salesTeam + "," + otherTeam);
        if (!who.roles().isEmpty()) q.append("&roles=").append(who.roles());
        if (!who.heads().isEmpty()) q.append("&headTeamIds=").append(who.heads());
        return q.toString();
    }

    private ResponseEntity<String> call(HttpMethod method, String path, Object body, Who who) {
        String full = path + (path.contains("?") ? "&" : "?") + query(who);
        try {
            String json = body == null ? null : MAPPER.writeValueAsString(body);
            byte[] bytes = json == null ? new byte[0] : json.getBytes(StandardCharsets.UTF_8);
            Map<String, Object> ctx = new HashMap<>();
            ctx.put("user_id", who.uid());
            ctx.put("team_id", null);
            ctx.put("scopes", SCOPES);
            ctx.put("operation", "hr_test");
            ctx.put("is_org_admin", false);
            ctx.put("is_team_admin", false);
            ctx.put("trace_id", UUID.randomUUID().toString());
            ctx.put("timestamp", Instant.now().getEpochSecond());
            ctx.put("nonce", UUID.randomUUID().toString().replace("-", ""));
            ctx.put("method", method.name());
            ctx.put("path", full);
            ctx.put("body_sha256", HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes)));
            String b64 = Base64.getEncoder().encodeToString(MAPPER.writeValueAsString(ctx).getBytes(StandardCharsets.UTF_8));
            javax.crypto.Mac mac = javax.crypto.Mac.getInstance("HmacSHA256");
            mac.init(new javax.crypto.spec.SecretKeySpec(HMAC_SECRET.getBytes(StandardCharsets.UTF_8), "HmacSHA256"));
            HttpHeaders headers = new HttpHeaders();
            headers.set("X-Context", b64);
            headers.set("X-Signature", HexFormat.of().formatHex(mac.doFinal(b64.getBytes(StandardCharsets.UTF_8))));
            headers.setContentType(org.springframework.http.MediaType.APPLICATION_JSON);
            headers.set("Idempotency-Key", UUID.randomUUID().toString());
            return rest.exchange(URI.create("http://127.0.0.1:" + port + full), method, new HttpEntity<>(json, headers), String.class);
        } catch (Exception e) {
            throw new RuntimeException(e);
        }
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> map(ResponseEntity<String> r) {
        try {
            return MAPPER.readValue(r.getBody(), Map.class);
        } catch (Exception e) {
            throw new RuntimeException(r.getStatusCode() + " " + r.getBody(), e);
        }
    }

    @SuppressWarnings("unchecked")
    private List<Map<String, Object>> list(ResponseEntity<String> r) {
        try {
            return MAPPER.readValue(r.getBody(), List.class);
        } catch (Exception e) {
            throw new RuntimeException(r.getStatusCode() + " " + r.getBody(), e);
        }
    }

    private Map<String, Object> body(String type, Long target, boolean isHead) {
        Map<String, Object> b = new HashMap<>();
        b.put("caseType", type);
        b.put("employeeUserId", employee);
        b.put("teamId", salesTeam);
        b.put("targetTeamId", target);
        b.put("effectiveDate", TODAY.plusDays(7).toString());
        b.put("position", "销售专员");
        b.put("employeeIsHead", isHead);
        return b;
    }

    private long create(String type, Long target) {
        ResponseEntity<String> r = call(HttpMethod.POST, "/hr/cases", body(type, target, false), hr(hr1));
        assertThat(r.getStatusCode()).as(r.getBody()).isEqualTo(HttpStatus.OK);
        return ((Number) map(r).get("id")).longValue();
    }

    private Map<String, Object> approve(long id) {
        ResponseEntity<String> r = call(HttpMethod.POST, "/hr/cases/" + id + "/approve", Map.of("note", "同意"), headOf());
        assertThat(r.getStatusCode()).as(r.getBody()).isEqualTo(HttpStatus.OK);
        return map(r);
    }

    @SuppressWarnings("unchecked")
    private List<Map<String, Object>> tasks(Map<String, Object> c) {
        return (List<Map<String, Object>>) c.get("tasks");
    }

    @SuppressWarnings("unchecked")
    private List<String> checkCodes(Map<String, Object> c) {
        return ((List<Map<String, Object>>) c.get("checks")).stream().map(m -> (String) m.get("code")).toList();
    }

    private Who ownerOf(String owner) {
        return switch (owner) {
            case "HR" -> hr(hr2);
            case "IT" -> new Who(itUser, "IT", "");
            case "FINANCE" -> new Who(finUser, "FINANCE", "");
            case "MANAGER" -> headOf();
            default -> plain(employee);
        };
    }

    private void finishAll(long id, Map<String, Object> c) {
        for (Map<String, Object> t : tasks(c)) {
            long taskId = ((Number) t.get("id")).longValue();
            ResponseEntity<String> r = call(HttpMethod.POST, "/hr/cases/" + id + "/tasks/" + taskId + "/done", Map.of("note", "已办"),
                    ownerOf((String) t.get("owner")));
            assertThat(r.getStatusCode()).as(t.get("title") + " " + r.getBody()).isEqualTo(HttpStatus.OK);
        }
    }

    // ------------------------------------------------------------------ 全流程

    @Test
    void onboarding_create_approve_tasksByEachParty_complete() {
        long id = create("ONBOARDING", null);
        Map<String, Object> pending = map(call(HttpMethod.GET, "/hr/cases/" + id, null, hr(hr1)));
        assertThat(pending).containsEntry("status", "PENDING_APPROVAL").containsEntry("caseTypeLabel", "入职");
        assertThat(checkCodes(pending)).contains("NO_LEAVE_BALANCE");
        assertThat(tasks(pending)).isEmpty();

        assertThat(list(call(HttpMethod.GET, "/hr/cases?view=approval", null, headOf()))).extracting(m -> ((Number) m.get("id")).longValue()).contains(id);
        Map<String, Object> approved = approve(id);
        assertThat(approved).containsEntry("status", "IN_PROGRESS");
        assertThat(tasks(approved)).hasSize(7);
        assertThat(tasks(approved).stream().map(t -> t.get("owner")).distinct().toList())
                .containsExactlyInAnyOrder("HR", "IT", "FINANCE", "MANAGER", "EMPLOYEE");

        // 每个办理方在“我的任务”里只看到自己的
        assertThat(list(call(HttpMethod.GET, "/hr/cases/my-tasks", null, new Who(itUser, "IT", "")))).hasSize(2)
                .allMatch(t -> "IT".equals(t.get("owner")));
        assertThat(list(call(HttpMethod.GET, "/hr/cases/my-tasks", null, plain(employee)))).hasSize(1);
        assertThat(list(call(HttpMethod.GET, "/hr/cases/my-tasks", null, plain(stranger)))).isEmpty();

        // 不能替别人办
        long itTask = tasks(approved).stream().filter(t -> "IT".equals(t.get("owner"))).map(t -> ((Number) t.get("id")).longValue()).findFirst().orElseThrow();
        assertThat(call(HttpMethod.POST, "/hr/cases/" + id + "/tasks/" + itTask + "/done", null, new Who(finUser, "FINANCE", "")).getStatusCode())
                .isEqualTo(HttpStatus.FORBIDDEN);
        // 必办任务不能跳过；可选任务跳过要写原因
        assertThat(call(HttpMethod.POST, "/hr/cases/" + id + "/tasks/" + itTask + "/skip", Map.of("note", "不需要"), new Who(itUser, "IT", "")).getStatusCode())
                .isEqualTo(HttpStatus.BAD_REQUEST);
        // 必办任务没办完不能办结
        ResponseEntity<String> early = call(HttpMethod.POST, "/hr/cases/" + id + "/complete", null, hr(hr1));
        assertThat(early.getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
        assertThat(early.getBody()).contains("必办任务没有完成");

        finishAll(id, approved);
        assertThat(call(HttpMethod.POST, "/hr/cases/" + id + "/complete", null, plain(employee)).getStatusCode())
                .as("非 HR 不能办结").isEqualTo(HttpStatus.FORBIDDEN);
        Map<String, Object> done = map(call(HttpMethod.POST, "/hr/cases/" + id + "/complete", null, hr(hr1)));
        assertThat(done).containsEntry("status", "COMPLETED").containsEntry("effectPending", false);

        // 入职办结后再办入职会提示重复
        long again = create("ONBOARDING", null);
        assertThat(checkCodes(map(call(HttpMethod.GET, "/hr/cases/" + again, null, hr(hr1))))).contains("ALREADY_ONBOARDED");
    }

    @Test
    void approvalRules() {
        long id = create("PROBATION", null);
        assertThat(call(HttpMethod.POST, "/hr/cases/" + id + "/approve", null, plain(stranger)).getStatusCode()).isEqualTo(HttpStatus.NOT_FOUND);
        assertThat(call(HttpMethod.POST, "/hr/cases/" + id + "/approve", null, new Who(stranger, "", String.valueOf(otherTeam))).getStatusCode())
                .as("别的部门负责人").isEqualTo(HttpStatus.NOT_FOUND);
        assertThat(call(HttpMethod.POST, "/hr/cases/" + id + "/approve", null, new Who(employee, "", String.valueOf(salesTeam))).getStatusCode())
                .as("员工本人是负责人也不能批自己的").isEqualTo(HttpStatus.BAD_REQUEST);
        assertThat(call(HttpMethod.POST, "/hr/cases/" + id + "/approve", null, new Who(hr1, "HR", String.valueOf(salesTeam))).getStatusCode())
                .as("发起人不能批准自己发起的").isEqualTo(HttpStatus.BAD_REQUEST);
        Map<String, Object> rejected = map(call(HttpMethod.POST, "/hr/cases/" + id + "/reject", Map.of("note", "试用期考核未完成"), headOf()));
        assertThat(rejected).containsEntry("status", "REJECTED").containsEntry("decisionNote", "试用期考核未完成");
        assertThat(call(HttpMethod.POST, "/hr/cases/" + id + "/approve", null, headOf()).getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
    }

    @Test
    void onlyHrCanInitiate_andNotForThemselves_andWithinScope() {
        assertThat(call(HttpMethod.POST, "/hr/cases", body("ONBOARDING", null, false), headOf()).getStatusCode()).isEqualTo(HttpStatus.FORBIDDEN);
        assertThat(call(HttpMethod.POST, "/hr/cases", body("ONBOARDING", null, false), new Who(itUser, "IT", "")).getStatusCode()).isEqualTo(HttpStatus.FORBIDDEN);
        Map<String, Object> self = body("ONBOARDING", null, false);
        self.put("employeeUserId", hr1);
        assertThat(call(HttpMethod.POST, "/hr/cases", self, hr(hr1)).getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
        Map<String, Object> foreign = body("ONBOARDING", null, false);
        foreign.put("teamId", foreignTeam);
        assertThat(call(HttpMethod.POST, "/hr/cases", foreign, hr(hr1)).getStatusCode()).isEqualTo(HttpStatus.NOT_FOUND);
        assertThat(call(HttpMethod.POST, "/hr/cases", body("TRANSFER", foreignTeam, false), hr(hr1)).getStatusCode()).isEqualTo(HttpStatus.NOT_FOUND);
        assertThat(call(HttpMethod.GET, "/hr/cases?roles=GOD", null, plain(stranger)).getStatusCode())
                .isEqualTo(HttpStatus.BAD_REQUEST);
        assertThat(call(HttpMethod.GET, "/hr/cases", null, plain(stranger)).getStatusCode()).as("非 HR 不能看全部").isEqualTo(HttpStatus.FORBIDDEN);
    }

    @Test
    void visibility() {
        long id = create("PROBATION", null);
        assertThat(call(HttpMethod.GET, "/hr/cases/" + id, null, plain(employee)).getStatusCode()).as("员工本人能看").isEqualTo(HttpStatus.OK);
        assertThat(call(HttpMethod.GET, "/hr/cases/" + id, null, headOf()).getStatusCode()).isEqualTo(HttpStatus.OK);
        assertThat(call(HttpMethod.GET, "/hr/cases/" + id, null, plain(stranger)).getStatusCode()).isEqualTo(HttpStatus.NOT_FOUND);
        assertThat(list(call(HttpMethod.GET, "/hr/cases?view=mine", null, plain(employee)))).hasSize(1);
        assertThat(list(call(HttpMethod.GET, "/hr/cases?view=approval", null, new Who(stranger, "", String.valueOf(otherTeam))))).isEmpty();
    }

    // ------------------------------------------------------------------ 规则检查

    @Test
    void duplicateAndConflictingCasesAreBlocked() {
        create("OFFBOARDING", null);
        ResponseEntity<String> dup = call(HttpMethod.POST, "/hr/cases", body("OFFBOARDING", null, false), hr(hr1));
        assertThat(dup.getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
        assertThat(dup.getBody()).contains("不能重复办理");
        ResponseEntity<String> transfer = call(HttpMethod.POST, "/hr/cases", body("TRANSFER", otherTeam, false), hr(hr1));
        assertThat(transfer.getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
        assertThat(transfer.getBody()).contains("正在办理离职");
        Map<String, Object> pre = map(call(HttpMethod.POST, "/hr/cases/precheck", body("TRANSFER", otherTeam, false), hr(hr1)));
        assertThat(pre).containsEntry("canSubmit", false).containsEntry("riskLevel", "BLOCK");
    }

    @Test
    void transferChecks() {
        assertThat(call(HttpMethod.POST, "/hr/cases", body("TRANSFER", salesTeam, false), hr(hr1)).getStatusCode())
                .as("目标部门与当前相同").isEqualTo(HttpStatus.BAD_REQUEST);
        jdbc.update("INSERT INTO expense_claim (applicant_user_id, team_id, status, total_amount, created_at) VALUES (?, ?, 'SUBMITTED', 88, NOW())",
                employee, salesTeam);
        Map<String, Object> pre = map(call(HttpMethod.POST, "/hr/cases/precheck", body("TRANSFER", otherTeam, true), hr(hr1)));
        @SuppressWarnings("unchecked")
        List<String> codes = ((List<Map<String, Object>>) pre.get("checks")).stream().map(m -> (String) m.get("code")).toList();
        assertThat(codes).contains("PENDING_CLAIMS", "EMPLOYEE_IS_HEAD");
        assertThat(pre).containsEntry("riskLevel", "WARN").containsEntry("canSubmit", true);
        long id = create("TRANSFER", otherTeam);
        Map<String, Object> c = approve(id);
        finishAll(id, c);
        Map<String, Object> done = map(call(HttpMethod.POST, "/hr/cases/" + id + "/complete", null, hr(hr1)));
        assertThat(done).containsEntry("status", "COMPLETED").containsEntry("effectPending", true);
        Map<String, Object> applied = map(call(HttpMethod.POST, "/hr/cases/" + id + "/effect-applied", null, hr(hr1)));
        assertThat(applied).containsEntry("effectPending", false);
        assertThat(call(HttpMethod.POST, "/hr/cases/" + id + "/effect-applied", null, hr(hr1)).getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
    }

    @Test
    void offboardingCannotCompleteWhileDeviceStillAssigned() {
        jdbc.update("INSERT INTO it_device (asset_no, device_type, model, status, assignee_user_id, managing_team_id, created_at, updated_at) "
                + "VALUES (?, 'LAPTOP', 'ThinkPad', 'ASSIGNED', ?, ?, NOW(), NOW())", "HR-" + employee, employee, salesTeam);
        long id = create("OFFBOARDING", null);
        Map<String, Object> c = approve(id);
        assertThat(checkCodes(c)).contains("DEVICES_ASSIGNED");
        finishAll(id, c);
        ResponseEntity<String> blocked = call(HttpMethod.POST, "/hr/cases/" + id + "/complete", null, hr(hr1));
        assertThat(blocked.getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
        assertThat(blocked.getBody()).contains("HR-" + employee).contains("未收回");
        jdbc.update("UPDATE it_device SET status = 'IN_STOCK', assignee_user_id = NULL WHERE asset_no = ?", "HR-" + employee);
        Map<String, Object> done = map(call(HttpMethod.POST, "/hr/cases/" + id + "/complete", null, hr(hr1)));
        assertThat(done).containsEntry("status", "COMPLETED").containsEntry("effectPending", true);
    }

    @Test
    void optionalTaskSkipNeedsReason_andTaskCanOnlyBeDoneOnce() {
        long id = create("ONBOARDING", null);
        Map<String, Object> c = approve(id);
        Map<String, Object> optional = tasks(c).stream().filter(t -> !Boolean.TRUE.equals(t.get("required"))).findFirst().orElseThrow();
        long taskId = ((Number) optional.get("id")).longValue();
        Who it = new Who(itUser, "IT", "");
        assertThat(call(HttpMethod.POST, "/hr/cases/" + id + "/tasks/" + taskId + "/skip", null, it).getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
        Map<String, Object> skipped = map(call(HttpMethod.POST, "/hr/cases/" + id + "/tasks/" + taskId + "/skip", Map.of("note", "自带电脑"), it));
        assertThat(tasks(skipped).stream().filter(t -> ((Number) t.get("id")).longValue() == taskId).findFirst().orElseThrow())
                .containsEntry("status", "SKIPPED").containsEntry("note", "自带电脑");
        assertThat(call(HttpMethod.POST, "/hr/cases/" + id + "/tasks/" + taskId + "/done", null, it).getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
    }

    @Test
    void cancelAndSummary() {
        long id = create("PROBATION", null);
        assertThat(call(HttpMethod.POST, "/hr/cases/" + id + "/cancel", null, plain(employee)).getStatusCode()).isEqualTo(HttpStatus.NOT_FOUND);
        assertThat(map(call(HttpMethod.POST, "/hr/cases/" + id + "/cancel", null, hr(hr2)))).containsEntry("status", "CANCELLED");
        long onboarding = create("ONBOARDING", null);
        approve(onboarding);
        jdbc.update("UPDATE hr_case_task SET due_date = DATE_SUB(CURDATE(), INTERVAL 3 DAY) WHERE case_id = ? AND seq = 1", onboarding);
        Map<String, Object> summary = map(call(HttpMethod.GET, "/hr/cases/summary", null, hr(hr1)));
        assertThat(((Number) summary.get("overdueTasks")).intValue()).isEqualTo(1);
        @SuppressWarnings("unchecked")
        Map<String, Map<String, Object>> byType = (Map<String, Map<String, Object>>) summary.get("byType");
        assertThat(((Number) byType.get("PROBATION").get("CANCELLED")).intValue()).isEqualTo(1);
        assertThat(((Number) byType.get("ONBOARDING").get("IN_PROGRESS")).intValue()).isEqualTo(1);
    }

    @Test
    void concurrentApprovalCreatesTasksOnce() throws Exception {
        long id = create("ONBOARDING", null);
        ExecutorService pool = Executors.newFixedThreadPool(2);
        try {
            Callable<HttpStatus> approve = () -> (HttpStatus) call(HttpMethod.POST, "/hr/cases/" + id + "/approve", null, headOf()).getStatusCode();
            Future<HttpStatus> a = pool.submit(approve);
            Future<HttpStatus> b = pool.submit(approve);
            assertThat(List.of(a.get(), b.get())).containsExactlyInAnyOrder(HttpStatus.OK, HttpStatus.BAD_REQUEST);
        } finally {
            pool.shutdownNow();
        }
        assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM hr_case_task WHERE case_id = ?", Integer.class, id)).isEqualTo(7);
    }
}
