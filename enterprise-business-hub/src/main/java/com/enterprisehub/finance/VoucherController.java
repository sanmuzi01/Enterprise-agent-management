package com.enterprisehub.finance;

import com.enterprisehub.finance.dto.ExpenseClaimDto;
import com.enterprisehub.finance.dto.VoucherDto;
import com.enterprisehub.finance.dto.VoucherRequests;
import com.enterprisehub.finance.dto.VoucherSummaryDto;
import com.enterprisehub.idempotency.IdempotencyService;
import com.enterprisehub.security.RequestContext;
import com.enterprisehub.security.RequestContextHolder;
import com.enterprisehub.security.ScopeGuard;
import jakarta.validation.Valid;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.server.ResponseStatusException;

import java.util.List;
import java.util.Map;

/**
 * 记账凭证接口。权限分两层：scope（finance.voucher.read / finance.voucher.write）只由 FastAPI 在确认调用者是
 * 财务部门成员后才签发；scopeTeamIds 是 FastAPI 查出的“同一企业的全部部门”，写在已签名的 URL 里，
 * 凭证不在这个范围内一律按不存在处理，所以即使 scope 被误给也看不到别的企业的凭证。
 */
@RestController
@RequestMapping("/finance/vouchers")
public class VoucherController {
    private static final String IDEMPOTENCY_HEADER = FinanceController.IDEMPOTENCY_HEADER;

    private final VoucherService voucherService;
    private final FinanceService financeService;
    private final IdempotencyService idempotencyService;

    public VoucherController(VoucherService voucherService, FinanceService financeService,
                             IdempotencyService idempotencyService) {
        this.voucherService = voucherService;
        this.financeService = financeService;
        this.idempotencyService = idempotencyService;
    }

    @GetMapping
    public List<VoucherSummaryDto> list(@RequestParam List<Long> scopeTeamIds,
                                        @RequestParam(required = false) String status,
                                        @RequestParam(required = false) String period,
                                        @RequestParam(defaultValue = "100") int limit) {
        ScopeGuard.require("finance.voucher.read");
        return voucherService.list(requireScope(scopeTeamIds), status, period, limit);
    }

    @GetMapping("/summary")
    public Map<String, Object> summary(@RequestParam List<Long> scopeTeamIds,
                                       @RequestParam(required = false) String period) {
        ScopeGuard.require("finance.voucher.read");
        return voucherService.monthlySummary(requireScope(scopeTeamIds), period);
    }

    @GetMapping("/unbooked")
    public List<ExpenseClaimDto> unbooked(@RequestParam List<Long> scopeTeamIds) {
        ScopeGuard.require("finance.voucher.read");
        return financeService.listApprovedWithoutVoucher(requireScope(scopeTeamIds));
    }

    @GetMapping("/subjects")
    public List<Map<String, Object>> subjects() {
        ScopeGuard.require("finance.voucher.read");
        return voucherService.subjects();
    }

    @GetMapping("/{id}")
    public VoucherDto get(@PathVariable("id") long id, @RequestParam List<Long> scopeTeamIds) {
        ScopeGuard.require("finance.voucher.read");
        return voucherService.get(id, requireScope(scopeTeamIds));
    }

    @GetMapping("/by-claim/{claimId}")
    public VoucherDto byClaim(@PathVariable("claimId") long claimId, @RequestParam List<Long> scopeTeamIds) {
        ScopeGuard.require("finance.voucher.read");
        return voucherService.getByClaim(claimId, requireScope(scopeTeamIds));
    }

    @PostMapping("/from-claim/{claimId}")
    public ResponseEntity<Object> generate(@PathVariable("claimId") long claimId,
                                           @RequestParam List<Long> scopeTeamIds,
                                           @RequestBody(required = false) VoucherRequests.Generate body,
                                           @RequestHeader(value = IDEMPOTENCY_HEADER, required = false) String key) {
        ScopeGuard.require("finance.voucher.write");
        RequestContext ctx = RequestContextHolder.current();
        List<Long> scope = requireScope(scopeTeamIds);
        String departmentCode = body != null ? body.departmentCode() : null;
        return idempotencyService.execute(key, () -> ResponseEntity.<Object>ok(
                voucherService.generateFromClaim(claimId, departmentCode, ctx.userId(), scope, ctx.traceId())));
    }

