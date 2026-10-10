package com.enterprisehub.it;

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
import java.util.HashMap;
import java.util.HexFormat;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.Callable;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.ThreadLocalRandom;

import static org.assertj.core.api.Assertions.assertThat;

/** IT 服务台的真实 HTTP 集成测试（真实 MySQL + 签名上下文）：工单全流程、审批、SLA、范围隔离、设备生命周期、并发。
 * ID 段 3_000_000–3_990_000。 */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
@ActiveProfiles("test")
class ItServiceDeskIntegrationTest {
    private static final String HMAC_SECRET = "test-secret-not-for-production";
    private static final ObjectMapper MAPPER = new ObjectMapper();
    private static final List<String> EMPLOYEE = List.of("it.ticket.read", "it.ticket.write");
    private static final List<String> APPROVE = List.of("it.ticket.read", "it.ticket.approve");
    private static final List<String> DESK_READ = List.of("it.desk.read");
    private static final List<String> DESK = List.of("it.desk.read", "it.desk.write");

    @LocalServerPort
    private int port;
    @Autowired
    private TestRestTemplate rest;
    @Autowired
    private JdbcTemplate jdbc;

    private long requester;
    private long head;
    private long it1;
    private long it2;
    private long stranger;
    private long teamId;       // 申请人所在业务部门
    private long itTeamId;     // IT 部门
    private long otherTeamId;  // 另一家企业的部门

    @BeforeEach
    void setUp() {
        requester = ThreadLocalRandom.current().nextLong(3_000_000L, 3_980_000L);
        head = requester + 1;
        it1 = requester + 2;
        it2 = requester + 3;
        stranger = requester + 4;
        teamId = requester;
        itTeamId = requester + 10;
        otherTeamId = requester + 20;
    }

    @AfterEach
    void tearDown() {
        String teams = teamId + "," + itTeamId + "," + otherTeamId;
        jdbc.update("DELETE FROM it_ticket_comment WHERE ticket_id IN (SELECT id FROM it_ticket WHERE team_id IN (" + teams + "))");
        jdbc.update("DELETE FROM it_ticket WHERE team_id IN (" + teams + ")");
        jdbc.update("DELETE FROM it_device_event WHERE device_id IN (SELECT id FROM it_device WHERE managing_team_id IN (" + teams + "))");
        jdbc.update("DELETE FROM it_device WHERE managing_team_id IN (" + teams + ")");
        jdbc.update("DELETE FROM audit_event WHERE user_id IN (?, ?, ?, ?, ?)", requester, head, it1, it2, stranger);
    }

    // ------------------------------------------------------------------ 请求辅助

