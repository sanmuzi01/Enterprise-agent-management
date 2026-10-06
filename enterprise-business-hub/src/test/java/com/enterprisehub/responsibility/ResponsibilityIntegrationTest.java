package com.enterprisehub.responsibility;

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

import java.lang.reflect.Method;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.Instant;
import java.time.LocalDate;
import java.time.ZoneId;
import java.util.*;
import java.util.concurrent.ThreadLocalRandom;

import static org.assertj.core.api.Assertions.assertThat;

/** 部门责任执行的真实 HTTP 集成测试（真实 MySQL + 签名上下文）。ID 段 3_000_000–3_980_000。 */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
@ActiveProfiles("test")
class ResponsibilityIntegrationTest {
    private static final String HMAC_SECRET = "test-secret-not-for-production";
    private static final ObjectMapper MAPPER = new ObjectMapper();
    private static final List<String> SCOPES = List.of("responsibility.read", "responsibility.write");
    private static final LocalDate TODAY = LocalDate.now(ZoneId.of("Asia/Shanghai"));

    @LocalServerPort
    private int port;
    @Autowired
    private TestRestTemplate rest;
    @Autowired
    private JdbcTemplate jdbc;

    private long head, alice, bob, carol, dave, stranger;
    private long team, otherTeam, foreignTeam;

    @BeforeEach
    void setUp() {
        long base = ThreadLocalRandom.current().nextLong(3_000_000L, 3_980_000L);
        head = base;
        alice = base + 1;
        bob = base + 2;
        carol = base + 3;
        dave = base + 4;
        stranger = base + 5;
        team = base;
        otherTeam = base + 1;
        foreignTeam = base + 9;
    }

    @AfterEach
    void tearDown() {
        String teams = team + "," + otherTeam + "," + foreignTeam;
        String plans = "SELECT id FROM responsibility_plan WHERE team_id IN (" + teams + ")";
        String taskIds = "SELECT id FROM responsibility_task WHERE team_id IN (" + teams + ")";
        jdbc.update("DELETE FROM responsibility_event WHERE plan_id IN (" + plans + ")");
        jdbc.update("DELETE FROM responsibility_deliverable WHERE task_id IN (" + taskIds + ")");
        jdbc.update("DELETE FROM responsibility_dependency WHERE task_id IN (" + taskIds + ")");
        jdbc.update("DELETE FROM responsibility_collaborator WHERE task_id IN (" + taskIds + ")");
        jdbc.update("DELETE FROM responsibility_task WHERE team_id IN (" + teams + ")");
        jdbc.update("DELETE FROM responsibility_plan WHERE team_id IN (" + teams + ")");
        jdbc.update("DELETE FROM audit_event WHERE user_id BETWEEN ? AND ?", head, head + 5);
    }

    // ------------------------------------------------------------------ 请求辅助

    /** 身份：members 是仍有效的成员部门，heads 是负责的部门。 */
    private record Who(long uid, List<Long> members, List<Long> heads) {
    }

    private Who headOf() { return new Who(head, List.of(team), List.of(team)); }
    private Who member(long uid) { return new Who(uid, List.of(team), List.of()); }
    private Who left(long uid) { return new Who(uid, List.of(otherTeam), List.of()); }

    private Map<String, Object> eligible() {
        return Map.of("memberIds", List.of(head, alice, bob), "reviewerIds", List.of(head, alice, bob, carol));
    }