    @PostMapping("/{id}/regenerate")
    public ResponseEntity<Object> regenerate(@PathVariable("id") long id, @RequestParam List<Long> scopeTeamIds,
                                             @RequestHeader(value = IDEMPOTENCY_HEADER, required = false) String key) {
        ScopeGuard.require("finance.voucher.write");
        RequestContext ctx = RequestContextHolder.current();
        List<Long> scope = requireScope(scopeTeamIds);
        return idempotencyService.execute(key, () -> ResponseEntity.<Object>ok(
                voucherService.regenerate(id, ctx.userId(), scope, ctx.traceId())));
    }

    @PostMapping("/{id}/entries/{entryId}/subject")
    public ResponseEntity<Object> updateEntry(@PathVariable("id") long id, @PathVariable("entryId") long entryId,
                                              @RequestParam List<Long> scopeTeamIds,
                                              @Valid @RequestBody VoucherRequests.UpdateEntry body,
                                              @RequestHeader(value = IDEMPOTENCY_HEADER, required = false) String key) {
        ScopeGuard.require("finance.voucher.write");
        RequestContext ctx = RequestContextHolder.current();
        List<Long> scope = requireScope(scopeTeamIds);
        return idempotencyService.execute(key, () -> ResponseEntity.<Object>ok(
                voucherService.updateEntrySubject(id, entryId, body.subjectCode(), body.reason(), ctx.userId(), scope,
                        ctx.traceId())));
    }

    @PostMapping("/{id}/date")
    public ResponseEntity<Object> updateVoucher(@PathVariable("id") long id, @RequestParam List<Long> scopeTeamIds,
                                                @Valid @RequestBody VoucherRequests.UpdateVoucher body,
                                                @RequestHeader(value = IDEMPOTENCY_HEADER, required = false) String key) {
        ScopeGuard.require("finance.voucher.write");
        RequestContext ctx = RequestContextHolder.current();
        List<Long> scope = requireScope(scopeTeamIds);
        return idempotencyService.execute(key, () -> ResponseEntity.<Object>ok(
                voucherService.updateVoucherDate(id, body.voucherDate(), ctx.userId(), scope, ctx.traceId())));
    }

    @PostMapping("/{id}/recheck")
    public VoucherDto recheck(@PathVariable("id") long id, @RequestParam List<Long> scopeTeamIds) {
        ScopeGuard.require("finance.voucher.write");
        RequestContext ctx = RequestContextHolder.current();
        return voucherService.recheck(id, ctx.userId(), requireScope(scopeTeamIds));
    }

    @PostMapping("/{id}/confirm")
    public ResponseEntity<Object> confirm(@PathVariable("id") long id, @RequestParam List<Long> scopeTeamIds,
                                          @RequestBody(required = false) VoucherRequests.Confirm body,
                                          @RequestHeader(value = IDEMPOTENCY_HEADER, required = false) String key) {
        ScopeGuard.require("finance.voucher.write");
        RequestContext ctx = RequestContextHolder.current();
        List<Long> scope = requireScope(scopeTeamIds);
        String note = body != null ? body.note() : null;
        boolean acknowledged = body != null && body.acknowledgeWarnings();
        return idempotencyService.execute(key, () -> ResponseEntity.<Object>ok(
                voucherService.confirm(id, ctx.userId(), note, acknowledged, scope, ctx.traceId())));
    }

    @PostMapping("/{id}/void")
    public ResponseEntity<Object> voidVoucher(@PathVariable("id") long id, @RequestParam List<Long> scopeTeamIds,
                                              @Valid @RequestBody VoucherRequests.Void body,
                                              @RequestHeader(value = IDEMPOTENCY_HEADER, required = false) String key) {
        ScopeGuard.require("finance.voucher.write");
        RequestContext ctx = RequestContextHolder.current();
        List<Long> scope = requireScope(scopeTeamIds);
        return idempotencyService.execute(key, () -> ResponseEntity.<Object>ok(
                voucherService.voidVoucher(id, body.reason(), ctx.userId(), scope, ctx.traceId())));
    }

    private List<Long> requireScope(List<Long> scopeTeamIds) {
        if (scopeTeamIds == null || scopeTeamIds.isEmpty()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "缺少 scopeTeamIds");
        }
        return scopeTeamIds;
    }
}
