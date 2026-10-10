package com.enterprisehub.finance;

import com.enterprisehub.finance.dto.CreateExpenseClaimRequest;
import com.enterprisehub.finance.dto.ExpenseClaimDto;
import com.enterprisehub.finance.dto.ExpenseDecisionRequest;
import com.enterprisehub.idempotency.IdempotencyService;
import com.enterprisehub.security.RequestContext;
import com.enterprisehub.security.RequestContextHolder;
import com.enterprisehub.security.ScopeGuard;
import jakarta.validation.Valid;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.time.Year;
import java.util.List;

/** 财务报销闭环的 REST 接口，供 FastAPI 侧的财务 Agent 工具调用
 * （service/tools/finance.py，跟 oa_leave.py/procurement.py/crm.py 是同一种薄工具层）。 */
@RestController
@RequestMapping("/finance")
public class FinanceController {
    public static final String IDEMPOTENCY_HEADER = "Idempotency-Key";

    private final FinanceService financeService;
    private final IdempotencyService idempotencyService;

    public FinanceController(FinanceService financeService, IdempotencyService idempotencyService) {
        this.financeService = financeService;
        this.idempotencyService = idempotencyService;
    }

    @GetMapping("/budget")
    public Object getBudget(@RequestParam(required = false) Integer year) {
        ScopeGuard.require("finance.read");
        long teamId = requireTeamId(RequestContextHolder.current());
        int y = year != null ? year : Year.now().getValue();
        return financeService.getBudget(teamId, y);
    }

    @GetMapping("/invoices/usage")
    public Object invoiceUsage(@RequestParam("numbers") java.util.List<String> numbers) {
        ScopeGuard.require("finance.read");
        return financeService.invoiceUsages(numbers);
    }

    @PostMapping("/expenses")
    public ResponseEntity<Object> createDraft(@Valid @RequestBody CreateExpenseClaimRequest body,
                                               @RequestHeader(value = IDEMPOTENCY_HEADER, required = false) String idempotencyKey) {
        ScopeGuard.require("finance.write");
        RequestContext ctx = RequestContextHolder.current();
        long teamId = requireTeamId(ctx);
        return idempotencyService.execute(idempotencyKey, () -> {
            var dto = financeService.createDraft(ctx.userId(), teamId, body, ctx.traceId());
            return ResponseEntity.<Object>ok(dto);
        });
    }

    @PostMapping("/expenses/{id}/submit")
    public ResponseEntity<Object> submit(@PathVariable("id") long id,
                                          @RequestHeader(value = IDEMPOTENCY_HEADER, required = false) String idempotencyKey) {
        ScopeGuard.require("finance.write");
        RequestContext ctx = RequestContextHolder.current();
        return idempotencyService.execute(idempotencyKey, () -> {
            var dto = financeService.submit(id, ctx.userId(), ctx.traceId());
            return ResponseEntity.<Object>ok(dto);
        });
    }

    @PostMapping("/expenses/{id}/approve")
    public ResponseEntity<Object> approve(@PathVariable("id") long id,
                                           @RequestBody(required = false) ExpenseDecisionRequest body,
                                           @RequestHeader(value = IDEMPOTENCY_HEADER, required = false) String idempotencyKey) {
        ScopeGuard.require("finance.approve");
        RequestContext ctx = RequestContextHolder.current();
        String note = body != null ? body.note() : null;
        String departmentCode = body != null ? body.departmentCode() : null;
        return idempotencyService.execute(idempotencyKey, () -> {
            var dto = financeService.approve(id, ctx.userId(), note, departmentCode, ctx.traceId(),
                    ctx.teamId(), ctx.isOrgAdmin(), ctx.isTeamAdmin());
            return ResponseEntity.<Object>ok(dto);
        });
    }

    @PostMapping("/expenses/{id}/reject")
    public ResponseEntity<Object> reject(@PathVariable("id") long id,
                                          @RequestBody(required = false) ExpenseDecisionRequest body,
                                          @RequestHeader(value = IDEMPOTENCY_HEADER, required = false) String idempotencyKey) {
        ScopeGuard.require("finance.approve");
        RequestContext ctx = RequestContextHolder.current();
        String note = body != null ? body.note() : null;
        return idempotencyService.execute(idempotencyKey, () -> {
            var dto = financeService.reject(id, ctx.userId(), note, ctx.traceId(),
                    ctx.teamId(), ctx.isOrgAdmin(), ctx.isTeamAdmin());
            return ResponseEntity.<Object>ok(dto);
        });
    }

    @GetMapping("/expenses/{id}")
    public Object getStatus(@PathVariable("id") long id) {
        ScopeGuard.require("finance.read");
        RequestContext ctx = RequestContextHolder.current();
        return financeService.getStatus(id, ctx.userId(), ctx.teamId(), ctx.isOrgAdmin(), ctx.isTeamAdmin());
    }

    /** "我的报销"——部门工作台的列表视图。 */
    @GetMapping("/expenses/mine")
    public List<ExpenseClaimDto> myRequests() {
        ScopeGuard.require("finance.read");
        RequestContext ctx = RequestContextHolder.current();
        return financeService.listMine(ctx.userId());
    }

    /** "待我审批"——部门负责人/企业管理员查看某个部门的待审批报销单。teamId 是显式
     * 查询参数，不是从 ctx.teamId() 直接取——FastAPI 那边已经决定了"当前在看哪个部门"，
     * 这里的 TeamAccessGuard 检查（在 FinanceService.listTeamPending 内部）是第二道
     * 纵深防御，跟 LeaveController/ProcurementController.teamPending 是同一种模式。 */
    @GetMapping("/expenses/team-pending")
    public List<ExpenseClaimDto> teamPending(@RequestParam long teamId) {
        ScopeGuard.require("finance.read");
        RequestContext ctx = RequestContextHolder.current();
        return financeService.listTeamPending(teamId, ctx.teamId(), ctx.isOrgAdmin(), ctx.isTeamAdmin());
    }

    private long requireTeamId(RequestContext ctx) {
        if (ctx.teamId() == null) {
            throw new org.springframework.web.server.ResponseStatusException(
                    org.springframework.http.HttpStatus.BAD_REQUEST, "财务报销操作需要部门上下文（team_id）");
        }
        return ctx.teamId();
    }
}
