package com.enterprisehub.finance;

import com.enterprisehub.audit.AuditService;
import com.enterprisehub.finance.dto.ExpenseClaimDto;
import com.enterprisehub.finance.dto.VoucherDto;
import com.enterprisehub.finance.dto.VoucherDto.RiskItem;
import com.enterprisehub.finance.dto.VoucherSummaryDto;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.persistence.criteria.Predicate;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.domain.Sort;
import org.springframework.data.jpa.domain.Specification;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.time.format.DateTimeParseException;
import java.util.ArrayList;
import java.util.Collection;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.regex.Pattern;
import java.util.stream.Collectors;

/**
 * 财务自动记账：报销单批准后，按规则自动生成记账凭证草稿（借：费用科目 / 贷：其他应付款-员工报销款），
 * 财务人员核对科目与风险项后确认入账。系统只“建议”，不替人决定：
 * <ul>
 *   <li>每条借方分录带科目依据和置信度，规则判断不了的明确标成低置信度；</li>
 *   <li>风险检查是确定性规则（借贷平衡、科目有效、重复发票、疑似重复、无票、大额、跨期、预算），逐项给理由；</li>
 *   <li>阻断项不处理就不能入账，需核对项必须由财务明确勾选已核对；</li>
 *   <li>申请人本人不能确认自己报销单的凭证（制单与复核分离）；</li>
 *   <li>所有变更（生成/改科目/确认/作废）都写审计事件。</li>
 * </ul>
 * 调用方权限由 FastAPI 判断（只给财务部门成员签 finance.voucher.* scope），本服务通过 scopeTeamIds 再限定
 * 只能碰同一企业的部门——scopeTeamIds 在已签名的 URL 里，改不了。
 */
@Service
public class VoucherService {
    private static final Pattern PERIOD = Pattern.compile("^\\d{4}-(0[1-9]|1[0-2])$");
    private static final String CREDIT_SUBJECT = "2241.01";
    private static final Map<ExpenseCategory, String> CATEGORY_LABELS = Map.of(
            ExpenseCategory.TRAVEL, "差旅", ExpenseCategory.MEAL, "餐饮", ExpenseCategory.OFFICE_SUPPLY, "办公用品",
            ExpenseCategory.TRANSPORT, "交通", ExpenseCategory.OTHER, "其他");

    private final VoucherRepository voucherRepository;
    private final VoucherEntryRepository entryRepository;
    private final ExpenseClaimRepository claimRepository;
    private final ExpenseLineRepository lineRepository;
    private final AccountSubjectRepository subjectRepository;
    private final SubjectRuleRepository ruleRepository;
    private final VoucherRiskChecker riskChecker;
    private final AuditService auditService;
    private final JdbcTemplate jdbc;
    private final ObjectMapper mapper;

    public VoucherService(VoucherRepository voucherRepository, VoucherEntryRepository entryRepository,
                          ExpenseClaimRepository claimRepository, ExpenseLineRepository lineRepository,
                          AccountSubjectRepository subjectRepository, SubjectRuleRepository ruleRepository,
                          VoucherRiskChecker riskChecker, AuditService auditService, JdbcTemplate jdbc,
                          ObjectMapper mapper) {
        this.voucherRepository = voucherRepository;
        this.entryRepository = entryRepository;
        this.claimRepository = claimRepository;
        this.lineRepository = lineRepository;
        this.subjectRepository = subjectRepository;
        this.ruleRepository = ruleRepository;
        this.riskChecker = riskChecker;
        this.auditService = auditService;
        this.jdbc = jdbc;
        this.mapper = mapper;
    }

    // ---------------------------------------------------------------- 生成

    /** 批准报销单时在同一事务里自动调用；已有凭证则原样返回（幂等）。 */
    @Transactional
    public Voucher generateDraft(ExpenseClaim claim, long operatorId, String traceId) {
        if (claim.getStatus() != ExpenseStatus.APPROVED) {
            throw badRequest("只有已批准的报销单才能生成凭证，当前状态: " + claim.getStatus());
        }
        var existing = voucherRepository.findByExpenseClaimId(claim.getId());
        if (existing.isPresent()) {
            return existing.get();
        }
        List<ExpenseLine> lines = sortedLines(claim.getId());
        String expenseClass = "sales".equalsIgnoreCase(claim.getDepartmentCode()) ? "SALES" : "ADMIN";
        Voucher voucher = new Voucher(claim.getId(), claim.getTeamId(), claim.getApplicantUserId(), expenseClass,
                LocalDate.now(VoucherRiskChecker.BEIJING), summaryOf(claim, lines), claim.getTotalAmount(), operatorId);
        voucherRepository.save(voucher);
        buildEntries(voucher, claim, lines);
        refreshRisk(voucher, claim);
        auditService.record(operatorId, "finance.voucher_generated", "voucher", voucher.getId(),
                "{\"claimId\":" + claim.getId() + ",\"totalAmount\":" + voucher.getTotalAmount() + "}", traceId);
        return voucher;
    }

