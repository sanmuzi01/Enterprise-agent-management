package com.enterprisehub.finance;

import com.enterprisehub.finance.dto.VoucherDto.RiskItem;
import org.springframework.stereotype.Component;

import java.math.BigDecimal;
import java.time.Instant;
import java.time.LocalDate;
import java.time.ZoneId;
import java.time.temporal.ChronoUnit;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.stream.Collectors;

/**
 * 凭证风险检查：全部是确定性规则，每一项都说清楚“哪里有问题、为什么要看”。
 * BLOCK 表示按规则不能入账；WARN 表示需要财务人员核对后明确确认；INFO 只是提示。
 * 每次生成、改分录、重新检查、确认入账时都会重算，不依赖过期的结果。
 */
@Component
public class VoucherRiskChecker {
    static final ZoneId BEIJING = ZoneId.of("Asia/Shanghai");
    static final BigDecimal LARGE_LINE = new BigDecimal("5000.00");
    static final int SIMILAR_WINDOW_DAYS = 14;

    private final ExpenseLineRepository lineRepository;
    private final ExpenseBudgetRepository budgetRepository;

    public VoucherRiskChecker(ExpenseLineRepository lineRepository, ExpenseBudgetRepository budgetRepository) {
        this.lineRepository = lineRepository;
        this.budgetRepository = budgetRepository;
    }

    public List<RiskItem> check(Voucher voucher, ExpenseClaim claim, List<VoucherEntry> entries,
                                List<ExpenseLine> lines, Map<String, AccountSubject> subjects) {
        List<RiskItem> risks = new ArrayList<>();
        checkBalance(entries, risks);
        checkSubjects(entries, subjects, risks);
        checkInvoices(claim, lines, risks);
        checkSimilarClaims(claim, lines, risks);
        checkLargeLines(lines, risks);
        checkConfidence(entries, risks);
        checkPeriod(voucher, claim, risks);
        checkBudget(voucher, claim, risks);
        if (entries.stream().anyMatch(e -> "D".equals(e.getDirection()) && e.getSubjectCode().endsWith(".02"))) {
            risks.add(new RiskItem("ENTERTAINMENT", "INFO",
                    "含业务招待费：税前扣除有比例限额，请核对招待对象、事由和人数"));
        }
        return risks;
    }

    public static String overallLevel(List<RiskItem> risks) {
        Set<String> levels = risks.stream().map(RiskItem::level).collect(Collectors.toCollection(LinkedHashSet::new));
        if (levels.contains("BLOCK")) {
            return "BLOCK";
        }
        if (levels.contains("WARN")) {
            return "WARN";
        }
        return levels.contains("INFO") ? "INFO" : "NONE";
    }

    private void checkBalance(List<VoucherEntry> entries, List<RiskItem> risks) {
        BigDecimal debit = sum(entries, "D");
        BigDecimal credit = sum(entries, "C");
        if (entries.isEmpty() || debit.compareTo(credit) != 0 || debit.signum() <= 0) {
            risks.add(new RiskItem("UNBALANCED", "BLOCK",
                    "借贷不平衡：借方 " + debit + "，贷方 " + credit + "，不能入账"));
        }
    }

    private void checkSubjects(List<VoucherEntry> entries, Map<String, AccountSubject> subjects, List<RiskItem> risks) {
        for (VoucherEntry entry : entries) {
            AccountSubject subject = subjects.get(entry.getSubjectCode());
            if (subject == null || !subject.isEnabled()) {
                risks.add(new RiskItem("SUBJECT_INVALID", "BLOCK",
                        "分录 " + entry.getLineNo() + " 的科目 " + entry.getSubjectCode() + " 不存在或已停用"));
            }
        }
    }

    private void checkInvoices(ExpenseClaim claim, List<ExpenseLine> lines, List<RiskItem> risks) {
        List<String> numbers = lines.stream().map(ExpenseLine::getInvoiceNo)
                .filter(n -> n != null && !n.isBlank()).map(String::trim).distinct().toList();
        if (!numbers.isEmpty()) {
            for (Object[] row : lineRepository.findActiveInvoiceUsages(numbers)) {
                long usedBy = ((Number) row[1]).longValue();
                if (usedBy != claim.getId()) {
                    risks.add(new RiskItem("DUPLICATE_INVOICE", "BLOCK",
                            "发票号 " + row[0] + " 同时出现在报销单 #" + usedBy + " 中，存在重复报销"));
                }
            }
        }
        List<ExpenseLine> missing = lines.stream().filter(l -> l.getInvoiceNo() == null || l.getInvoiceNo().isBlank()).toList();
        if (!missing.isEmpty()) {
            BigDecimal amount = missing.stream().map(ExpenseLine::getAmount).reduce(BigDecimal.ZERO, BigDecimal::add);
            risks.add(new RiskItem("NO_INVOICE", "WARN",
                    missing.size() + " 条费用（合计 " + amount + " 元）没有发票号，无票支出税前不能扣除，请确认票据已补齐"));
        }
    }

