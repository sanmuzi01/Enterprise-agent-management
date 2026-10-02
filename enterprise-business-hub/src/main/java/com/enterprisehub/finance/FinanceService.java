package com.enterprisehub.finance;

import com.enterprisehub.audit.AuditService;
import com.enterprisehub.finance.dto.CreateExpenseClaimRequest;
import com.enterprisehub.finance.dto.ExpenseBudgetDto;
import com.enterprisehub.finance.dto.ExpenseClaimDto;
import com.enterprisehub.security.TeamAccessGuard;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;

import java.math.BigDecimal;
import java.time.Year;
import java.util.Collection;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * 财务报销闭环（里程碑4）：员工提交报销单（含多条费用明细）→ 部门负责人批准/拒绝，
 * 批准时扣减部门报销预算。跟 procurement 不同的地方——报销明细的金额是用户直接填的，
 * 不需要查任何主数据（没有类似 Product 的"单价"概念）；批准后也不像采购那样要生成
 * 下一个单据（没有 SAP 对接这一步），比采购更简单。
 *
 * 部门数据隔离：报销单带 teamId，审批必须 TeamAccessGuard 校验调用方跟报销单的
 * teamId 一致，跟 leave/procurement 同一套原则。
 */
@Service
public class FinanceService {
    private final ExpenseBudgetRepository budgetRepository;
    private final ExpenseClaimRepository claimRepository;
    private final ExpenseLineRepository lineRepository;
    private final AuditService auditService;

    public FinanceService(ExpenseBudgetRepository budgetRepository, ExpenseClaimRepository claimRepository,
                           ExpenseLineRepository lineRepository, AuditService auditService) {
        this.budgetRepository = budgetRepository;
        this.claimRepository = claimRepository;
        this.lineRepository = lineRepository;
        this.auditService = auditService;
    }

    public ExpenseBudgetDto getBudget(long teamId, int year) {
        ExpenseBudget budget = budgetRepository.findByTeamIdAndYear(teamId, year)
                .orElseThrow(() -> badRequest("没有 " + year + " 年度的报销预算记录"));
        return ExpenseBudgetDto.from(budget);
    }

    /** 发票号在未驳回报销单中的占用情况，供录入前核对。 */
    public List<Map<String, Object>> invoiceUsages(Collection<String> numbers) {
        List<String> normalized = numbers.stream().filter(n -> n != null && !n.isBlank())
                .map(String::trim).distinct().limit(50).toList();
        if (normalized.isEmpty()) {
            return List.of();
        }
        return lineRepository.findActiveInvoiceUsages(normalized).stream()
                .map(row -> Map.<String, Object>of("invoiceNo", row[0], "claimId", row[1],
                        "status", ((ExpenseStatus) row[2]).name()))
                .toList();
    }

    @Transactional
    public ExpenseClaimDto createDraft(long applicantUserId, long teamId, CreateExpenseClaimRequest body,
                                        String traceId) {
        rejectDuplicateInvoices(body);
        BigDecimal totalAmount = BigDecimal.ZERO;
        for (CreateExpenseClaimRequest.LineItem item : body.lines()) {
            totalAmount = totalAmount.add(item.amount());
        }

        ExpenseClaim claim = new ExpenseClaim(applicantUserId, teamId, totalAmount);
        claimRepository.save(claim);
        for (CreateExpenseClaimRequest.LineItem item : body.lines()) {
            ExpenseCategory category = parseCategory(item.category());
            lineRepository.save(new ExpenseLine(claim.getId(), category, item.amount(), item.description(),
                    item.invoiceNo()));
        }

        auditService.record(applicantUserId, "finance.draft_created", "expense_claim", claim.getId(),
                "{\"lineCount\":" + body.lines().size() + ",\"totalAmount\":" + totalAmount + "}", traceId);
        return toDto(claim);
    }

    /** 同一张发票不能被两张未驳回的报销单使用（无论哪个入口录入：报销模块、Agent 工具、AI 材料整理）。 */
    private void rejectDuplicateInvoices(CreateExpenseClaimRequest body) {
        Set<String> seen = new HashSet<>();
        for (CreateExpenseClaimRequest.LineItem item : body.lines()) {
            String no = item.invoiceNo() == null ? null : item.invoiceNo().trim();
            if (no != null && !no.isEmpty() && !seen.add(no)) {
                throw badRequest("同一报销单中发票号重复: " + no);
            }
        }
        List<Map<String, Object>> used = invoiceUsages(seen);
        if (!used.isEmpty()) {
            Map<String, Object> first = used.get(0);
            throw badRequest("发票号 " + first.get("invoiceNo") + " 已在报销单 #" + first.get("claimId")
                    + " 中使用，不能重复报销");
        }
    }

    @Transactional
    public ExpenseClaimDto submit(long requestId, long applicantUserId, String traceId) {
        ExpenseClaim claim = getOwnedDraft(requestId, applicantUserId);
        ExpenseBudget budget = budgetRepository.findByTeamIdAndYear(claim.getTeamId(), Year.now().getValue())
                .orElseThrow(() -> badRequest("没有本年度的报销预算记录"));
        if (budget.getRemainingAmount().compareTo(claim.getTotalAmount()) < 0) {
            throw badRequest("预算不足：还剩 " + budget.getRemainingAmount() + "，申请了 " + claim.getTotalAmount());
        }
        claim.submit();
        auditService.record(applicantUserId, "finance.submitted", "expense_claim", claim.getId(), null, traceId);
        return toDto(claim);
    }