    private static String sha256Hex(byte[] bytes) {
        try {
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes));
        } catch (Exception e) {
            throw new RuntimeException(e);
        }
    }

    private HttpHeaders signed(HttpMethod method, String path, String body, long uid, Long ctxTeam, List<String> scopes,
                               boolean teamAdmin) {
        try {
            Map<String, Object> ctx = new HashMap<>();
            ctx.put("user_id", uid);
            ctx.put("team_id", ctxTeam);
            ctx.put("scopes", scopes);
            ctx.put("operation", "it_test");
            ctx.put("is_org_admin", false);
            ctx.put("is_team_admin", teamAdmin);
            ctx.put("trace_id", UUID.randomUUID().toString());
            ctx.put("timestamp", Instant.now().getEpochSecond());
            ctx.put("nonce", UUID.randomUUID().toString().replace("-", ""));
            ctx.put("method", method.name());
            ctx.put("path", path);
            ctx.put("body_sha256", sha256Hex(body == null ? new byte[0] : body.getBytes(StandardCharsets.UTF_8)));
            String b64 = Base64.getEncoder().encodeToString(MAPPER.writeValueAsString(ctx).getBytes(StandardCharsets.UTF_8));
            javax.crypto.Mac mac = javax.crypto.Mac.getInstance("HmacSHA256");
            mac.init(new javax.crypto.spec.SecretKeySpec(HMAC_SECRET.getBytes(StandardCharsets.UTF_8), "HmacSHA256"));
            HttpHeaders headers = new HttpHeaders();
            headers.set("X-Context", b64);
            headers.set("X-Signature", HexFormat.of().formatHex(mac.doFinal(b64.getBytes(StandardCharsets.UTF_8))));
            headers.setContentType(org.springframework.http.MediaType.APPLICATION_JSON);
            headers.set("Idempotency-Key", UUID.randomUUID().toString());
            return headers;
        } catch (Exception e) {
            throw new RuntimeException(e);
        }
    }

    private ResponseEntity<String> call(HttpMethod method, String path, Object body, long uid, Long ctxTeam,
                                        List<String> scopes, boolean teamAdmin) {
        try {
            String json = body == null ? null : MAPPER.writeValueAsString(body);
            return rest.exchange(java.net.URI.create("http://127.0.0.1:" + port + path), method,
                    new HttpEntity<>(json, signed(method, path, json, uid, ctxTeam, scopes, teamAdmin)), String.class);
        } catch (Exception e) {
            throw new RuntimeException(e);
        }
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> map(ResponseEntity<String> response) {
        try {
            return MAPPER.readValue(response.getBody(), Map.class);
        } catch (Exception e) {
            throw new RuntimeException(response.getStatusCode() + " " + response.getBody(), e);
        }
    }

    @SuppressWarnings("unchecked")
    private List<Map<String, Object>> list(ResponseEntity<String> response) {
        try {
            return MAPPER.readValue(response.getBody(), List.class);
        } catch (Exception e) {
            throw new RuntimeException(response.getStatusCode() + " " + response.getBody(), e);
        }
    }

    private String scope() {
        return "scopeTeamIds=" + teamId + "," + itTeamId;
    }

    private ResponseEntity<String> employee(HttpMethod method, String path, Object body) {
        return call(method, path, body, requester, teamId, EMPLOYEE, false);
    }

    private ResponseEntity<String> desk(long uid, HttpMethod method, String path, Object body) {
        String sep = path.contains("?") ? "&" : "?";
        return call(method, path + sep + scope(), body, uid, itTeamId, DESK, false);
    }

    private ResponseEntity<String> deskRead(HttpMethod method, String path) {
        String sep = path.contains("?") ? "&" : "?";
        return call(method, path + sep + scope(), null, it1, itTeamId, DESK_READ, false);
    }

    private static Map<String, Object> ticketBody(String category, String title, String description) {
        Map<String, Object> body = new HashMap<>();
        body.put("category", category);
        body.put("title", title);
        body.put("description", description);
        return body;
    }

    private long createTicket(String category, String title, String description) {
        ResponseEntity<String> response = employee(HttpMethod.POST, "/it/tickets", ticketBody(category, title, description));
        assertThat(response.getStatusCode()).as(response.getBody()).isEqualTo(HttpStatus.OK);
        return ((Number) map(response).get("id")).longValue();
    }

    private static String title() {
        return "测试工单-" + UUID.randomUUID().toString().substring(0, 8);
    }

    private Map<String, Object> deskTicket(long id) {
        return map(deskRead(HttpMethod.GET, "/it/desk/tickets/" + id));
    }

    // ------------------------------------------------------------------ 工单全流程

    @Test
    void incidentFlow_take_waitForUser_resolve_confirm() {
        long id = createTicket("INCIDENT", title(), "打印机卡纸后一直脱机，无法打印");
        Map<String, Object> created = deskTicket(id);
        assertThat(created).containsEntry("status", "OPEN").containsEntry("slaStatus", "OK");
        assertThat(created.get("firstResponseAt")).isNull();

        assertThat(list(deskRead(HttpMethod.GET, "/it/desk/tickets?assignee=unassigned"))).anyMatch(t -> ((Number) t.get("id")).longValue() == id);

        Map<String, Object> taken = map(desk(it1, HttpMethod.POST, "/it/desk/tickets/" + id + "/assign", Map.of("assigneeUserId", it1)));
        assertThat(taken).containsEntry("status", "IN_PROGRESS");
        assertThat(((Number) taken.get("assigneeUserId")).longValue()).isEqualTo(it1);
        assertThat(taken.get("firstResponseAt")).isNotNull();

        ResponseEntity<String> noQuestion = desk(it1, HttpMethod.POST, "/it/desk/tickets/" + id + "/status", Map.of("status", "WAITING_USER"));
        assertThat(noQuestion.getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
        Map<String, Object> waiting = map(desk(it1, HttpMethod.POST, "/it/desk/tickets/" + id + "/status",
                Map.of("status", "WAITING_USER", "note", "请告诉我打印机的位置和型号")));
        assertThat(waiting).containsEntry("status", "WAITING_USER").containsEntry("slaStatus", "PAUSED");

        // 等待用户期间不计入处理时限：把进入等待的时间往前挪 2 小时，用户回复后截止时间应顺延约 2 小时
        Instant dueBefore = jdbc.queryForObject("SELECT sla_due_at FROM it_ticket WHERE id = ?", java.sql.Timestamp.class, id).toInstant();
        jdbc.update("UPDATE it_ticket SET waiting_since = DATE_SUB(waiting_since, INTERVAL 2 HOUR) WHERE id = ?", id);
        Map<String, Object> replied = map(employee(HttpMethod.POST, "/it/tickets/" + id + "/comments", Map.of("body", "三楼东侧，惠普 M227")));
        assertThat(replied).containsEntry("status", "IN_PROGRESS");
        Instant dueAfter = jdbc.queryForObject("SELECT sla_due_at FROM it_ticket WHERE id = ?", java.sql.Timestamp.class, id).toInstant();
        assertThat(java.time.Duration.between(dueBefore, dueAfter).toMinutes()).isBetween(118L, 125L);

        ResponseEntity<String> shortResolution = desk(it1, HttpMethod.POST, "/it/desk/tickets/" + id + "/resolve", Map.of("resolution", "好"));
        assertThat(shortResolution.getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
        Map<String, Object> resolved = map(desk(it1, HttpMethod.POST, "/it/desk/tickets/" + id + "/resolve",
                Map.of("resolution", "清除卡纸并重新安装驱动，已能正常打印")));
        assertThat(resolved).containsEntry("status", "RESOLVED").containsEntry("slaStatus", "MET");

        assertThat(map(employee(HttpMethod.POST, "/it/tickets/" + id + "/confirm", null))).containsEntry("status", "CLOSED");
        assertThat(list(deskRead(HttpMethod.GET, "/it/desk/tickets"))).as("已关闭的不在默认队列里")
                .noneMatch(t -> ((Number) t.get("id")).longValue() == id);
        assertThat(list(deskRead(HttpMethod.GET, "/it/desk/tickets?status=CLOSED"))).as("显式选状态能看到")
                .anyMatch(t -> ((Number) t.get("id")).longValue() == id);
        assertThat(employee(HttpMethod.POST, "/it/tickets/" + id + "/confirm", null).getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);

        @SuppressWarnings("unchecked")
        Map<String, Object> effect = (Map<String, Object>) map(deskRead(HttpMethod.GET, "/it/desk/summary")).get("effect");
        assertThat(((Number) effect.get("resolved")).intValue()).isEqualTo(1);
        assertThat(((Number) effect.get("slaMetRate")).doubleValue()).isEqualTo(100.0);
    }

    @Test
    void requestTicketsNeedDepartmentHeadApproval_beforeItSeesThem() {
        long id = createTicket("ACCOUNT", title(), "新员工入职，需要开通 OA 账号");
        assertThat(deskTicket(id)).containsEntry("status", "PENDING_APPROVAL");
        assertThat(list(deskRead(HttpMethod.GET, "/it/desk/tickets"))).noneMatch(t -> ((Number) t.get("id")).longValue() == id);
        assertThat(desk(it1, HttpMethod.POST, "/it/desk/tickets/" + id + "/assign", Map.of("assigneeUserId", it1)).getStatusCode())
                .isEqualTo(HttpStatus.BAD_REQUEST);

        // 待审批列表只有部门负责人能看，普通成员 404
        assertThat(call(HttpMethod.GET, "/it/tickets/team-pending?teamId=" + teamId, null, stranger, teamId, APPROVE, false)
                .getStatusCode()).isEqualTo(HttpStatus.NOT_FOUND);
        assertThat(list(call(HttpMethod.GET, "/it/tickets/team-pending?teamId=" + teamId, null, head, teamId, APPROVE, true)))
                .anyMatch(t -> ((Number) t.get("id")).longValue() == id);
        assertThat(call(HttpMethod.POST, "/it/tickets/" + id + "/approve", Map.of("note", "x"), stranger, teamId, APPROVE, false)
                .getStatusCode()).isEqualTo(HttpStatus.NOT_FOUND);
        assertThat(call(HttpMethod.POST, "/it/tickets/" + id + "/approve", Map.of(), requester, teamId, APPROVE, true)
                .getStatusCode()).as("不能批准自己提交的工单").isEqualTo(HttpStatus.BAD_REQUEST);

        Map<String, Object> approved = map(call(HttpMethod.POST, "/it/tickets/" + id + "/approve", Map.of("note", "同意开通"),
                head, teamId, APPROVE, true));
        assertThat(approved).containsEntry("status", "OPEN");
        assertThat(call(HttpMethod.POST, "/it/tickets/" + id + "/approve", Map.of(), head, teamId, APPROVE, true).getStatusCode())
                .isEqualTo(HttpStatus.BAD_REQUEST);
        assertThat(list(deskRead(HttpMethod.GET, "/it/desk/tickets"))).anyMatch(t -> ((Number) t.get("id")).longValue() == id);
    }

    @Test
    void rejectedRequestNeverReachesIt() {
        long id = createTicket("DEVICE", title(), "申请一台笔记本电脑用于外勤");
        Map<String, Object> rejected = map(call(HttpMethod.POST, "/it/tickets/" + id + "/reject", Map.of("note", "预算不足"),
                head, teamId, APPROVE, true));
        assertThat(rejected).containsEntry("status", "REJECTED").containsEntry("decisionNote", "预算不足");
        assertThat(employee(HttpMethod.POST, "/it/tickets/" + id + "/comments", Map.of("body", "再看看")).getStatusCode())
                .isEqualTo(HttpStatus.BAD_REQUEST);
    }

    @Test
    void itStaffCannotHandleTheirOwnTicket() {
        ResponseEntity<String> created = call(HttpMethod.POST, "/it/tickets", ticketBody("INCIDENT", title(), "我的电脑无法开机"),
                it1, itTeamId, EMPLOYEE, false);
        long id = ((Number) map(created).get("id")).longValue();
        for (String action : List.of("assign", "resolve")) {
            Object body = action.equals("assign") ? Map.of("assigneeUserId", it1) : Map.of("resolution", "自己解决了自己的");
            assertThat(desk(it1, HttpMethod.POST, "/it/desk/tickets/" + id + "/" + action, body).getStatusCode()).as(action)
                    .isEqualTo(HttpStatus.BAD_REQUEST);
        }
        assertThat(desk(it1, HttpMethod.POST, "/it/desk/tickets/" + id + "/comments", Map.of("body", "x", "internal", true))
                .getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
        assertThat(desk(it2, HttpMethod.POST, "/it/desk/tickets/" + id + "/assign", Map.of("assigneeUserId", it2)).getStatusCode())
                .isEqualTo(HttpStatus.OK);
        // 也不能把工单指派给申请人
        long other = createTicket("INCIDENT", title(), "网络无法连接");
        assertThat(desk(it1, HttpMethod.POST, "/it/desk/tickets/" + other + "/assign", Map.of("assigneeUserId", requester))
                .getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
    }

    @Test
    void reopenAfterResolve_andCancel() {
        long id = createTicket("INCIDENT", title(), "邮箱收不到邮件");
        desk(it1, HttpMethod.POST, "/it/desk/tickets/" + id + "/assign", Map.of("assigneeUserId", it1));
        desk(it1, HttpMethod.POST, "/it/desk/tickets/" + id + "/resolve", Map.of("resolution", "重新添加了邮箱账号"));
        assertThat(employee(HttpMethod.POST, "/it/tickets/" + id + "/reopen", Map.of("reason", "x")).getStatusCode())
                .as("原因太短").isEqualTo(HttpStatus.BAD_REQUEST);
        Map<String, Object> reopened = map(employee(HttpMethod.POST, "/it/tickets/" + id + "/reopen", Map.of("reason", "还是收不到")));
        assertThat(reopened).containsEntry("status", "IN_PROGRESS").containsEntry("reopenCount", 1);
        assertThat(employee(HttpMethod.POST, "/it/tickets/" + id + "/confirm", null).getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);

        long cancelled = createTicket("OTHER", title(), "咨询一下 VPN 怎么申请");
        assertThat(map(employee(HttpMethod.POST, "/it/tickets/" + cancelled + "/cancel", null))).containsEntry("status", "CANCELLED");
        assertThat(employee(HttpMethod.POST, "/it/tickets/" + cancelled + "/cancel", null).getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
    }

    @Test
    void duplicateOpenTicketTitleIsRejected() {
        String title = title();
        createTicket("INCIDENT", title, "投影仪无法投屏");
        ResponseEntity<String> again = employee(HttpMethod.POST, "/it/tickets", ticketBody("INCIDENT", title, "投影仪无法投屏"));
        assertThat(again.getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
        assertThat(again.getBody()).contains("标题相同且未结束");
    }

    // ------------------------------------------------------------------ 可见性与范围

    @Test
    void ticketVisibility_internalNotesHiddenFromRequester() {
        long id = createTicket("INCIDENT", title(), "电脑很慢，已经卡死");
        desk(it1, HttpMethod.POST, "/it/desk/tickets/" + id + "/assign", Map.of("assigneeUserId", it1));
        desk(it1, HttpMethod.POST, "/it/desk/tickets/" + id + "/comments", Map.of("body", "怀疑是磁盘问题，内部记录", "internal", true));
        desk(it1, HttpMethod.POST, "/it/desk/tickets/" + id + "/comments", Map.of("body", "已安排检查", "internal", false));

        @SuppressWarnings("unchecked")
        List<Map<String, Object>> mine = (List<Map<String, Object>>) map(employee(HttpMethod.GET, "/it/tickets/" + id, null)).get("comments");
        assertThat(mine).noneMatch(c -> ((String) c.get("body")).contains("内部记录"));
        assertThat(mine).anyMatch(c -> "已安排检查".equals(c.get("body")));
        @SuppressWarnings("unchecked")
        List<Map<String, Object>> internal = (List<Map<String, Object>>) deskTicket(id).get("comments");
        assertThat(internal).anyMatch(c -> ((String) c.get("body")).contains("内部记录") && Boolean.TRUE.equals(c.get("internal")));

        assertThat(call(HttpMethod.GET, "/it/tickets/" + id, null, stranger, teamId, EMPLOYEE, false).getStatusCode())
                .as("别的员工看不到").isEqualTo(HttpStatus.NOT_FOUND);
        assertThat(call(HttpMethod.GET, "/it/tickets/" + id, null, head, teamId, EMPLOYEE, true).getStatusCode())
                .as("部门负责人可以看本部门员工的工单").isEqualTo(HttpStatus.OK);
        assertThat(list(employee(HttpMethod.GET, "/it/tickets/mine", null))).hasSize(1);
        assertThat(list(call(HttpMethod.GET, "/it/tickets/mine", null, stranger, teamId, EMPLOYEE, false))).isEmpty();
    }

    @Test
    void deskScopeAndScopesAreEnforced() {
        long id = createTicket("INCIDENT", title(), "无法登录系统");
        assertThat(call(HttpMethod.GET, "/it/desk/tickets/" + id + "?scopeTeamIds=" + otherTeamId, null, it1, itTeamId, DESK_READ, false)
                .getStatusCode()).as("范围外的工单看起来不存在").isEqualTo(HttpStatus.NOT_FOUND);
        assertThat(call(HttpMethod.POST, "/it/desk/tickets/" + id + "/assign?scopeTeamIds=" + otherTeamId, Map.of("assigneeUserId", it1),
                it1, itTeamId, DESK, false).getStatusCode()).isEqualTo(HttpStatus.NOT_FOUND);
        assertThat(list(call(HttpMethod.GET, "/it/desk/tickets?scopeTeamIds=" + otherTeamId, null, it1, itTeamId, DESK_READ, false))).isEmpty();
        assertThat(call(HttpMethod.GET, "/it/desk/tickets", null, it1, itTeamId, DESK_READ, false).getStatusCode())
                .as("缺少范围").isEqualTo(HttpStatus.BAD_REQUEST);
        assertThat(call(HttpMethod.GET, "/it/desk/tickets?" + scope(), null, requester, teamId, EMPLOYEE, false).getStatusCode())
                .as("员工 scope 不能进 IT 台").isEqualTo(HttpStatus.FORBIDDEN);
        assertThat(call(HttpMethod.POST, "/it/desk/tickets/" + id + "/assign?" + scope(), Map.of("assigneeUserId", it1), it1, itTeamId,
                DESK_READ, false).getStatusCode()).as("只读 scope 不能写").isEqualTo(HttpStatus.FORBIDDEN);
        assertThat(call(HttpMethod.GET, "/it/tickets/mine", null, requester, teamId, DESK, false).getStatusCode())
                .as("IT 台 scope 不能当员工 scope 用").isEqualTo(HttpStatus.FORBIDDEN);
    }

    // ------------------------------------------------------------------ 分类、SLA、自助方案

    @Test
    void reclassifyUpgradesSla_butCannotSneakIntoApprovalCategory() {
        long id = createTicket("INCIDENT", title(), "网络比较慢");
        Instant before = Instant.parse((String) deskTicket(id).get("slaDueAt"));
        Map<String, Object> upgraded = map(desk(it1, HttpMethod.POST, "/it/desk/tickets/" + id + "/reclassify",
                Map.of("category", "INCIDENT", "priority", "URGENT", "reason", "多人受影响")));
        assertThat(upgraded).containsEntry("priority", "URGENT");
        assertThat(Instant.parse((String) upgraded.get("slaDueAt"))).isBefore(before);
        assertThat(desk(it1, HttpMethod.POST, "/it/desk/tickets/" + id + "/reclassify",
                Map.of("category", "PERMISSION", "priority", "NORMAL", "reason", "改成权限")).getStatusCode())
                .isEqualTo(HttpStatus.BAD_REQUEST);
        // 降级不会延长已有截止时间
        Map<String, Object> downgraded = map(desk(it1, HttpMethod.POST, "/it/desk/tickets/" + id + "/reclassify",
                Map.of("category", "INCIDENT", "priority", "LOW", "reason", "影响范围缩小")));
        assertThat(java.time.Duration.between(Instant.parse((String) upgraded.get("slaDueAt")), Instant.parse((String) downgraded.get("slaDueAt"))).abs().toSeconds()).isLessThanOrEqualTo(1L);
        @SuppressWarnings("unchecked")
        List<Map<String, Object>> notes = (List<Map<String, Object>>) deskTicket(id).get("comments");
        assertThat(notes).anyMatch(c -> ((String) c.get("body")).contains("调整分类/优先级"));
    }

    @Test
    void overdueTicketsAreFlagged() {
        long id = createTicket("INCIDENT", title(), "电脑无法开机");
        jdbc.update("UPDATE it_ticket SET sla_due_at = DATE_SUB(UTC_TIMESTAMP(), INTERVAL 1 HOUR) WHERE id = ?", id);
        assertThat(deskTicket(id)).containsEntry("slaStatus", "BREACHED");
        assertThat(list(deskRead(HttpMethod.GET, "/it/desk/tickets?overdue=true"))).anyMatch(t -> ((Number) t.get("id")).longValue() == id);
        assertThat(((Number) map(deskRead(HttpMethod.GET, "/it/desk/summary")).get("overdue")).intValue()).isEqualTo(1);
    }

    @Test
    void classifierAndKnowledgeSuggestions() {
        Map<String, Object> printer = map(employee(HttpMethod.GET, "/it/classify?text=" + enc("打印机无法打印，会议马上开始"), null));
        assertThat(printer).containsEntry("category", "INCIDENT").containsEntry("priority", "HIGH");
        assertThat(map(employee(HttpMethod.GET, "/it/classify?text=" + enc("申请一台笔记本电脑"), null))).containsEntry("category", "DEVICE");
        assertThat(map(employee(HttpMethod.GET, "/it/classify?text=" + enc("需要开通共享盘的访问权限"), null))).containsEntry("category", "PERMISSION");
        assertThat(map(employee(HttpMethod.GET, "/it/classify?text=" + enc("全公司无法上网，网络故障"), null))).containsEntry("priority", "URGENT");
        assertThat(map(employee(HttpMethod.GET, "/it/classify?text=" + enc("忘记密码了"), null))).containsEntry("category", "INCIDENT");
        assertThat(map(employee(HttpMethod.GET, "/it/classify?text=" + enc("请问报销流程"), null))).containsEntry("category", "OTHER");

        List<Map<String, Object>> kb = list(employee(HttpMethod.GET, "/it/kb/suggest?text=" + enc("打印机一直脱机无法打印"), null));
        assertThat(kb).isNotEmpty();
        assertThat((String) kb.get(0).get("title")).contains("打印机");
        assertThat(list(employee(HttpMethod.GET, "/it/kb/suggest?text=" + enc("完全无关的内容"), null))).isEmpty();

        long id = createTicket("DEVICE", title(), "申请一台显示器");
        assertThat(deskTicket(id)).containsEntry("suggestedCategory", "DEVICE");
        assertThat((String) deskTicket(id).get("classifyReason")).contains("设备申请");
    }

    private static String enc(String text) {
        return java.net.URLEncoder.encode(text, StandardCharsets.UTF_8);
    }

    // ------------------------------------------------------------------ 设备

    private long createDevice(String assetNo) {
        ResponseEntity<String> response = desk(it1, HttpMethod.POST, "/it/desk/devices", Map.of("assetNo", assetNo, "deviceType", "LAPTOP",
                "model", "ThinkPad T14", "managingTeamId", itTeamId, "purchasedOn", "2026-01-10", "warrantyUntil", "2029-01-09"));
        assertThat(response.getStatusCode()).as(response.getBody()).isEqualTo(HttpStatus.OK);
        return ((Number) map(response).get("id")).longValue();
    }

    @Test
    void deviceLifecycle() {
        String asset = "T-" + UUID.randomUUID().toString().substring(0, 8);
        long id = createDevice(asset);
        assertThat(desk(it1, HttpMethod.POST, "/it/desk/devices", Map.of("assetNo", asset, "deviceType", "LAPTOP", "model", "x",
                "managingTeamId", itTeamId)).getStatusCode()).as("资产编号重复").isEqualTo(HttpStatus.BAD_REQUEST);
        assertThat(desk(it1, HttpMethod.POST, "/it/desk/devices", Map.of("assetNo", asset + "x", "deviceType", "TOASTER", "model", "x",
                "managingTeamId", itTeamId)).getStatusCode()).as("设备类型无效").isEqualTo(HttpStatus.BAD_REQUEST);
        assertThat(desk(it1, HttpMethod.POST, "/it/desk/devices", Map.of("assetNo", asset + "y", "deviceType", "LAPTOP", "model", "x",
                "managingTeamId", otherTeamId)).getStatusCode()).as("管理部门不在范围内").isEqualTo(HttpStatus.NOT_FOUND);

        // 关联设备申请工单发放
        long ticket = createTicket("DEVICE", title(), "申请一台笔记本电脑");
        call(HttpMethod.POST, "/it/tickets/" + ticket + "/approve", Map.of(), head, teamId, APPROVE, true);
        assertThat(desk(it1, HttpMethod.POST, "/it/desk/devices/" + id + "/assign", Map.of("userId", stranger, "ticketId", ticket))
                .getStatusCode()).as("工单申请人与领用人不一致").isEqualTo(HttpStatus.BAD_REQUEST);
        Map<String, Object> assigned = map(desk(it1, HttpMethod.POST, "/it/desk/devices/" + id + "/assign",
                Map.of("userId", requester, "ticketId", ticket, "note", "随工单发放")));
        assertThat(assigned).containsEntry("status", "ASSIGNED");
        assertThat(((Number) assigned.get("assigneeUserId")).longValue()).isEqualTo(requester);
        assertThat(desk(it1, HttpMethod.POST, "/it/desk/devices/" + id + "/assign", Map.of("userId", stranger)).getStatusCode())
                .as("已领用的设备不能再领用").isEqualTo(HttpStatus.BAD_REQUEST);
        @SuppressWarnings("unchecked")
        List<Map<String, Object>> ticketNotes = (List<Map<String, Object>>) deskTicket(ticket).get("comments");
        assertThat(ticketNotes).anyMatch(c -> ((String) c.get("body")).contains(asset));

        assertThat(list(employee(HttpMethod.GET, "/it/devices/mine", null))).hasSize(1);
        assertThat(desk(it1, HttpMethod.POST, "/it/desk/devices/" + id + "/retire", Map.of("note", "旧了")).getStatusCode())
                .as("还在员工名下不能报废").isEqualTo(HttpStatus.BAD_REQUEST);

        assertThat(map(desk(it1, HttpMethod.POST, "/it/desk/devices/" + id + "/repair", Map.of("note", "屏幕坏点")))).containsEntry("status", "REPAIR");
        Map<String, Object> repaired = map(desk(it1, HttpMethod.POST, "/it/desk/devices/" + id + "/repair-done", Map.of("note", "已换屏")));
        assertThat(repaired).containsEntry("status", "ASSIGNED");
        assertThat(((Number) repaired.get("assigneeUserId")).longValue()).as("修好后仍在原持有人手上").isEqualTo(requester);

        assertThat(map(desk(it1, HttpMethod.POST, "/it/desk/devices/" + id + "/return", Map.of("note", "离职归还")))).containsEntry("status", "IN_STOCK");
        assertThat(list(employee(HttpMethod.GET, "/it/devices/mine", null))).isEmpty();
        Map<String, Object> retired = map(desk(it1, HttpMethod.POST, "/it/desk/devices/" + id + "/retire", Map.of("note", "到期报废")));
        assertThat(retired).containsEntry("status", "RETIRED");
        @SuppressWarnings("unchecked")
        List<Map<String, Object>> events = (List<Map<String, Object>>) map(deskRead(HttpMethod.GET, "/it/desk/devices/" + id)).get("events");
        assertThat(events.stream().map(e -> e.get("eventType")).toList())
                .containsExactly("CREATED", "ASSIGNED", "REPAIR", "REPAIRED", "RETURNED", "RETIRED");
        assertThat(desk(it1, HttpMethod.POST, "/it/desk/devices/" + id + "/assign", Map.of("userId", requester)).getStatusCode())
                .as("报废后不能再领用").isEqualTo(HttpStatus.BAD_REQUEST);
        assertThat(call(HttpMethod.GET, "/it/desk/devices/" + id + "?scopeTeamIds=" + otherTeamId, null, it1, itTeamId, DESK_READ, false)
                .getStatusCode()).isEqualTo(HttpStatus.NOT_FOUND);
    }

    // ------------------------------------------------------------------ 并发

    @Test
    void concurrentDeviceAssignOnlyOneWins() throws Exception {
        long id = createDevice("C-" + UUID.randomUUID().toString().substring(0, 8));
        ExecutorService pool = Executors.newFixedThreadPool(2);
        try {
            Future<HttpStatus> one = pool.submit((Callable<HttpStatus>) () -> (HttpStatus) desk(it1, HttpMethod.POST,
                    "/it/desk/devices/" + id + "/assign", Map.of("userId", requester)).getStatusCode());
            Future<HttpStatus> two = pool.submit((Callable<HttpStatus>) () -> (HttpStatus) desk(it2, HttpMethod.POST,
                    "/it/desk/devices/" + id + "/assign", Map.of("userId", stranger)).getStatusCode());
            assertThat(List.of(one.get(), two.get())).containsExactlyInAnyOrder(HttpStatus.OK, HttpStatus.BAD_REQUEST);
        } finally {
            pool.shutdownNow();
        }
        assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM it_device_event WHERE device_id = ? AND event_type = 'ASSIGNED'", Integer.class, id))
                .isEqualTo(1);
    }

    @Test
    void concurrentResolveOnlyOnce() throws Exception {
        long id = createTicket("INCIDENT", title(), "VPN 连接失败");
        desk(it1, HttpMethod.POST, "/it/desk/tickets/" + id + "/assign", Map.of("assigneeUserId", it1));
        ExecutorService pool = Executors.newFixedThreadPool(2);
        try {
            Future<HttpStatus> one = pool.submit((Callable<HttpStatus>) () -> (HttpStatus) desk(it1, HttpMethod.POST,
                    "/it/desk/tickets/" + id + "/resolve", Map.of("resolution", "重装客户端后恢复")).getStatusCode());
            Future<HttpStatus> two = pool.submit((Callable<HttpStatus>) () -> (HttpStatus) desk(it2, HttpMethod.POST,
                    "/it/desk/tickets/" + id + "/resolve", Map.of("resolution", "重启网络后恢复")).getStatusCode());
            assertThat(List.of(one.get(), two.get())).containsExactlyInAnyOrder(HttpStatus.OK, HttpStatus.BAD_REQUEST);
        } finally {
            pool.shutdownNow();
        }
        assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM it_ticket_comment WHERE ticket_id = ? AND body LIKE '已解决：%'", Integer.class, id))
                .isEqualTo(1);
    }
}