    private void checkSimilarClaims(ExpenseClaim claim, List<ExpenseLine> lines, List<RiskItem> risks) {
        Instant since = claim.getCreatedAt().minus(SIMILAR_WINDOW_DAYS, ChronoUnit.DAYS);
        Set<Long> similar = new LinkedHashSet<>();
        for (ExpenseLine line : lines) {
            similar.addAll(lineRepository.findSimilarClaims(claim.getApplicantUserId(), claim.getId(),
                    line.getCategory(), line.getAmount(), since));
        }
        if (!similar.isEmpty()) {
            risks.add(new RiskItem("SIMILAR_CLAIM", "WARN",
                    "同一申请人在 " + SIMILAR_WINDOW_DAYS + " 天内有类别和金额都相同的报销（单据 "
                            + similar.stream().map(id -> "#" + id).collect(Collectors.joining("、"))
                            + "），请确认不是重复报销"));
        }
    }

    private void checkLargeLines(List<ExpenseLine> lines, List<RiskItem> risks) {
        long large = lines.stream().filter(l -> l.getAmount().compareTo(LARGE_LINE) >= 0).count();
        if (large > 0) {
            risks.add(new RiskItem("LARGE_LINE", "WARN",
                    large + " 条费用单笔达到 " + LARGE_LINE.toBigInteger() + " 元，请核对原始票据和审批依据"));
        }
    }

    private void checkConfidence(List<VoucherEntry> entries, List<RiskItem> risks) {
        long low = entries.stream().filter(e -> "D".equals(e.getDirection()) && "LOW".equals(e.getConfidence())).count();
        long medium = entries.stream().filter(e -> "D".equals(e.getDirection()) && "MEDIUM".equals(e.getConfidence())).count();
        if (low > 0) {
            risks.add(new RiskItem("LOW_CONFIDENCE", "WARN",
                    low + " 条分录无法可靠判断科目，已暂记“其他”，请改成正确的费用科目"));
        }
        if (medium > 0) {
            risks.add(new RiskItem("MEDIUM_CONFIDENCE", "INFO",
                    medium + " 条分录的科目是按说明关键词或默认规则推断的，请核对依据"));
        }
    }

    private void checkPeriod(Voucher voucher, ExpenseClaim claim, List<RiskItem> risks) {
        String expensePeriod = claim.getCreatedAt().atZone(BEIJING).toLocalDate().toString().substring(0, 7);
        if (!expensePeriod.equals(voucher.getPeriod())) {
            risks.add(new RiskItem("CROSS_PERIOD", "WARN",
                    "报销单创建于 " + expensePeriod + "，凭证入账期间是 " + voucher.getPeriod()
                            + "，跨期入账请确认期间是否正确"));
        }
        LocalDate today = LocalDate.now(BEIJING);
        if (voucher.getVoucherDate().isAfter(today)) {
            risks.add(new RiskItem("FUTURE_DATE", "BLOCK", "凭证日期 " + voucher.getVoucherDate() + " 晚于今天，不能入账"));
        }
    }

    private void checkBudget(Voucher voucher, ExpenseClaim claim, List<RiskItem> risks) {
        int year = Integer.parseInt(voucher.getPeriod().substring(0, 4));
        budgetRepository.findByTeamIdAndYear(claim.getTeamId(), year).ifPresent(budget -> {
            if (budget.getRemainingAmount().signum() <= 0) {
                risks.add(new RiskItem("BUDGET_EXHAUSTED", "WARN",
                        voucher.getPeriod().substring(0, 4) + " 年度该部门报销预算已用完（剩余 "
                                + budget.getRemainingAmount() + " 元），请确认超预算是否已获批准"));
            }
        });
    }

    private static BigDecimal sum(List<VoucherEntry> entries, String direction) {
        return entries.stream().filter(e -> direction.equals(e.getDirection()))
                .map(VoucherEntry::getAmount).reduce(BigDecimal.ZERO, BigDecimal::add);
    }
}
