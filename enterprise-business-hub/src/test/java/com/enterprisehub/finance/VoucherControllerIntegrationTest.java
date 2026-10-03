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
import java.util.ArrayList;
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

/** 财务自动记账的真实 HTTP 集成测试（真实 MySQL + 签名上下文）：批准即生成凭证草稿、科目建议与依据、
 * 风险检查、确认/作废的规则、制单复核分离、范围隔离、并发确认。ID 段 4_000_000–4_999_000，避开其它测试。 */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
@ActiveProfiles("test")
class VoucherControllerIntegrationTest {
    private static final String HMAC_SECRET = "test-secret-not-for-production";
    private static final ObjectMapper MAPPER = new ObjectMapper();

    @LocalServerPort
    private int port;
    @Autowired
    private TestRestTemplate rest;
    @Autowired
    private JdbcTemplate jdbc;

    private long applicant;
    private long approver;
    private long accountant;
    private long accountant2;
    private long teamId;
    private long otherTeamId;
    private final List<Long> claimIds = new ArrayList<>();

    @BeforeEach
    void setUp() {
        applicant = ThreadLocalRandom.current().nextLong(4_000_000L, 4_990_000L);
        approver = applicant + 1;
        accountant = applicant + 2;
        accountant2 = applicant + 3;
        teamId = applicant;
        otherTeamId = applicant + 5;
        jdbc.update("INSERT INTO expense_budget (team_id, year, remaining_amount) VALUES (?, ?, 100000.00)",
                teamId, java.time.LocalDate.now(java.time.ZoneId.of("Asia/Shanghai")).getYear());
    }

    @AfterEach
    void tearDown() {
        String ids = claimIds.isEmpty() ? "0" : claimIds.stream().map(String::valueOf).collect(java.util.stream.Collectors.joining(","));
        jdbc.update("DELETE FROM voucher_entry WHERE voucher_id IN (SELECT id FROM voucher WHERE team_id IN (?, ?))", teamId, otherTeamId);
        jdbc.update("DELETE FROM voucher WHERE team_id IN (?, ?)", teamId, otherTeamId);
        jdbc.update("DELETE FROM expense_line WHERE expense_claim_id IN (" + ids + ")");
        jdbc.update("DELETE FROM expense_claim WHERE id IN (" + ids + ")");
        jdbc.update("DELETE FROM expense_budget WHERE team_id = ?", teamId);
        jdbc.update("DELETE FROM audit_event WHERE user_id IN (?, ?, ?, ?)", applicant, approver, accountant, accountant2);
    }

    // ------------------------------------------------------------------ 请求辅助