    /** 财务人员补生成：报销单是上线前批准的、或自动生成当时失败的。 */
    @Transactional
    public VoucherDto generateFromClaim(long claimId, String departmentCode, long operatorId,
                                        Collection<Long> scopeTeamIds, String traceId) {
        ExpenseClaim claim = claimRepository.findForUpdate(claimId).orElseThrow(() -> notFound("报销单不存在"));
        requireInScope(claim.getTeamId(), scopeTeamIds, "报销单不存在");
        if (claim.getDepartmentCode() == null && departmentCode != null && !departmentCode.isBlank()) {
            claim.setDepartmentCode(departmentCode.trim().toLowerCase());
        }
        return toDto(generateDraft(claim, operatorId, traceId));
    }

    /** 丢弃人工修改，按规则重新建议科目。 */
    @Transactional
    public VoucherDto regenerate(long voucherId, long operatorId, Collection<Long> scopeTeamIds, String traceId) {
        Voucher voucher = lockDraft(voucherId, scopeTeamIds);
        ExpenseClaim claim = claimOf(voucher);
        entryRepository.deleteByVoucherId(voucher.getId());
        entryRepository.flush();
        buildEntries(voucher, claim, sortedLines(claim.getId()));
        refreshRisk(voucher, claim);
        auditService.record(operatorId, "finance.voucher_regenerated", "voucher", voucher.getId(), null, traceId);
        return toDto(voucher);
    }

    private void buildEntries(Voucher voucher, ExpenseClaim claim, List<ExpenseLine> lines) {
        Map<String, AccountSubject> subjects = subjectMap();
        String prefix = "SALES".equals(voucher.getExpenseClass()) ? "6601" : "6602";
        int lineNo = 1;
        BigDecimal total = BigDecimal.ZERO;
        for (ExpenseLine line : lines) {
            Suggestion suggestion = suggest(line, prefix, subjects);
            AccountSubject subject = subjects.get(suggestion.code());
            String text = line.getDescription() != null && !line.getDescription().isBlank()
                    ? line.getDescription() : CATEGORY_LABELS.get(line.getCategory()) + "费用";
            entryRepository.save(new VoucherEntry(voucher.getId(), lineNo++, subject.getCode(), subject.getName(),
                    "D", line.getAmount(), clip(text, 200), clip(suggestion.basis(), 300), suggestion.confidence(),
                    line.getId()));
            total = total.add(line.getAmount());
        }
        AccountSubject credit = subjects.get(CREDIT_SUBJECT);
        entryRepository.save(new VoucherEntry(voucher.getId(), lineNo, credit.getCode(), credit.getName(), "C", total,
                clip("报销单 #" + claim.getId() + " 应付员工报销款", 200), "报销单批准后确认对员工的应付款项", "HIGH", null));
    }

    private Suggestion suggest(ExpenseLine line, String prefix, Map<String, AccountSubject> subjects) {
        String description = line.getDescription() == null ? "" : line.getDescription();
        for (SubjectRule rule : ruleRepository.findByCategoryOrderByPriorityDescIdAsc(line.getCategory().name())) {
            boolean matches = rule.getKeyword() == null || description.contains(rule.getKeyword());
            String code = prefix + "." + rule.getSubjectSuffix();
            if (matches && subjects.containsKey(code) && subjects.get(code).isEnabled()) {
                return new Suggestion(code, rule.getNote(), rule.getConfidence());
            }
        }
        return new Suggestion(prefix + ".99", "没有匹配的科目规则，暂记其他，请财务核对", "LOW");
    }

    private record Suggestion(String code, String basis, String confidence) {
    }

    // ---------------------------------------------------------------- 修改与风险