    @Transactional
    public ExpenseClaimDto approve(long requestId, long approverUserId, String note, String traceId,
                                    Long approverTeamId, boolean isOrgAdmin, boolean isTeamAdmin) {
        ExpenseClaim claim = getSubmitted(requestId);
        TeamAccessGuard.requireTeamAccess(approverTeamId, isOrgAdmin, isTeamAdmin, claim.getTeamId());
        if (claim.getApplicantUserId() == approverUserId) {
            throw badRequest("不能审批自己提交的报销单，需要由部门负责人或企业管理员处理");
        }
        ExpenseBudget budget = budgetRepository.findByTeamIdAndYear(claim.getTeamId(), Year.now().getValue())
                .orElseThrow(() -> badRequest("预算记录不存在，无法批准"));
        if (budget.getRemainingAmount().compareTo(claim.getTotalAmount()) < 0) {
            throw badRequest("批准时预算不足（可能已被其它已批准的报销占用）");
        }
        budget.deduct(claim.getTotalAmount());
        claim.approve(approverUserId, note);
        auditService.record(approverUserId, "finance.approved", "expense_claim", claim.getId(), note, traceId);
        return toDto(claim);
    }

    @Transactional
    public ExpenseClaimDto reject(long requestId, long approverUserId, String note, String traceId,
                                   Long approverTeamId, boolean isOrgAdmin, boolean isTeamAdmin) {
        ExpenseClaim claim = getSubmitted(requestId);
        TeamAccessGuard.requireTeamAccess(approverTeamId, isOrgAdmin, isTeamAdmin, claim.getTeamId());
        if (claim.getApplicantUserId() == approverUserId) {
            throw badRequest("不能处理自己提交的报销单，需要由部门负责人或企业管理员处理");
        }
        claim.reject(approverUserId, note);
        auditService.record(approverUserId, "finance.rejected", "expense_claim", claim.getId(), note, traceId);
        return toDto(claim);
    }

    public ExpenseClaimDto getStatus(long requestId, long requesterUserId, Long requesterTeamId,
                                      boolean isOrgAdmin, boolean isTeamAdmin) {
        ExpenseClaim claim = claimRepository.findById(requestId)
                .orElseThrow(() -> notFound("报销单不存在"));
        TeamAccessGuard.requireOwnerOrTeamAccess(claim.getApplicantUserId(), requesterUserId, requesterTeamId,
                isOrgAdmin, isTeamAdmin, claim.getTeamId());
        return toDto(claim);
    }

    /** "我的报销"列表——天然按 applicantUserId 过滤，不存在跨用户泄露的可能。 */
    public List<ExpenseClaimDto> listMine(long applicantUserId) {
        return claimRepository.findByApplicantUserIdOrderByCreatedAtDesc(applicantUserId).stream()
                .map(this::toDto).toList();
    }

    /** "部门待审批"列表——跟 approve/reject 用同一个 TeamAccessGuard 检查。 */
    public List<ExpenseClaimDto> listTeamPending(long teamId, Long callerTeamId, boolean isOrgAdmin,
                                                  boolean isTeamAdmin) {
        TeamAccessGuard.requireTeamAccess(callerTeamId, isOrgAdmin, isTeamAdmin, teamId);
        return claimRepository.findByTeamIdAndStatusOrderByCreatedAtDesc(teamId, ExpenseStatus.SUBMITTED).stream()
                .map(this::toDto).toList();
    }

    private ExpenseClaimDto toDto(ExpenseClaim claim) {
        List<ExpenseClaimDto.LineDto> lines = lineRepository.findByExpenseClaimId(claim.getId()).stream()
                .map(line -> new ExpenseClaimDto.LineDto(line.getCategory().name(), line.getAmount(),
                        line.getDescription(), line.getInvoiceNo()))
                .toList();
        return new ExpenseClaimDto(
                claim.getId(), claim.getApplicantUserId(), claim.getTeamId(),
                claim.getStatus().name(), claim.getTotalAmount(), lines,
                claim.getApproverUserId(), claim.getDecisionNote(),
                claim.getCreatedAt(), claim.getSubmittedAt(), claim.getDecidedAt()
        );
    }

    private ExpenseClaim getOwnedDraft(long requestId, long applicantUserId) {
        ExpenseClaim claim = claimRepository.findById(requestId)
                .orElseThrow(() -> notFound("报销单不存在"));
        if (claim.getApplicantUserId() != applicantUserId) {
            throw new ResponseStatusException(HttpStatus.NOT_FOUND, "报销单不存在");
        }
        if (claim.getStatus() != ExpenseStatus.DRAFT) {
            throw badRequest("只有草稿状态的报销单能提交，当前状态: " + claim.getStatus());
        }
        return claim;
    }

    private ExpenseClaim getSubmitted(long requestId) {
        ExpenseClaim claim = claimRepository.findById(requestId)
                .orElseThrow(() -> notFound("报销单不存在"));
        if (claim.getStatus() != ExpenseStatus.SUBMITTED) {
            throw badRequest("只有已提交状态的报销单能审批，当前状态: " + claim.getStatus());
        }
        return claim;
    }

    private ExpenseCategory parseCategory(String raw) {
        try {
            return ExpenseCategory.valueOf(raw.toUpperCase());
        } catch (IllegalArgumentException e) {
            throw badRequest("未知的费用类别: " + raw);
        }
    }

    private ResponseStatusException badRequest(String message) {
        return new ResponseStatusException(HttpStatus.BAD_REQUEST, message);
    }

    private ResponseStatusException notFound(String message) {
        return new ResponseStatusException(HttpStatus.NOT_FOUND, message);
    }
}