    private String query(Who who) {
        StringBuilder q = new StringBuilder("scopeTeamIds=" + team + "," + otherTeam);
        if (!who.members().isEmpty()) q.append("&memberTeamIds=").append(String.join(",", who.members().stream().map(String::valueOf).toList()));
        if (!who.heads().isEmpty()) q.append("&headTeamIds=").append(String.join(",", who.heads().stream().map(String::valueOf).toList()));
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
            ctx.put("operation", "resp_test");
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

    private ResponseEntity<String> post(String path, Object body, Who who) { return call(HttpMethod.POST, path, body, who); }
    private ResponseEntity<String> get(String path, Who who) { return call(HttpMethod.GET, path, null, who); }

    private static Map<String, Object> taskBody(String title, Long resp, Long reviewer, String due, String deliverable, String criteria) {
        Map<String, Object> m = new HashMap<>();
        m.put("title", title);
        m.put("responsibleUserId", resp);
        m.put("reviewerUserId", reviewer);
        m.put("dueDate", due);
        m.put("deliverable", deliverable);
        m.put("acceptanceCriteria", criteria);
        m.put("priority", "NORMAL");
        m.put("evidence", "原文：" + title);
        return m;
    }

    private String due(int days) { return TODAY.plusDays(days).toString(); }

    private Map<String, Object> planBody(List<Map<String, Object>> tasks, String workId) {
        Map<String, Object> m = new HashMap<>();
        m.put("teamId", team);
        m.put("title", "十月例会责任计划");
        m.put("sourceType", "MEETING");
        m.put("sourceText", "原文：完成新版首页联调");
        m.put("summary", "会议决定本月发布新版页面");
        m.put("decisions", List.of(Map.of("content", "本月发布", "evidence", "本月发布")));
        m.put("unresolved", List.of("原文没有说明最终验收人"));
        m.put("tasks", tasks);
        m.put("automationWorkId", workId);
        m.put("eligible", eligible());
        return m;
    }

    private Map<String, Object> createPlan(List<Map<String, Object>> tasks) {
        ResponseEntity<String> r = post("/responsibility/plans", planBody(tasks, null), headOf());
        assertThat(r.getStatusCode()).as(r.getBody()).isEqualTo(HttpStatus.OK);
        return map(r);
    }

    private Map<String, Object> fullTask(String title) {
        return taskBody(title, alice, carol, due(7), "可部署的前端构建包", "通过测试环境回归且无阻断问题");
    }

    @SuppressWarnings("unchecked")
    private long taskId(Map<String, Object> plan, int index) {
        return ((Number) ((List<Map<String, Object>>) plan.get("tasks")).get(index).get("id")).longValue();
    }

    private Map<String, Object> publishedPlan(String... titles) {
        List<Map<String, Object>> tasks = new ArrayList<>();
        for (String t : titles) tasks.add(fullTask(t));
        Map<String, Object> plan = createPlan(tasks);
        ResponseEntity<String> r = post("/responsibility/plans/" + plan.get("id") + "/publish", Map.of("eligible", eligible()), headOf());
        assertThat(r.getStatusCode()).as(r.getBody()).isEqualTo(HttpStatus.OK);
        return map(r);
    }

    private ResponseEntity<String> act(long task, String action, Map<String, Object> body, Who who) {
        return post("/responsibility/tasks/" + task + "/" + action, body == null ? Map.of() : body, who);
    }

    @SuppressWarnings("unchecked")
    private String statusOf(long task, Who who) {
        return (String) ((Map<String, Object>) map(get("/responsibility/tasks/" + task, who)).get("task")).get("status");
    }

    // ------------------------------------------------------------------ 草稿与完整性

    @Test
    void draftKeepsMissingFieldsAndPublishIsBlockedUntilComplete() {
        Map<String, Object> noOwner = taskBody("整理客户反馈清单", null, carol, due(5), "反馈清单", "覆盖本月全部反馈");
        Map<String, Object> noCriteria = taskBody("完成新版首页联调", alice, carol, due(7), "前端构建包", null);
        Map<String, Object> plan = createPlan(List.of(noOwner, noCriteria, fullTask("编写发布说明文档")));
        assertThat(plan.get("status")).isEqualTo("DRAFT");
        assertThat(plan.get("canPublish")).isEqualTo(false);
        assertThat(plan.get("blockerCount")).isEqualTo(2);
        assertThat(plan.get("missingAtCreation")).isEqualTo(2);

        ResponseEntity<String> blocked = post("/responsibility/plans/" + plan.get("id") + "/publish", Map.of("eligible", eligible()), headOf());
        assertThat(blocked.getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
        assertThat(blocked.getBody()).contains("还不能发布").contains("主责员工").contains("验收标准");

        // 补全后可发布
        Map<String, Object> fixed1 = fullTask("整理客户反馈清单");
        Map<String, Object> fixed2 = fullTask("完成新版首页联调");
        assertThat(post("/responsibility/tasks/" + taskId(plan, 0) + "/edit", Map.of("task", fixed1, "eligible", eligible()), headOf()).getStatusCode()).isEqualTo(HttpStatus.OK);
        assertThat(post("/responsibility/tasks/" + taskId(plan, 1) + "/edit", Map.of("task", fixed2, "eligible", eligible()), headOf()).getStatusCode()).isEqualTo(HttpStatus.OK);
        Map<String, Object> ready = map(get("/responsibility/plans/" + plan.get("id"), headOf()));
        assertThat(ready.get("canPublish")).isEqualTo(true);
        Map<String, Object> published = map(post("/responsibility/plans/" + plan.get("id") + "/publish", Map.of("eligible", eligible()), headOf()));
        assertThat(published.get("status")).isEqualTo("PUBLISHED");
        assertThat(statusOf(taskId(plan, 0), headOf())).isEqualTo("PENDING_ACCEPT");
    }

    @Test
    void sameAutomationWorkCreatesOnlyOnePlan() {
        Map<String, Object> body = planBody(List.of(fullTask("联调首页")), "work-" + UUID.randomUUID().toString().substring(0, 8));
        Map<String, Object> first = map(post("/responsibility/plans", body, headOf()));
        Map<String, Object> second = map(post("/responsibility/plans", body, headOf()));   // 不同的幂等键：模拟响应丢失后的重试
        assertThat(second.get("id")).isEqualTo(first.get("id"));
        assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM responsibility_plan WHERE team_id = ?", Integer.class, team)).isEqualTo(1);
        assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM responsibility_task WHERE team_id = ?", Integer.class, team)).isEqualTo(1);
        // 别人拿同一个整理编号不能再生成
        assertThat(post("/responsibility/plans", body, member(alice)).getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
    }

    @Test
    void ineligiblePeopleAndSelfReviewAreRejected() {
        // dave 不在可指派名单里（离职/停用/别的企业）
        Map<String, Object> bad = taskBody("联调首页", dave, carol, due(7), "构建包", "回归通过");
        ResponseEntity<String> r = post("/responsibility/plans", planBody(List.of(bad), null), headOf());
        assertThat(r.getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
        assertThat(r.getBody()).contains("不是本部门的有效成员");

        Map<String, Object> badReviewer = taskBody("联调首页", alice, dave, due(7), "构建包", "回归通过");
        assertThat(post("/responsibility/plans", planBody(List.of(badReviewer), null), headOf()).getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);

        // 主责人不能验收自己
        Map<String, Object> self = taskBody("联调首页", alice, alice, due(7), "构建包", "回归通过");
        Map<String, Object> plan = map(post("/responsibility/plans", planBody(List.of(self), null), headOf()));
        assertThat(plan.get("canPublish")).isEqualTo(false);
        ResponseEntity<String> publish = post("/responsibility/plans/" + plan.get("id") + "/publish", Map.of("eligible", eligible()), headOf());
        assertThat(publish.getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
        assertThat(publish.getBody()).contains("不能验收自己的成果");

        // 协办人不能就是主责人
        Map<String, Object> dup = fullTask("联调首页");
        dup.put("collaboratorUserIds", List.of(alice));
        assertThat(post("/responsibility/plans", planBody(List.of(dup), null), headOf()).getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
    }

    @Test
    void publishRechecksThatPeopleAreStillValid() {
        Map<String, Object> plan = createPlan(List.of(fullTask("联调首页")));
        // 发布前 alice 已离开部门：Python 算出的名单里没有她
        Map<String, Object> shrunk = Map.of("memberIds", List.of(head, bob), "reviewerIds", List.of(head, bob, carol));
        ResponseEntity<String> r = post("/responsibility/plans/" + plan.get("id") + "/publish", Map.of("eligible", shrunk), headOf());
        assertThat(r.getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
        assertThat(r.getBody()).contains("已不是本部门的有效成员");
    }

    @Test
    void onlyHeadPublishesAndOnlyOnce() {
        Map<String, Object> plan = createPlan(List.of(fullTask("联调首页")));
        ResponseEntity<String> byMember = post("/responsibility/plans/" + plan.get("id") + "/publish", Map.of("eligible", eligible()), member(alice));
        assertThat(byMember.getStatusCode()).isIn(HttpStatus.FORBIDDEN, HttpStatus.NOT_FOUND);
        assertThat(map(get("/responsibility/plans/" + plan.get("id"), headOf())).get("status")).isEqualTo("DRAFT");
        assertThat(post("/responsibility/plans/" + plan.get("id") + "/publish", Map.of("eligible", eligible()), headOf()).getStatusCode()).isEqualTo(HttpStatus.OK);
        assertThat(post("/responsibility/plans/" + plan.get("id") + "/publish", Map.of("eligible", eligible()), headOf()).getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
        assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM responsibility_event WHERE plan_id = ? AND event_type = 'PUBLISHED'", Integer.class, plan.get("id"))).isEqualTo(1);
    }

    // ------------------------------------------------------------------ 状态机与权限

    @Test
    @SuppressWarnings("unchecked")
    void fullLifecycleWithReworkAndVerification() {
        long task = taskId(publishedPlan("联调首页"), 0);

        // 指派人不能替员工接受；其他员工也不行
        assertThat(act(task, "accept", null, headOf()).getStatusCode()).isEqualTo(HttpStatus.FORBIDDEN);
        assertThat(act(task, "accept", null, member(bob)).getStatusCode()).isIn(HttpStatus.FORBIDDEN, HttpStatus.NOT_FOUND);
        assertThat(statusOf(task, member(alice))).isEqualTo("PENDING_ACCEPT");
        // 接受前不能提交
        assertThat(act(task, "submit", Map.of("summary", "已做完"), member(alice)).getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);

        assertThat(act(task, "accept", null, member(alice)).getStatusCode()).isEqualTo(HttpStatus.OK);
        assertThat(act(task, "accept", null, member(alice)).getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);   // 重复点击不会重复生效
        assertThat(act(task, "progress", Map.of("note", "接口联调完成一半", "percent", 50), member(alice)).getStatusCode()).isEqualTo(HttpStatus.OK);
        assertThat(act(task, "progress", Map.of("note", "x", "percent", 150), member(alice)).getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);

        // 受阻必须写原因；负责人也能解除
        assertThat(act(task, "block", Map.of(), member(alice)).getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
        assertThat(act(task, "block", Map.of("reason", "缺少测试环境账号", "waitingOnUserId", bob, "eligible", eligible()), member(alice)).getStatusCode()).isEqualTo(HttpStatus.OK);
        assertThat(statusOf(task, member(alice))).isEqualTo("BLOCKED");
        assertThat(act(task, "unblock", Map.of("note", "已开通账号"), headOf()).getStatusCode()).isEqualTo(HttpStatus.OK);

        // 提交成果：必须有说明；链接只允许 http(s)
        assertThat(act(task, "submit", Map.of(), member(alice)).getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
        assertThat(act(task, "submit", Map.of("summary", "构建包已上传", "link", "javascript:alert(1)"), member(alice)).getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
        assertThat(act(task, "submit", Map.of("summary", "构建包已上传", "link", "https://example.com/build/1"), member(alice)).getStatusCode()).isEqualTo(HttpStatus.OK);
        assertThat(statusOf(task, member(alice))).isEqualTo("PENDING_REVIEW");

        // 主责人和指派人都不能验收；只有验收人
        assertThat(act(task, "verify", null, member(alice)).getStatusCode()).isEqualTo(HttpStatus.FORBIDDEN);
        assertThat(act(task, "verify", null, headOf()).getStatusCode()).isEqualTo(HttpStatus.FORBIDDEN);
        // 验收人退回必须写原因
        assertThat(act(task, "rework", Map.of(), member(carol)).getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
        assertThat(act(task, "rework", Map.of("reason", "回归用例没有覆盖登录页"), member(carol)).getStatusCode()).isEqualTo(HttpStatus.OK);
        assertThat(statusOf(task, member(alice))).isEqualTo("IN_PROGRESS");
        assertThat(act(task, "submit", Map.of("summary", "补充了登录页回归"), member(alice)).getStatusCode()).isEqualTo(HttpStatus.OK);
        Map<String, Object> done = map(act(task, "verify", Map.of("note", "符合验收标准"), member(carol)));
        Map<String, Object> dto = (Map<String, Object>) done.get("task");
        assertThat(dto.get("status")).isEqualTo("DONE");
        assertThat(dto.get("reworkCount")).isEqualTo(1);
        assertThat(done.get("planStatus")).isEqualTo("COMPLETED");
        assertThat(act(task, "verify", null, member(carol)).getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);   // 已完成不能重复验收

        // 完整的履责事件（谁、何时、为何），两次提交各留一份交付物
        List<Map<String, Object>> events = (List<Map<String, Object>>) done.get("events");
        assertThat(events.stream().map(e -> (String) e.get("type")).toList())
                .containsExactly("PUBLISHED", "ACCEPTED", "PROGRESS", "BLOCKED", "UNBLOCKED", "SUBMITTED", "REWORK", "SUBMITTED", "VERIFIED");
        assertThat(((List<?>) done.get("deliverables"))).hasSize(2);
        // 完成后不能再改
        assertThat(act(task, "revise", Map.of("reason", "改期", "dueDate", due(20), "eligible", eligible()), headOf()).getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
        assertThat(act(task, "cancel", Map.of("reason", "不要了"), headOf()).getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
    }

    @Test
    @SuppressWarnings("unchecked")
    void objectionGoesBackToNegotiationAndRevisionIsRecorded() {
        long task = taskId(publishedPlan("联调首页"), 0);
        assertThat(act(task, "object", Map.of(), member(alice)).getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
        assertThat(act(task, "object", Map.of("reason", "这个期限和我手上的发版冲突，建议延后三天"), member(alice)).getStatusCode()).isEqualTo(HttpStatus.OK);
        assertThat(statusOf(task, member(alice))).isEqualTo("NEGOTIATING");
        assertThat(act(task, "accept", null, member(alice)).getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);   // 异议未处理前不能直接接受

        // 员工不能自己改责任；负责人改必须写原因并留下前后记录
        assertThat(act(task, "revise", Map.of("reason", "自己改", "dueDate", due(10), "eligible", eligible()), member(alice)).getStatusCode()).isEqualTo(HttpStatus.FORBIDDEN);
        assertThat(act(task, "revise", Map.of("dueDate", due(10), "eligible", eligible()), headOf()).getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
        assertThat(act(task, "revise", Map.of("reason", "没有变化", "eligible", eligible()), headOf()).getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
        assertThat(act(task, "revise", Map.of("reason", "晚于今天", "dueDate", due(-1), "eligible", eligible()), headOf()).getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
        Map<String, Object> revised = map(act(task, "revise", Map.of("reason", "同意延后三天", "dueDate", due(10), "eligible", eligible()), headOf()));
        assertThat(((Map<String, Object>) revised.get("task")).get("status")).isEqualTo("PENDING_ACCEPT");
        assertThat(((Map<String, Object>) revised.get("task")).get("dueDate")).isEqualTo(due(10));
        List<Map<String, Object>> events = (List<Map<String, Object>>) revised.get("events");
        Map<String, Object> change = events.get(events.size() - 1);
        assertThat(change.get("type")).isEqualTo("REVISED");
        assertThat(change.get("note")).isEqualTo("同意延后三天");
        assertThat((String) change.get("detail")).contains("dueDate").contains(due(7)).contains(due(10));
        assertThat(act(task, "accept", null, member(alice)).getStatusCode()).isEqualTo(HttpStatus.OK);
    }

    @Test
    @SuppressWarnings("unchecked")
    void changingResponsibleRequiresNewAcceptanceAndOldOwnerLosesAccess() {
        long task = taskId(publishedPlan("联调首页"), 0);
        assertThat(act(task, "accept", null, member(alice)).getStatusCode()).isEqualTo(HttpStatus.OK);
        // 新主责人必须是有效成员；验收人不能被换成与新主责人相同
        assertThat(act(task, "revise", Map.of("reason", "换人", "responsibleUserId", dave, "eligible", eligible()), headOf()).getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
        assertThat(act(task, "revise", Map.of("reason", "换人", "responsibleUserId", carol, "eligible", eligible()), headOf()).getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
        Map<String, Object> revised = map(act(task, "revise", Map.of("reason", "alice 要休假，交给 bob", "responsibleUserId", bob, "eligible", eligible()), headOf()));
        assertThat(((Map<String, Object>) revised.get("task")).get("status")).isEqualTo("PENDING_ACCEPT");
        // 新主责人要重新接受；原主责人不再能操作也看不到
        assertThat(act(task, "accept", null, member(alice)).getStatusCode()).isIn(HttpStatus.FORBIDDEN, HttpStatus.NOT_FOUND);
        assertThat(get("/responsibility/tasks/" + task, member(alice)).getStatusCode()).isEqualTo(HttpStatus.NOT_FOUND);
        assertThat(act(task, "accept", null, member(bob)).getStatusCode()).isEqualTo(HttpStatus.OK);
    }

    @Test
    void leavingTheDepartmentRemovesAllAccess() {
        long task = taskId(publishedPlan("联调首页"), 0);
        assertThat(act(task, "accept", null, member(alice)).getStatusCode()).isEqualTo(HttpStatus.OK);
        // alice 离开 team（只剩 otherTeam 的成员身份）：看不到、也不能再提交
        assertThat(get("/responsibility/tasks/" + task, left(alice)).getStatusCode()).isEqualTo(HttpStatus.NOT_FOUND);
        assertThat(act(task, "submit", Map.of("summary", "做完了"), left(alice)).getStatusCode()).isEqualTo(HttpStatus.NOT_FOUND);
        assertThat(list(get("/responsibility/tasks?view=mine", left(alice)))).isEmpty();
        // 验收人同理
        assertThat(act(task, "verify", null, left(carol)).getStatusCode()).isEqualTo(HttpStatus.NOT_FOUND);
    }

    @Test
    void outsidersCannotSeeOrActAcrossCompanies() {
        long task = taskId(publishedPlan("联调首页"), 0);
        Who foreign = new Who(stranger, List.of(foreignTeam), List.of(foreignTeam));
        // 外企业的人：团队不在其可访问范围内（路径里 scope 不含它）→ 400/404
        String foreignQuery = "scopeTeamIds=" + foreignTeam + "&memberTeamIds=" + foreignTeam + "&headTeamIds=" + foreignTeam;
        ResponseEntity<String> r = callWithQuery(HttpMethod.GET, "/responsibility/tasks/" + task, foreignQuery, stranger);
        assertThat(r.getStatusCode()).isEqualTo(HttpStatus.NOT_FOUND);
        ResponseEntity<String> w = callWithQuery(HttpMethod.POST, "/responsibility/tasks/" + task + "/cancel", foreignQuery, stranger);
        assertThat(w.getStatusCode()).isEqualTo(HttpStatus.NOT_FOUND);
        // 成员/负责人部门不在企业范围内的伪造请求直接拒绝
        ResponseEntity<String> forged = callWithQuery(HttpMethod.GET, "/responsibility/tasks", "scopeTeamIds=" + team + "&headTeamIds=" + foreignTeam, stranger);
        assertThat(forged.getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
        assertThat(foreign.uid()).isEqualTo(stranger);
    }

    private ResponseEntity<String> callWithQuery(HttpMethod method, String path, String query, long uid) {
        try {
            String full = path + "?" + query;
            String json = method == HttpMethod.POST ? "{}" : null;
            byte[] bytes = json == null ? new byte[0] : json.getBytes(StandardCharsets.UTF_8);
            Map<String, Object> ctx = new HashMap<>();
            ctx.put("user_id", uid);
            ctx.put("team_id", null);
            ctx.put("scopes", SCOPES);
            ctx.put("operation", "resp_test");
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

    // ------------------------------------------------------------------ 延期、转交、取消

    @Test
    @SuppressWarnings("unchecked")
    void extensionRequestNeedsHeadDecision() {
        long task = taskId(publishedPlan("联调首页"), 0);
        act(task, "accept", null, member(alice));
        assertThat(act(task, "request-extension", Map.of("proposedDate", due(5), "reason", "依赖方延迟"), member(alice)).getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);   // 不晚于当前截止日
        assertThat(act(task, "request-extension", Map.of("proposedDate", due(12), "reason", "依赖方接口延迟交付"), member(alice)).getStatusCode()).isEqualTo(HttpStatus.OK);
        assertThat(act(task, "decide-extension", Map.of("approve", true), member(alice)).getStatusCode()).isEqualTo(HttpStatus.FORBIDDEN);
        assertThat(act(task, "decide-extension", Map.of("approve", false), headOf()).getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);   // 不同意要写原因
        Map<String, Object> approved = map(act(task, "decide-extension", Map.of("approve", true, "note", "同意"), headOf()));
        assertThat(((Map<String, Object>) approved.get("task")).get("dueDate")).isEqualTo(due(12));
        assertThat(((Map<String, Object>) approved.get("task")).get("pendingDueDate")).isNull();
        // 再申请并被拒绝：截止日不变
        act(task, "request-extension", Map.of("proposedDate", due(20), "reason", "还需要时间"), member(alice));
        Map<String, Object> rejected = map(act(task, "decide-extension", Map.of("approve", false, "note", "不能再拖了"), headOf()));
        assertThat(((Map<String, Object>) rejected.get("task")).get("dueDate")).isEqualTo(due(12));
        List<Map<String, Object>> events = (List<Map<String, Object>>) rejected.get("events");
        assertThat(events.stream().map(e -> e.get("type")).toList()).contains("EXTENSION_REQUESTED", "EXTENSION_APPROVED", "EXTENSION_REJECTED");
    }

    @Test
    @SuppressWarnings("unchecked")
    void transferRequestHandledByHead() {
        long task = taskId(publishedPlan("联调首页"), 0);
        act(task, "accept", null, member(alice));
        assertThat(act(task, "request-transfer", Map.of("reason", "我下周休假，建议交给 bob"), member(alice)).getStatusCode()).isEqualTo(HttpStatus.OK);
        Map<String, Object> done = map(act(task, "decide-transfer", Map.of("approve", true, "responsibleUserId", bob, "reason", "同意转交给 bob", "eligible", eligible()), headOf()));
        Map<String, Object> dto = (Map<String, Object>) done.get("task");
        assertThat(((Number) dto.get("responsibleUserId")).longValue()).isEqualTo(bob);
        assertThat(dto.get("status")).isEqualTo("PENDING_ACCEPT");
        assertThat(dto.get("transferNote")).isNull();
    }

    @Test
    @SuppressWarnings("unchecked")
    void cancelTaskAndPlanNeedReasonAndHead() {
        Map<String, Object> plan = publishedPlan("联调首页", "编写发布说明");
        long first = taskId(plan, 0), second = taskId(plan, 1);
        assertThat(act(first, "cancel", Map.of("reason", "需求取消"), member(alice)).getStatusCode()).isEqualTo(HttpStatus.FORBIDDEN);
        assertThat(act(first, "cancel", Map.of(), headOf()).getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
        assertThat(act(first, "cancel", Map.of("reason", "需求取消"), headOf()).getStatusCode()).isEqualTo(HttpStatus.OK);
        assertThat(map(get("/responsibility/plans/" + plan.get("id"), headOf())).get("status")).isEqualTo("PUBLISHED");
        // 最后一项也取消 → 计划随之结束
        assertThat(act(second, "cancel", Map.of("reason", "整体取消"), headOf()).getStatusCode()).isEqualTo(HttpStatus.OK);
        assertThat(map(get("/responsibility/plans/" + plan.get("id"), headOf())).get("status")).isEqualTo("CANCELLED");

        Map<String, Object> other = publishedPlan("另一件事");
        assertThat(post("/responsibility/plans/" + other.get("id") + "/cancel", Map.of(), headOf()).getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
        assertThat(post("/responsibility/plans/" + other.get("id") + "/cancel", Map.of("reason", "改由其他部门负责"), headOf()).getStatusCode()).isEqualTo(HttpStatus.OK);
        assertThat(statusOf(taskId(other, 0), headOf())).isEqualTo("CANCELLED");
    }

    // ------------------------------------------------------------------ 依赖

    @Test
    @SuppressWarnings("unchecked")
    void dependenciesBlockSubmissionAndRejectCyclesAndEarlyDeadlines() {
        Map<String, Object> first = fullTask("先做接口");
        Map<String, Object> second = taskBody("再做页面", bob, carol, due(9), "页面", "页面验收通过");
        second.put("dependsOnSeq", List.of(1));
        Map<String, Object> plan = map(post("/responsibility/plans", planBody(List.of(first, second), null), headOf()));
        assertThat(((List<Map<String, Object>>) plan.get("tasks")).get(1).get("dependsOnSeq")).isEqualTo(List.of(1));

        // 循环依赖被拒绝
        Map<String, Object> cyc = fullTask("先做接口");
        cyc.put("dependsOnSeq", List.of(2));
        ResponseEntity<String> r = post("/responsibility/tasks/" + taskId(plan, 0) + "/edit", Map.of("task", cyc, "eligible", eligible()), headOf());
        assertThat(r.getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
        assertThat(r.getBody()).contains("循环");

        // 截止日早于前置事项：发布被拦
        Map<String, Object> early = taskBody("再做页面", bob, carol, due(3), "页面", "页面验收通过");
        early.put("dependsOnSeq", List.of(1));
        assertThat(post("/responsibility/tasks/" + taskId(plan, 1) + "/edit", Map.of("task", early, "eligible", eligible()), headOf()).getStatusCode()).isEqualTo(HttpStatus.OK);
        ResponseEntity<String> blocked = post("/responsibility/plans/" + plan.get("id") + "/publish", Map.of("eligible", eligible()), headOf());
        assertThat(blocked.getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
        assertThat(blocked.getBody()).contains("早于它依赖的前置事项");

        Map<String, Object> fixed = taskBody("再做页面", bob, carol, due(9), "页面", "页面验收通过");
        fixed.put("dependsOnSeq", List.of(1));
        post("/responsibility/tasks/" + taskId(plan, 1) + "/edit", Map.of("task", fixed, "eligible", eligible()), headOf());
        assertThat(post("/responsibility/plans/" + plan.get("id") + "/publish", Map.of("eligible", eligible()), headOf()).getStatusCode()).isEqualTo(HttpStatus.OK);

        // 前置事项没完成，后一项不能提交
        long t1 = taskId(plan, 0), t2 = taskId(plan, 1);
        act(t2, "accept", null, member(bob));
        ResponseEntity<String> early2 = act(t2, "submit", Map.of("summary", "页面做好了"), member(bob));
        assertThat(early2.getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
        assertThat(early2.getBody()).contains("前置事项还没有完成");
        act(t1, "accept", null, member(alice));
        act(t1, "submit", Map.of("summary", "接口做好了"), member(alice));
        act(t1, "verify", null, member(carol));
        assertThat(act(t2, "submit", Map.of("summary", "页面做好了"), member(bob)).getStatusCode()).isEqualTo(HttpStatus.OK);
    }

    // ------------------------------------------------------------------ 查询与统计

    @Test
    @SuppressWarnings("unchecked")
    void viewsAndHomeCounts() {
        Map<String, Object> plan = publishedPlan("联调首页", "编写发布说明");
        long first = taskId(plan, 0);
        act(first, "accept", null, member(alice));

        assertThat(list(get("/responsibility/tasks?view=mine", member(alice)))).hasSize(2);
        assertThat(list(get("/responsibility/tasks?view=mine&status=IN_PROGRESS", member(alice)))).hasSize(1);
        assertThat(list(get("/responsibility/tasks?view=review", member(carol)))).hasSize(2);
        assertThat(list(get("/responsibility/tasks?view=assigned", headOf()))).hasSize(2);
        assertThat(list(get("/responsibility/tasks?view=team", headOf()))).hasSize(2);
        assertThat(get("/responsibility/tasks?view=team", member(alice)).getStatusCode()).isEqualTo(HttpStatus.FORBIDDEN);
        // 没有关系的同部门成员看不到计划
        assertThat(list(get("/responsibility/plans", member(dave)))).isEmpty();
        assertThat(get("/responsibility/plans/" + plan.get("id"), member(dave)).getStatusCode()).isEqualTo(HttpStatus.NOT_FOUND);
        assertThat(list(get("/responsibility/plans", member(alice)))).hasSize(1);

        Map<String, Object> mine = map(get("/responsibility/mine", member(alice)));
        assertThat(mine.get("pendingAccept")).isEqualTo(1);
        assertThat(mine.get("inProgress")).isEqualTo(1);
        Map<String, Object> reviewer = map(get("/responsibility/mine", member(carol)));
        assertThat(reviewer.get("pendingReview")).isEqualTo(0);
        act(first, "submit", Map.of("summary", "已做好"), member(alice));
        assertThat(map(get("/responsibility/mine", member(carol))).get("pendingReview")).isEqualTo(1);
        Map<String, Object> headMine = map(get("/responsibility/mine", headOf()));
        assertThat(headMine.get("isHead")).isEqualTo(true);
        assertThat(headMine.get("assignedNotAccepted")).isEqualTo(1);
    }

    @Test
    @SuppressWarnings("unchecked")
    void summaryIsForHeadsAndCarriesQualityMetricsNotCounts() {
        Map<String, Object> plan = publishedPlan("联调首页", "编写发布说明");
        long first = taskId(plan, 0);
        act(first, "accept", null, member(alice));
        act(first, "block", Map.of("reason", "缺少账号"), member(alice));
        act(first, "unblock", Map.of(), member(alice));
        act(first, "submit", Map.of("summary", "做好了"), member(alice));
        act(first, "verify", null, member(carol));

        assertThat(get("/responsibility/summary?teamId=" + team, member(alice)).getStatusCode()).isEqualTo(HttpStatus.FORBIDDEN);
        Map<String, Object> summary = map(get("/responsibility/summary?teamId=" + team, headOf()));
        Map<String, Object> byStatus = (Map<String, Object>) summary.get("byStatus");
        assertThat(byStatus.get("DONE")).isEqualTo(1);
        assertThat(byStatus.get("PENDING_ACCEPT")).isEqualTo(1);
        Map<String, Object> metrics = (Map<String, Object>) summary.get("metrics");
        assertThat(metrics.get("firstPassRate")).isEqualTo(100.0);
        assertThat(metrics.get("onTimeSubmitRate")).isEqualTo(100.0);
        assertThat(metrics.get("acceptMedianHours")).isNotNull();
        assertThat(metrics.get("blockedResolveAvgHours")).isNotNull();
        assertThat(metrics).doesNotContainKey("completedTaskCount");
        Map<String, Object> week = (Map<String, Object>) summary.get("week");
        assertThat(week).containsKeys("due", "done", "rate");
        assertThat(((List<?>) summary.get("waitingAccept"))).hasSize(1);
    }

    @Test
    @SuppressWarnings("unchecked")
    void aiMatchRateReflectsWhetherTheSuggestedOwnerWasKept() {
        Map<String, Object> kept = fullTask("联调首页");
        kept.put("aiResponsibleUserId", alice);
        Map<String, Object> changed = fullTask("编写发布说明");
        changed.put("aiResponsibleUserId", bob);   // AI 建议 bob，负责人改成了 alice
        Map<String, Object> plan = createPlan(List.of(kept, changed));
        assertThat(post("/responsibility/plans/" + plan.get("id") + "/publish", Map.of("eligible", eligible()), headOf()).getStatusCode()).isEqualTo(HttpStatus.OK);
        Map<String, Object> metrics = (Map<String, Object>) map(get("/responsibility/summary?teamId=" + team, headOf())).get("metrics");
        assertThat(metrics.get("aiMatchRate")).isEqualTo(50.0);
        assertThat(metrics.get("aiMatchSamples")).isEqualTo(2);
    }

    @Test
    void draftIsInvisibleToAssigneesUntilPublished() {
        Map<String, Object> plan = createPlan(List.of(fullTask("联调首页")));   // 负责人起草，alice 被列为主责
        long task = taskId(plan, 0);
        assertThat(get("/responsibility/plans/" + plan.get("id"), member(alice)).getStatusCode()).isEqualTo(HttpStatus.NOT_FOUND);
        assertThat(get("/responsibility/tasks/" + task, member(alice)).getStatusCode()).isEqualTo(HttpStatus.NOT_FOUND);
        assertThat(list(get("/responsibility/plans", member(alice)))).isEmpty();
        assertThat(list(get("/responsibility/tasks?view=mine", member(alice)))).isEmpty();
        assertThat(act(task, "accept", null, member(alice)).getStatusCode()).isIn(HttpStatus.FORBIDDEN, HttpStatus.BAD_REQUEST, HttpStatus.NOT_FOUND);
        post("/responsibility/plans/" + plan.get("id") + "/publish", Map.of("eligible", eligible()), headOf());
        assertThat(get("/responsibility/plans/" + plan.get("id"), member(alice)).getStatusCode()).isEqualTo(HttpStatus.OK);
        assertThat(list(get("/responsibility/plans", member(alice)))).hasSize(1);
    }

    @Test
    void pendingReviewCountIgnoresTheCurrentDepartmentFilter() {
        long task = taskId(publishedPlan("联调首页"), 0);
        act(task, "accept", null, member(alice));
        act(task, "submit", Map.of("summary", "已做好"), member(alice));
        // carol 作为验收人，当前所在部门是 otherTeam，但她能看到 team 里等她验收的成果
        Who carolElsewhere = new Who(carol, List.of(otherTeam), List.of(team));
        assertThat(map(get("/responsibility/mine?teamId=" + otherTeam, carolElsewhere)).get("pendingReview")).isEqualTo(1);
        assertThat(list(get("/responsibility/tasks?view=review", carolElsewhere))).hasSize(1);
    }

    @Test
    void eventsAreAppendOnly() {
        for (Method method : RespEventRepository.class.getMethods()) {
            String name = method.getName().toLowerCase();
            assertThat(name).as("事件仓库不应有修改入口: " + method).doesNotContain("delete").doesNotContain("update").doesNotContain("remove");
        }
        assertThat(Arrays.stream(RespEvent.class.getMethods()).map(Method::getName).filter(n -> n.startsWith("set")).toList()).isEmpty();
    }

    @Test
    void draftEditingIsLimitedToCreatorAndHead() {
        Map<String, Object> body = planBody(List.of(fullTask("联调首页")), null);
        Map<String, Object> plan = map(post("/responsibility/plans", body, member(alice)));   // 普通成员也能整理出草稿
        long task = taskId(plan, 0);
        Map<String, Object> edit = Map.of("task", fullTask("联调首页（改）"), "eligible", eligible());
        assertThat(post("/responsibility/tasks/" + task + "/edit", edit, member(bob)).getStatusCode()).isEqualTo(HttpStatus.FORBIDDEN);
        assertThat(post("/responsibility/tasks/" + task + "/edit", edit, member(alice)).getStatusCode()).isEqualTo(HttpStatus.OK);
        // 创建人不能自己发布
        assertThat(post("/responsibility/plans/" + plan.get("id") + "/publish", Map.of("eligible", eligible()), member(alice)).getStatusCode()).isEqualTo(HttpStatus.FORBIDDEN);
        // 增删草稿任务
        Map<String, Object> added = map(post("/responsibility/plans/" + plan.get("id") + "/tasks", Map.of("task", fullTask("编写发布说明"), "eligible", eligible()), member(alice)));
        assertThat((List<?>) added.get("tasks")).hasSize(2);
        Map<String, Object> removed = map(post("/responsibility/tasks/" + taskId(added, 1) + "/remove", Map.of(), member(alice)));
        assertThat((List<?>) removed.get("tasks")).hasSize(1);
        // 发布后不能再按草稿改
        post("/responsibility/plans/" + plan.get("id") + "/publish", Map.of("eligible", eligible()), headOf());
        assertThat(post("/responsibility/tasks/" + task + "/edit", edit, member(alice)).getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
    }
}