    private static String sha256Hex(byte[] bytes) {
        try {
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes));
        } catch (Exception e) {
            throw new RuntimeException(e);
        }
    }

    private HttpHeaders signed(HttpMethod method, String path, String body, long uid, List<String> scopes,
                               boolean isTeamAdmin) {
        try {
            Map<String, Object> ctx = new HashMap<>();
            ctx.put("user_id", uid);
            ctx.put("team_id", teamId);
            ctx.put("scopes", scopes);
            ctx.put("operation", "voucher_test");
            ctx.put("is_org_admin", false);
            ctx.put("is_team_admin", isTeamAdmin);
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

    private ResponseEntity<String> call(HttpMethod method, String path, Object body, long uid, List<String> scopes,
                                        boolean isTeamAdmin) {
        try {
            String json = body == null ? null : MAPPER.writeValueAsString(body);
            return rest.exchange("http://127.0.0.1:" + port + path, method,
                    new HttpEntity<>(json, signed(method, path, json, uid, scopes, isTeamAdmin)), String.class);
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
        return "scopeTeamIds=" + teamId;
    }

    private String voucherPath(long id, String suffix) {
        return "/finance/vouchers/" + id + suffix + (suffix.contains("?") ? "&" : "?") + scope();
    }

    private static final List<String> READ = List.of("finance.voucher.read");
    private static final List<String> WRITE = List.of("finance.voucher.read", "finance.voucher.write");

    /** 员工创建→提交→部门负责人批准，返回批准后的报销单 id（批准时自动生成凭证）。 */
    private long approvedClaim(String departmentCode, List<Map<String, Object>> lines) {
        Map<String, Object> draftBody = new HashMap<>();
        draftBody.put("lines", lines);
        if (departmentCode != null) {
            draftBody.put("departmentCode", departmentCode);
        }
        long id = ((Number) map(call(HttpMethod.POST, "/finance/expenses", draftBody, applicant,
                List.of("finance.write"), false)).get("id")).longValue();
        claimIds.add(id);
        assertThat(call(HttpMethod.POST, "/finance/expenses/" + id + "/submit", null, applicant,
                List.of("finance.write"), false).getStatusCode()).isEqualTo(HttpStatus.OK);
        ResponseEntity<String> approved = call(HttpMethod.POST, "/finance/expenses/" + id + "/approve",
                Map.of("note", "同意"), approver, List.of("finance.approve"), true);
        assertThat(approved.getStatusCode()).as(approved.getBody()).isEqualTo(HttpStatus.OK);
        return id;
    }

    private static Map<String, Object> line(String category, int amount, String description, String invoiceNo) {
        Map<String, Object> line = new HashMap<>();
        line.put("category", category);
        line.put("amount", amount);
        line.put("description", description);
        if (invoiceNo != null) {
            line.put("invoiceNo", invoiceNo);
        }
        return line;
    }

    private static String invoice() {
        return "INV-" + UUID.randomUUID().toString().substring(0, 10);
    }

    private Map<String, Object> voucherOfClaim(long claimId) {
        return map(call(HttpMethod.GET, "/finance/vouchers/by-claim/" + claimId + "?" + scope(), null, accountant, READ, false));
    }

    @SuppressWarnings("unchecked")
    private List<Map<String, Object>> entries(Map<String, Object> voucher) {
        return (List<Map<String, Object>>) voucher.get("entries");
    }

    @SuppressWarnings("unchecked")
    private List<String> riskCodes(Map<String, Object> voucher) {
        return ((List<Map<String, Object>>) voucher.get("risks")).stream().map(r -> (String) r.get("code")).toList();
    }

    // ------------------------------------------------------------------ 自动生成与科目建议

    @Test
    void approvalAutoGeneratesBalancedDraftWithSubjectEvidence() {
        long claim = approvedClaim("finance", List.of(
                line("TRAVEL", 260, "高铁票", invoice()),
                line("MEAL", 180, "客户宴请", invoice()),
                line("OFFICE_SUPPLY", 40, "打印纸", invoice())));
        Map<String, Object> voucher = voucherOfClaim(claim);

        assertThat(voucher.get("status")).isEqualTo("DRAFT");
        assertThat(voucher.get("expenseClass")).isEqualTo("ADMIN");
        assertThat(((Number) voucher.get("totalAmount")).doubleValue()).isEqualTo(480.00);
        List<Map<String, Object>> entries = entries(voucher);
        assertThat(entries).hasSize(4);
        assertThat(entries.get(0)).containsEntry("subjectCode", "6602.01").containsEntry("direction", "D")
                .containsEntry("confidence", "HIGH");
        assertThat(entries.get(1)).containsEntry("subjectCode", "6602.02").containsEntry("confidence", "HIGH");
        assertThat((String) entries.get(1).get("basis")).contains("招待");
        assertThat(entries.get(2)).containsEntry("subjectCode", "6602.03");
        assertThat(entries.get(3)).containsEntry("subjectCode", "2241.01").containsEntry("direction", "C");
        assertThat(((Number) entries.get(3).get("amount")).doubleValue()).isEqualTo(480.00);
        assertThat(riskCodes(voucher)).contains("ENTERTAINMENT");
        assertThat(voucher.get("riskLevel")).isEqualTo("INFO");
    }

    @Test
    void salesDepartmentUsesSellingExpenseSubjects_andAmbiguousMealIsFlagged() {
        long claim = approvedClaim("sales", List.of(line("MEAL", 90, "加班工作餐", invoice()),
                line("OTHER", 35, "客户那边打车", invoice()), line("OTHER", 12, "不明支出", invoice())));
        Map<String, Object> voucher = voucherOfClaim(claim);
        assertThat(voucher.get("expenseClass")).isEqualTo("SALES");
        List<Map<String, Object>> entries = entries(voucher);
        assertThat(entries.get(0)).containsEntry("subjectCode", "6601.05").containsEntry("confidence", "MEDIUM");
        assertThat(entries.get(1)).containsEntry("subjectCode", "6601.04").containsEntry("confidence", "MEDIUM");
        assertThat(entries.get(2)).containsEntry("subjectCode", "6601.99").containsEntry("confidence", "LOW");
        assertThat(riskCodes(voucher)).contains("LOW_CONFIDENCE", "MEDIUM_CONFIDENCE");
        assertThat(voucher.get("riskLevel")).isEqualTo("WARN");
    }

    @Test
    void generationIsAtomicWithApproval_andNotDuplicatedOnManualGenerate() {
        long claim = approvedClaim(null, List.of(line("TRAVEL", 100, "出差", invoice())));
        long voucherId = ((Number) voucherOfClaim(claim).get("id")).longValue();
        ResponseEntity<String> again = call(HttpMethod.POST, "/finance/vouchers/from-claim/" + claim + "?" + scope(),
                Map.of(), accountant, WRITE, false);
        assertThat(again.getStatusCode()).isEqualTo(HttpStatus.OK);
        assertThat(((Number) map(again).get("id")).longValue()).isEqualTo(voucherId);
        assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM voucher WHERE expense_claim_id = ?", Integer.class, claim))
                .isEqualTo(1);
    }

    @Test
    void approvedClaimWithoutVoucherIsListedAndCanBeBackfilled() {
        long claim = approvedClaim("sales", List.of(line("TRAVEL", 100, "出差", invoice())));
        jdbc.update("DELETE FROM voucher_entry WHERE voucher_id IN (SELECT id FROM voucher WHERE expense_claim_id = ?)", claim);
        jdbc.update("DELETE FROM voucher WHERE expense_claim_id = ?", claim);

        List<Map<String, Object>> unbooked = list(call(HttpMethod.GET, "/finance/vouchers/unbooked?" + scope(), null,
                accountant, READ, false));
        assertThat(unbooked).anyMatch(c -> ((Number) c.get("id")).longValue() == claim);
        Map<String, Object> summary = map(call(HttpMethod.GET, "/finance/vouchers/summary?" + scope(), null, accountant, READ, false));
        assertThat(((Number) summary.get("unbookedClaims")).intValue()).isEqualTo(1);

        ResponseEntity<String> generated = call(HttpMethod.POST, "/finance/vouchers/from-claim/" + claim + "?" + scope(),
                Map.of("departmentCode", "sales"), accountant, WRITE, false);
        assertThat(generated.getStatusCode()).isEqualTo(HttpStatus.OK);
        assertThat(entries(map(generated)).get(0)).containsEntry("subjectCode", "6601.01");
    }

    // ------------------------------------------------------------------ 风险检查

    @Test
    void missingInvoiceAndLargeLineRequireAcknowledgementBeforePosting() {
        long claim = approvedClaim(null, List.of(line("TRAVEL", 6000, "培训差旅", null)));
        Map<String, Object> voucher = voucherOfClaim(claim);
        long id = ((Number) voucher.get("id")).longValue();
        assertThat(riskCodes(voucher)).contains("NO_INVOICE", "LARGE_LINE");

        ResponseEntity<String> blocked = call(HttpMethod.POST, voucherPath(id, "/confirm"), Map.of("note", "核对过"),
                accountant, WRITE, false);
        assertThat(blocked.getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
        assertThat(blocked.getBody()).contains("已核对风险项");

        ResponseEntity<String> posted = call(HttpMethod.POST, voucherPath(id, "/confirm"),
                Map.of("note", "票据已补齐", "acknowledgeWarnings", true), accountant, WRITE, false);
        assertThat(posted.getStatusCode()).isEqualTo(HttpStatus.OK);
        Map<String, Object> body = map(posted);
        assertThat(body.get("status")).isEqualTo("POSTED");
        assertThat((String) body.get("voucherNo")).matches("记-\\d{6}-\\d{4}");
        assertThat(body.get("warningsAcknowledged")).isEqualTo(true);
        assertThat(((Number) body.get("confirmedBy")).longValue()).isEqualTo(accountant);
    }

    @Test
    void duplicateInvoiceInAnotherClaimBlocksPosting() {
        String shared = invoice();
        long claim = approvedClaim(null, List.of(line("TRAVEL", 100, "出差", shared)));
        long id = ((Number) voucherOfClaim(claim).get("id")).longValue();
        // 绕过创建时的重复拦截，模拟历史脏数据：另一张未驳回报销单里出现了同一张发票
        jdbc.update("INSERT INTO expense_claim (applicant_user_id, team_id, status, total_amount, created_at) "
                + "VALUES (?, ?, 'SUBMITTED', 50.00, NOW())", applicant + 9, teamId);
        long other = jdbc.queryForObject("SELECT MAX(id) FROM expense_claim WHERE applicant_user_id = ?", Long.class, applicant + 9);
        claimIds.add(other);
        jdbc.update("INSERT INTO expense_line (expense_claim_id, category, amount, description, invoice_no) "
                + "VALUES (?, 'TRAVEL', 50.00, '重复', ?)", other, shared);

        Map<String, Object> rechecked = map(call(HttpMethod.POST, voucherPath(id, "/recheck"), null, accountant, WRITE, false));
        assertThat(rechecked.get("riskLevel")).isEqualTo("BLOCK");
        assertThat(riskCodes(rechecked)).contains("DUPLICATE_INVOICE");
        ResponseEntity<String> confirm = call(HttpMethod.POST, voucherPath(id, "/confirm"),
                Map.of("acknowledgeWarnings", true), accountant, WRITE, false);
        assertThat(confirm.getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
        assertThat(confirm.getBody()).contains("重复报销");
    }

    @Test
    void similarClaimFromSameApplicantIsWarned() {
        approvedClaim(null, List.of(line("MEAL", 88, "午餐", invoice())));
        long second = approvedClaim(null, List.of(line("MEAL", 88, "午餐", invoice())));
        assertThat(riskCodes(voucherOfClaim(second))).contains("SIMILAR_CLAIM");
    }

    // ------------------------------------------------------------------ 人工修改

    @Test
    void financeCanChangeDebitSubjectButNotCreditOrInvalidSubject() {
        long claim = approvedClaim("finance", List.of(line("OTHER", 50, "不明支出", invoice())));
        Map<String, Object> voucher = voucherOfClaim(claim);
        long id = ((Number) voucher.get("id")).longValue();
        assertThat(voucher.get("riskLevel")).isEqualTo("WARN");
        long debitId = ((Number) entries(voucher).get(0).get("id")).longValue();
        long creditId = ((Number) entries(voucher).get(1).get("id")).longValue();

        String entryPath = "/finance/vouchers/" + id + "/entries/" + debitId + "/subject?" + scope();
        assertThat(call(HttpMethod.POST, entryPath, Map.of("subjectCode", "9999", "reason", "改一下"), accountant,
                WRITE, false).getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
        assertThat(call(HttpMethod.POST, entryPath, Map.of("subjectCode", "2241.01", "reason", "改一下"), accountant,
                WRITE, false).getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
        assertThat(call(HttpMethod.POST, "/finance/vouchers/" + id + "/entries/" + creditId + "/subject?" + scope(),
                Map.of("subjectCode", "6602.03", "reason", "改一下"), accountant, WRITE, false).getStatusCode())
                .isEqualTo(HttpStatus.BAD_REQUEST);

        ResponseEntity<String> changed = call(HttpMethod.POST, entryPath,
                Map.of("subjectCode", "6602.03", "reason", "实为办公用品采购"), accountant, WRITE, false);
        assertThat(changed.getStatusCode()).isEqualTo(HttpStatus.OK);
        Map<String, Object> updated = map(changed);
        assertThat(entries(updated).get(0)).containsEntry("subjectCode", "6602.03").containsEntry("confidence", "MANUAL")
                .containsEntry("manualOverride", true);
        assertThat((String) entries(updated).get(0).get("basis")).contains("改自", "实为办公用品采购");
        assertThat(riskCodes(updated)).doesNotContain("LOW_CONFIDENCE");
        assertThat(updated.get("riskLevel")).isEqualTo("NONE");
        assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM audit_event WHERE user_id = ? AND action = 'finance.voucher_entry_changed'",
                Integer.class, accountant)).isEqualTo(1);

        // 重新生成会丢弃人工修改，回到规则建议
        Map<String, Object> regenerated = map(call(HttpMethod.POST, voucherPath(id, "/regenerate"), null, accountant, WRITE, false));
        assertThat(entries(regenerated).get(0)).containsEntry("subjectCode", "6602.99").containsEntry("confidence", "LOW");
    }

    @Test
    void voucherDateCannotBeInTheFuture_andPeriodFollowsDate() {
        long claim = approvedClaim(null, List.of(line("TRAVEL", 100, "出差", invoice())));
        long id = ((Number) voucherOfClaim(claim).get("id")).longValue();
        assertThat(call(HttpMethod.POST, "/finance/vouchers/" + id + "/date?" + scope(), Map.of("voucherDate", "2999-01-01"),
                accountant, WRITE, false).getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
        assertThat(call(HttpMethod.POST, "/finance/vouchers/" + id + "/date?" + scope(), Map.of("voucherDate", "不是日期"),
                accountant, WRITE, false).getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
        String lastMonth = java.time.LocalDate.now(java.time.ZoneId.of("Asia/Shanghai")).minusMonths(1).withDayOfMonth(15).toString();
        Map<String, Object> changed = map(call(HttpMethod.POST, "/finance/vouchers/" + id + "/date?" + scope(),
                Map.of("voucherDate", lastMonth), accountant, WRITE, false));
        assertThat(changed.get("period")).isEqualTo(lastMonth.substring(0, 7));
        assertThat(riskCodes(changed)).contains("CROSS_PERIOD");
    }

    // ------------------------------------------------------------------ 确认、作废与权限

    @Test
    void applicantCannotConfirmOwnVoucher() {
        long claim = approvedClaim(null, List.of(line("TRAVEL", 100, "出差", invoice())));
        long id = ((Number) voucherOfClaim(claim).get("id")).longValue();
        ResponseEntity<String> response = call(HttpMethod.POST, voucherPath(id, "/confirm"), Map.of(), applicant, WRITE, false);
        assertThat(response.getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
        assertThat(response.getBody()).contains("不能确认自己申请");
    }

    @Test
    void postedAndVoidedVouchersAreImmutable() {
        long first = approvedClaim(null, List.of(line("TRAVEL", 100, "出差", invoice())));
        long second = approvedClaim(null, List.of(line("TRAVEL", 101, "出差", invoice())));
        long postedId = ((Number) voucherOfClaim(first).get("id")).longValue();
        long voidedId = ((Number) voucherOfClaim(second).get("id")).longValue();
        assertThat(call(HttpMethod.POST, voucherPath(postedId, "/confirm"), Map.of(), accountant, WRITE, false)
                .getStatusCode()).isEqualTo(HttpStatus.OK);
        assertThat(call(HttpMethod.POST, voucherPath(voidedId, "/void"), Map.of("reason", "票据不合规，退回申请人"), accountant,
                WRITE, false).getStatusCode()).isEqualTo(HttpStatus.OK);

        for (long id : List.of(postedId, voidedId)) {
            assertThat(call(HttpMethod.POST, voucherPath(id, "/confirm"), Map.of(), accountant2, WRITE, false).getStatusCode())
                    .isEqualTo(HttpStatus.BAD_REQUEST);
            assertThat(call(HttpMethod.POST, voucherPath(id, "/void"), Map.of("reason", "再作废一次"), accountant, WRITE, false)
                    .getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
            assertThat(call(HttpMethod.POST, voucherPath(id, "/regenerate"), null, accountant, WRITE, false).getStatusCode())
                    .isEqualTo(HttpStatus.BAD_REQUEST);
        }
        Map<String, Object> voided = map(call(HttpMethod.GET, voucherPath(voidedId, ""), null, accountant, READ, false));
        assertThat(voided).containsEntry("status", "VOID").containsEntry("voidReason", "票据不合规，退回申请人");
    }

    @Test
    void scopeIsRequired_andVouchersOutsideScopeLookNonexistent() {
        long claim = approvedClaim(null, List.of(line("TRAVEL", 100, "出差", invoice())));
        long id = ((Number) voucherOfClaim(claim).get("id")).longValue();

        assertThat(call(HttpMethod.GET, "/finance/vouchers/" + id + "?scopeTeamIds=" + otherTeamId, null, accountant, READ,
                false).getStatusCode()).isEqualTo(HttpStatus.NOT_FOUND);
        assertThat(call(HttpMethod.POST, "/finance/vouchers/" + id + "/confirm?scopeTeamIds=" + otherTeamId, Map.of(),
                accountant, WRITE, false).getStatusCode()).isEqualTo(HttpStatus.NOT_FOUND);
        assertThat(call(HttpMethod.GET, "/finance/vouchers/by-claim/" + claim + "?scopeTeamIds=" + otherTeamId, null,
                accountant, READ, false).getStatusCode()).isEqualTo(HttpStatus.NOT_FOUND);
        assertThat(list(call(HttpMethod.GET, "/finance/vouchers?scopeTeamIds=" + otherTeamId, null, accountant, READ, false)))
                .isEmpty();
        assertThat(call(HttpMethod.GET, "/finance/vouchers/" + id, null, accountant, READ, false).getStatusCode())
                .isEqualTo(HttpStatus.BAD_REQUEST);
        // 范围被篡改：签名里是 teamId，请求里改成别的，签名校验失败
        try {
            HttpHeaders headers = signed(HttpMethod.GET, "/finance/vouchers/" + id + "?" + scope(), null, accountant, READ, false);
            ResponseEntity<String> tampered = rest.exchange("http://127.0.0.1:" + port + "/finance/vouchers/" + id
                    + "?scopeTeamIds=" + teamId + "," + otherTeamId, HttpMethod.GET, new HttpEntity<>(headers), String.class);
            assertThat(tampered.getStatusCode()).isEqualTo(HttpStatus.UNAUTHORIZED);
        } catch (org.springframework.web.client.ResourceAccessException e) {
            assertThat(e.getMessage()).contains("server authentication", "streaming mode");
        }
    }

    @Test
    void writeNeedsWriteScope_readNeedsReadScope() {
        long claim = approvedClaim(null, List.of(line("TRAVEL", 100, "出差", invoice())));
        long id = ((Number) voucherOfClaim(claim).get("id")).longValue();
        assertThat(call(HttpMethod.POST, voucherPath(id, "/confirm"), Map.of(), accountant, READ, false).getStatusCode())
                .isEqualTo(HttpStatus.FORBIDDEN);
        assertThat(call(HttpMethod.GET, voucherPath(id, ""), null, accountant, List.of("finance.read"), false).getStatusCode())
                .isEqualTo(HttpStatus.FORBIDDEN);
    }

    // ------------------------------------------------------------------ 汇总与并发

    @Test
    void monthlySummaryAggregatesPostedVouchersBySubjectAndKeepsBackloggedDrafts() {
        long a = approvedClaim("finance", List.of(line("TRAVEL", 300, "出差", invoice()), line("OFFICE_SUPPLY", 70, "文具", invoice())));
        long b = approvedClaim("finance", List.of(line("TRAVEL", 200, "出差", invoice())));
        approvedClaim("finance", List.of(line("OTHER", 10, "不明", invoice())));   // 留作草稿
        for (long claim : List.of(a, b)) {
            long id = ((Number) voucherOfClaim(claim).get("id")).longValue();
            assertThat(call(HttpMethod.POST, voucherPath(id, "/confirm"), Map.of(), accountant, WRITE, false).getStatusCode())
                    .isEqualTo(HttpStatus.OK);
        }
        Map<String, Object> summary = map(call(HttpMethod.GET, "/finance/vouchers/summary?" + scope(), null, accountant, READ, false));
        @SuppressWarnings("unchecked")
        Map<String, Map<String, Object>> byStatus = (Map<String, Map<String, Object>>) summary.get("byStatus");
        assertThat(((Number) byStatus.get("POSTED").get("count")).intValue()).isEqualTo(2);
        assertThat(((Number) byStatus.get("POSTED").get("amount")).doubleValue()).isEqualTo(570.00);
        assertThat(((Number) byStatus.get("DRAFT").get("count")).intValue()).isEqualTo(1);
        assertThat(summary.get("balanced")).isEqualTo(true);
        assertThat(((Number) summary.get("debitTotal")).doubleValue()).isEqualTo(570.00);
        @SuppressWarnings("unchecked")
        List<Map<String, Object>> bySubject = (List<Map<String, Object>>) summary.get("bySubject");
        Map<String, Object> travel = bySubject.stream().filter(r -> "6602.01".equals(r.get("code"))).findFirst().orElseThrow();
        assertThat(((Number) travel.get("amount")).doubleValue()).isEqualTo(500.00);
        @SuppressWarnings("unchecked")
        Map<String, Object> draftRisk = (Map<String, Object>) summary.get("draftRisk");
        assertThat(((Number) draftRisk.get("WARN")).intValue()).isEqualTo(1);

        assertThat(call(HttpMethod.GET, "/finance/vouchers/summary?period=2026-13&" + scope(), null, accountant, READ, false)
                .getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
    }

    @Test
    void efficiencyShowsHowOftenSuggestionsWereAdoptedAsIs() {
        long a = approvedClaim("finance", List.of(line("TRAVEL", 300, "出差", invoice())));
        long b = approvedClaim("finance", List.of(line("OTHER", 50, "不明支出", invoice())));
        Map<String, Object> voucherB = voucherOfClaim(b);
        long idB = ((Number) voucherB.get("id")).longValue();
        long debitB = ((Number) entries(voucherB).get(0).get("id")).longValue();
        assertThat(call(HttpMethod.POST, "/finance/vouchers/" + idB + "/entries/" + debitB + "/subject?" + scope(),
                Map.of("subjectCode", "6602.03", "reason", "实为办公用品"), accountant, WRITE, false).getStatusCode())
                .isEqualTo(HttpStatus.OK);
        for (long claim : List.of(a, b)) {
            long id = ((Number) voucherOfClaim(claim).get("id")).longValue();
            assertThat(call(HttpMethod.POST, voucherPath(id, "/confirm"), Map.of("acknowledgeWarnings", true), accountant2,
                    WRITE, false).getStatusCode()).isEqualTo(HttpStatus.OK);
        }
        @SuppressWarnings("unchecked")
        Map<String, Object> efficiency = (Map<String, Object>) map(call(HttpMethod.GET, "/finance/vouchers/summary?" + scope(),
                null, accountant, READ, false)).get("efficiency");
        assertThat(((Number) efficiency.get("postedCount")).intValue()).isEqualTo(2);
        assertThat(((Number) efficiency.get("adoptedAsIs")).intValue()).isEqualTo(1);
        assertThat(((Number) efficiency.get("adoptedRate")).doubleValue()).isEqualTo(50.0);
        assertThat(((Number) efficiency.get("avgHoursToPost")).doubleValue()).isBetween(0.0, 1.0);
    }

    @Test
    void concurrentConfirmPostsOnlyOnce() throws Exception {
        long claim = approvedClaim(null, List.of(line("TRAVEL", 100, "出差", invoice())));
        long id = ((Number) voucherOfClaim(claim).get("id")).longValue();
        ExecutorService pool = Executors.newFixedThreadPool(2);
        try {
            Callable<HttpStatus> confirm = () -> (HttpStatus) call(HttpMethod.POST, voucherPath(id, "/confirm"), Map.of(),
                    accountant2, WRITE, false).getStatusCode();
            Future<HttpStatus> one = pool.submit(confirm);
            Future<HttpStatus> two = pool.submit(confirm);
            List<HttpStatus> results = List.of(one.get(), two.get());
            assertThat(results).containsExactlyInAnyOrder(HttpStatus.OK, HttpStatus.BAD_REQUEST);
        } finally {
            pool.shutdownNow();
        }
        assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM voucher WHERE id = ? AND status = 'POSTED' AND voucher_no IS NOT NULL",
                Integer.class, id)).isEqualTo(1);
    }

    @Test
    void concurrentApprovalDeductsBudgetOnceAndCreatesOneVoucher() throws Exception {
        Map<String, Object> body = Map.of("lines", List.of(line("TRAVEL", 400, "出差", invoice())));
        long id = ((Number) map(call(HttpMethod.POST, "/finance/expenses", body, applicant, List.of("finance.write"), false))
                .get("id")).longValue();
        claimIds.add(id);
        call(HttpMethod.POST, "/finance/expenses/" + id + "/submit", null, applicant, List.of("finance.write"), false);
        ExecutorService pool = Executors.newFixedThreadPool(2);
        try {
            Callable<HttpStatus> approve = () -> (HttpStatus) call(HttpMethod.POST, "/finance/expenses/" + id + "/approve",
                    Map.of("note", "ok"), approver, List.of("finance.approve"), true).getStatusCode();
            Future<HttpStatus> one = pool.submit(approve);
            Future<HttpStatus> two = pool.submit(approve);
            assertThat(List.of(one.get(), two.get())).containsExactlyInAnyOrder(HttpStatus.OK, HttpStatus.BAD_REQUEST);
        } finally {
            pool.shutdownNow();
        }
        assertThat(jdbc.queryForObject("SELECT remaining_amount FROM expense_budget WHERE team_id = ?", Double.class, teamId))
                .isEqualTo(99600.00);
        assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM voucher WHERE expense_claim_id = ?", Integer.class, id)).isEqualTo(1);
    }
}