    @Transactional
    public VoucherDto updateEntrySubject(long voucherId, long entryId, String subjectCode, String reason,
                                         long operatorId, Collection<Long> scopeTeamIds, String traceId) {
        Voucher voucher = lockDraft(voucherId, scopeTeamIds);
        VoucherEntry entry = entryRepository.findById(entryId)
                .filter(e -> e.getVoucherId() == voucher.getId()).orElseThrow(() -> notFound("分录不存在"));
        if (!"D".equals(entry.getDirection())) {
            throw badRequest("贷方分录固定为员工应付款，不能修改科目");
        }
        AccountSubject subject = subjectRepository.findById(subjectCode.trim())
                .filter(s -> s.isEnabled() && "EXPENSE".equals(s.getCategory()))
                .orElseThrow(() -> badRequest("科目 " + subjectCode + " 不存在、已停用，或不是费用类科目"));
        String old = entry.getSubjectCode();
        entry.overrideSubject(subject.getCode(), subject.getName(), "改自 " + old + "，原因：" + reason.trim());
        entryRepository.save(entry);
        refreshRisk(voucher, claimOf(voucher));
        auditService.record(operatorId, "finance.voucher_entry_changed", "voucher", voucher.getId(),
                "{\"entryId\":" + entryId + ",\"from\":\"" + old + "\",\"to\":\"" + subject.getCode() + "\"}", traceId);
        return toDto(voucher);
    }

    @Transactional
    public VoucherDto updateVoucherDate(long voucherId, String date, long operatorId, Collection<Long> scopeTeamIds,
                                        String traceId) {
        Voucher voucher = lockDraft(voucherId, scopeTeamIds);
        LocalDate parsed;
        try {
            parsed = LocalDate.parse(date.trim());
        } catch (DateTimeParseException e) {
            throw badRequest("凭证日期格式应为 YYYY-MM-DD");
        }
        if (parsed.isAfter(LocalDate.now(VoucherRiskChecker.BEIJING))) {
            throw badRequest("凭证日期不能晚于今天");
        }
        if (parsed.isBefore(LocalDate.now(VoucherRiskChecker.BEIJING).minusYears(1))) {
            throw badRequest("凭证日期不能早于一年前");
        }
        String old = voucher.getVoucherDate().toString();
        voucher.setVoucherDate(parsed);
        refreshRisk(voucher, claimOf(voucher));
        auditService.record(operatorId, "finance.voucher_date_changed", "voucher", voucher.getId(),
                "{\"from\":\"" + old + "\",\"to\":\"" + parsed + "\"}", traceId);
        return toDto(voucher);
    }

    @Transactional
    public VoucherDto recheck(long voucherId, long operatorId, Collection<Long> scopeTeamIds) {
        Voucher voucher = voucherRepository.findForUpdate(voucherId)
                .filter(v -> scopeTeamIds.contains(v.getTeamId())).orElseThrow(() -> notFound("凭证不存在"));
        if (voucher.getStatus() == VoucherStatus.DRAFT) {
            refreshRisk(voucher, claimOf(voucher));
        }
        return toDto(voucher);
    }

    private List<RiskItem> refreshRisk(Voucher voucher, ExpenseClaim claim) {
        List<VoucherEntry> entries = entryRepository.findByVoucherIdOrderByLineNo(voucher.getId());
        List<RiskItem> risks = riskChecker.check(voucher, claim, entries, sortedLines(claim.getId()), subjectMap());
        try {
            voucher.setRisk(VoucherRiskChecker.overallLevel(risks), mapper.writeValueAsString(risks));
        } catch (JsonProcessingException e) {
            throw new IllegalStateException(e);
        }
        voucherRepository.save(voucher);
        return risks;
    }

    // ---------------------------------------------------------------- 确认与作废

    @Transactional
    public VoucherDto confirm(long voucherId, long operatorId, String note, boolean acknowledgeWarnings,
                              Collection<Long> scopeTeamIds, String traceId) {
        Voucher voucher = lockDraft(voucherId, scopeTeamIds);
        if (voucher.getApplicantUserId() == operatorId) {
            throw badRequest("不能确认自己申请的报销单对应的凭证，请由其他财务人员处理");
        }
        List<RiskItem> risks = refreshRisk(voucher, claimOf(voucher));
        List<RiskItem> blocks = risks.stream().filter(r -> "BLOCK".equals(r.level())).toList();
        if (!blocks.isEmpty()) {
            throw badRequest("存在必须先处理的问题：" + blocks.stream().map(RiskItem::message)
                    .collect(Collectors.joining("；")));
        }
        long warnings = risks.stream().filter(r -> "WARN".equals(r.level())).count();
        if (warnings > 0 && !acknowledgeWarnings) {
            throw badRequest("还有 " + warnings + " 项需要核对的风险，请逐项核对后勾选“已核对风险项”再入账");
        }
        voucher.post(operatorId, note == null || note.isBlank() ? null : note.trim(), warnings > 0,
                nextVoucherNo(voucher.getPeriod()));
        voucherRepository.save(voucher);
        auditService.record(operatorId, "finance.voucher_posted", "voucher", voucher.getId(),
                "{\"voucherNo\":\"" + voucher.getVoucherNo() + "\",\"warnings\":" + warnings + "}", traceId);
        return toDto(voucher);
    }

    @Transactional
    public VoucherDto voidVoucher(long voucherId, String reason, long operatorId, Collection<Long> scopeTeamIds,
                                  String traceId) {
        Voucher voucher = lockDraft(voucherId, scopeTeamIds);
        voucher.voidIt(operatorId, reason.trim());
        voucherRepository.save(voucher);
        auditService.record(operatorId, "finance.voucher_voided", "voucher", voucher.getId(), reason.trim(), traceId);
        return toDto(voucher);
    }

    /** 凭证号按期间连续：记-YYYYMM-NNNN。期间序号行用 FOR UPDATE 串行化，并发确认不会拿到同一个号。 */
    private String nextVoucherNo(String period) {
        jdbc.update("INSERT IGNORE INTO voucher_sequence (period, last_no) VALUES (?, 0)", period);
        Integer last = jdbc.queryForObject("SELECT last_no FROM voucher_sequence WHERE period = ? FOR UPDATE",
                Integer.class, period);
        int next = (last == null ? 0 : last) + 1;
        jdbc.update("UPDATE voucher_sequence SET last_no = ? WHERE period = ?", next, period);
        return String.format("记-%s-%04d", period.replace("-", ""), next);
    }

    // ---------------------------------------------------------------- 查询

    @Transactional(readOnly = true)
    public VoucherDto get(long voucherId, Collection<Long> scopeTeamIds) {
        Voucher voucher = voucherRepository.findById(voucherId)
                .filter(v -> scopeTeamIds.contains(v.getTeamId())).orElseThrow(() -> notFound("凭证不存在"));
        return toDto(voucher);
    }

    @Transactional(readOnly = true)
    public VoucherDto getByClaim(long claimId, Collection<Long> scopeTeamIds) {
        Voucher voucher = voucherRepository.findByExpenseClaimId(claimId)
                .filter(v -> scopeTeamIds.contains(v.getTeamId())).orElseThrow(() -> notFound("该报销单还没有凭证"));
        return toDto(voucher);
    }

    @Transactional(readOnly = true)
    public List<VoucherSummaryDto> list(Collection<Long> scopeTeamIds, String status, String period, int limit) {
        VoucherStatus parsed = null;
        if (status != null && !status.isBlank()) {
            try {
                parsed = VoucherStatus.valueOf(status.trim().toUpperCase());
            } catch (IllegalArgumentException e) {
                throw badRequest("未知的凭证状态: " + status);
            }
        }
        String checkedPeriod = period == null || period.isBlank() ? null : requirePeriod(period);
        VoucherStatus finalStatus = parsed;
        Specification<Voucher> spec = (root, query, cb) -> {
            List<Predicate> predicates = new ArrayList<>();
            predicates.add(root.get("teamId").in(scopeTeamIds));
            if (finalStatus != null) {
                predicates.add(cb.equal(root.get("status"), finalStatus));
            }
            if (checkedPeriod != null) {
                predicates.add(cb.equal(root.get("period"), checkedPeriod));
            }
            return cb.and(predicates.toArray(new Predicate[0]));
        };
        int size = Math.max(1, Math.min(limit, 200));
        return voucherRepository.findAll(spec, PageRequest.of(0, size, Sort.by(Sort.Direction.DESC, "id")))
                .getContent().stream().map(this::toSummary).toList();
    }

    @Transactional(readOnly = true)
    public List<Map<String, Object>> subjects() {
        return subjectRepository.findByEnabledTrueOrderByCode().stream().map(s -> {
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("code", s.getCode());
            row.put("name", s.getName());
            row.put("category", s.getCategory());
            row.put("direction", s.getDirection());
            return row;
        }).toList();
    }

    /** 月度汇总：已入账按科目/部门汇总，待确认和风险积压，已批准但未生成凭证的报销单。 */
    @Transactional(readOnly = true)
    public Map<String, Object> monthlySummary(Collection<Long> scopeTeamIds, String period) {
        String checked = period == null || period.isBlank()
                ? LocalDate.now(VoucherRiskChecker.BEIJING).toString().substring(0, 7) : requirePeriod(period);
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("period", checked);

        Map<String, Map<String, Object>> statusMap = new LinkedHashMap<>();
        for (VoucherStatus s : VoucherStatus.values()) {
            statusMap.put(s.name(), statusRow(0, BigDecimal.ZERO));
        }
        for (Object[] row : voucherRepository.countByStatus(checked, scopeTeamIds)) {
            statusMap.put(((VoucherStatus) row[0]).name(), statusRow(((Number) row[1]).longValue(), (BigDecimal) row[2]));
        }
        result.put("byStatus", statusMap);

        List<Map<String, Object>> bySubject = new ArrayList<>();
        BigDecimal debit = BigDecimal.ZERO;
        BigDecimal credit = BigDecimal.ZERO;
        for (Object[] row : voucherRepository.postedBySubject(checked, scopeTeamIds)) {
            Map<String, Object> item = new LinkedHashMap<>();
            item.put("code", row[0]);
            item.put("name", row[1]);
            item.put("direction", row[2]);
            item.put("amount", row[3]);
            item.put("entries", row[4]);
            bySubject.add(item);
            if ("D".equals(row[2])) {
                debit = debit.add((BigDecimal) row[3]);
            } else {
                credit = credit.add((BigDecimal) row[3]);
            }
        }
        result.put("bySubject", bySubject);
        result.put("debitTotal", debit);
        result.put("creditTotal", credit);
        result.put("balanced", debit.compareTo(credit) == 0);

        List<Map<String, Object>> byTeam = new ArrayList<>();
        for (Object[] row : voucherRepository.postedByTeam(checked, scopeTeamIds)) {
            Map<String, Object> item = new LinkedHashMap<>();
            item.put("teamId", row[0]);
            item.put("count", row[1]);
            item.put("amount", row[2]);
            byTeam.add(item);
        }
        result.put("byTeam", byTeam);

        result.put("efficiency", efficiency(checked, scopeTeamIds,
                ((Number) statusMap.get("POSTED").get("count")).longValue()));

        Map<String, Long> draftRisk = new LinkedHashMap<>();
        for (String level : List.of("NONE", "INFO", "WARN", "BLOCK")) {
            draftRisk.put(level, 0L);
        }
        for (Object[] row : voucherRepository.draftRiskCounts(scopeTeamIds)) {
            draftRisk.put((String) row[0], ((Number) row[1]).longValue());
        }
        result.put("draftRisk", draftRisk);

        List<ExpenseClaim> unbooked = claimRepository.findApprovedWithoutVoucher(scopeTeamIds);
        result.put("unbookedClaims", unbooked.size());
        result.put("unbookedAmount", unbooked.stream().map(ExpenseClaim::getTotalAmount)
                .reduce(BigDecimal.ZERO, BigDecimal::add));
        return result;
    }

    /** 自动记账的效果：已入账凭证里科目建议被原样采纳的比例，以及从自动生成到财务入账的平均耗时。 */
    private Map<String, Object> efficiency(String period, Collection<Long> scopeTeamIds, long posted) {
        Map<String, Object> result = new LinkedHashMap<>();
        long overridden = posted == 0 ? 0 : voucherRepository.countPostedWithManualOverride(period, scopeTeamIds);
        long untouched = posted - overridden;
        double totalHours = 0;
        int timed = 0;
        for (Object[] row : voucherRepository.postedTimings(period, scopeTeamIds)) {
            totalHours += java.time.Duration.between((java.time.Instant) row[0], (java.time.Instant) row[1]).toSeconds() / 3600.0;
            timed++;
        }
        result.put("postedCount", posted);
        result.put("adoptedAsIs", untouched);
        result.put("adoptedRate", posted == 0 ? null : Math.round(untouched * 1000.0 / posted) / 10.0);
        result.put("avgHoursToPost", timed == 0 ? null : Math.round(totalHours / timed * 10.0) / 10.0);
        return result;
    }

    private Map<String, Object> statusRow(long count, BigDecimal amount) {
        Map<String, Object> row = new LinkedHashMap<>();
        row.put("count", count);
        row.put("amount", amount);
        return row;
    }

    // ---------------------------------------------------------------- 内部

    private Voucher lockDraft(long voucherId, Collection<Long> scopeTeamIds) {
        Voucher voucher = voucherRepository.findForUpdate(voucherId)
                .filter(v -> scopeTeamIds.contains(v.getTeamId())).orElseThrow(() -> notFound("凭证不存在"));
        if (voucher.getStatus() != VoucherStatus.DRAFT) {
            throw badRequest("只有草稿状态的凭证能修改、确认或作废，当前状态: " + voucher.getStatus());
        }
        return voucher;
    }

    private void requireInScope(long teamId, Collection<Long> scopeTeamIds, String message) {
        if (!scopeTeamIds.contains(teamId)) {
            throw notFound(message);
        }
    }

    private ExpenseClaim claimOf(Voucher voucher) {
        return claimRepository.findById(voucher.getExpenseClaimId()).orElseThrow(() -> notFound("来源报销单不存在"));
    }

    private List<ExpenseLine> sortedLines(long claimId) {
        return lineRepository.findByExpenseClaimId(claimId).stream()
                .sorted(Comparator.comparing(ExpenseLine::getId)).toList();
    }

    private Map<String, AccountSubject> subjectMap() {
        return subjectRepository.findAll().stream().collect(Collectors.toMap(AccountSubject::getCode, s -> s));
    }

    private String summaryOf(ExpenseClaim claim, List<ExpenseLine> lines) {
        String categories = lines.stream().map(l -> CATEGORY_LABELS.get(l.getCategory())).distinct()
                .collect(Collectors.joining("、"));
        return clip("报销单#" + claim.getId() + " " + categories + "等" + lines.size() + "项", 200);
    }

    private static String clip(String text, int max) {
        return text.length() <= max ? text : text.substring(0, max);
    }

    private String requirePeriod(String period) {
        String trimmed = period.trim();
        if (!PERIOD.matcher(trimmed).matches()) {
            throw badRequest("期间格式应为 YYYY-MM");
        }
        return trimmed;
    }

    private VoucherSummaryDto toSummary(Voucher v) {
        return new VoucherSummaryDto(v.getId(), v.getExpenseClaimId(), v.getTeamId(), v.getApplicantUserId(),
                v.getStatus().name(), v.getVoucherDate().toString(), v.getPeriod(), v.getSummary(), v.getTotalAmount(),
                v.getVoucherNo(), v.getRiskLevel(), v.getCreatedAt());
    }

    private VoucherDto toDto(Voucher v) {
        List<VoucherDto.EntryDto> entries = entryRepository.findByVoucherIdOrderByLineNo(v.getId()).stream()
                .map(e -> new VoucherDto.EntryDto(e.getId(), e.getLineNo(), e.getSubjectCode(), e.getSubjectName(),
                        e.getDirection(), e.getAmount(), e.getSummary(), e.getBasis(), e.getConfidence(),
                        e.isManualOverride())).toList();
        ExpenseClaim claim = claimOf(v);
        List<ExpenseClaimDto.LineDto> claimLines = sortedLines(claim.getId()).stream()
                .map(l -> new ExpenseClaimDto.LineDto(l.getCategory().name(), l.getAmount(), l.getDescription(),
                        l.getInvoiceNo())).toList();
        return new VoucherDto(v.getId(), v.getExpenseClaimId(), v.getTeamId(), v.getApplicantUserId(),
                v.getStatus().name(), v.getExpenseClass(), v.getVoucherDate().toString(), v.getPeriod(),
                v.getSummary(), v.getTotalAmount(), v.getVoucherNo(), v.getRiskLevel(), parseRisks(v.getRiskJson()),
                entries, claimLines,
                new VoucherDto.ClaimInfo(claim.getApproverUserId(), claim.getDecisionNote(), claim.getDecidedAt(),
                        claim.getDepartmentCode(), claim.getCreatedAt()),
                v.getCreatedAt(), v.getConfirmedBy(), v.getConfirmNote(), v.isWarningsAcknowledged(),
                v.getPostedAt(), v.getVoidedBy(), v.getVoidReason(), v.getVoidedAt());
    }

    private List<RiskItem> parseRisks(String json) {
        if (json == null || json.isBlank()) {
            return List.of();
        }
        try {
            return mapper.readValue(json, new TypeReference<List<RiskItem>>() {
            });
        } catch (JsonProcessingException e) {
            return List.of();
        }
    }

    private ResponseStatusException badRequest(String message) {
        return new ResponseStatusException(HttpStatus.BAD_REQUEST, message);
    }

    private ResponseStatusException notFound(String message) {
        return new ResponseStatusException(HttpStatus.NOT_FOUND, message);
    }
}
