package com.enterprisehub.oa;

import com.enterprisehub.idempotency.IdempotencyService;
import com.enterprisehub.oa.dto.CreateLeaveDraftRequest;
import com.enterprisehub.oa.dto.LeaveBalanceDto;
import com.enterprisehub.oa.dto.LeaveDecisionRequest;
import com.enterprisehub.oa.dto.LeaveRequestDto;
import com.enterprisehub.security.RequestContext;
import com.enterprisehub.security.RequestContextHolder;
import com.enterprisehub.security.ScopeGuard;
import jakarta.validation.Valid;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.time.LocalDate;
import java.time.Year;
import java.util.List;

/**
 * OA 请假闭环的 REST 接口，供 FastAPI 侧的 HR Agent 工具调用
 * （见仓库根目录 service/tools/oa_leave.py，一个工具对应这里一个方法）。
 */
@RestController
@RequestMapping("/oa/leave")
public class LeaveController {
    public static final String IDEMPOTENCY_HEADER = "Idempotency-Key";

    private final LeaveService leaveService;
    private final IdempotencyService idempotencyService;

    public LeaveController(LeaveService leaveService, IdempotencyService idempotencyService) {
        this.leaveService = leaveService;
        this.idempotencyService = idempotencyService;
    }

    @GetMapping("/balance")
    public List<LeaveBalanceDto> getBalance(@RequestParam(required = false) Integer year) {
        ScopeGuard.require("oa.leave.read");
        RequestContext ctx = RequestContextHolder.current();
        int y = year != null ? year : Year.now().getValue();
        return leaveService.getBalances(ctx.userId(), y);
    }

    @PostMapping("/requests")
    public ResponseEntity<Object> createDraft(@Valid @RequestBody CreateLeaveDraftRequest body,
                                               @RequestHeader(value = IDEMPOTENCY_HEADER, required = false) String idempotencyKey) {
        ScopeGuard.require("oa.leave.write");
        RequestContext ctx = RequestContextHolder.current();
        return idempotencyService.execute(idempotencyKey, () -> {
            LocalDate start = body.startDate();
            LocalDate end = body.endDate();
            LeaveRequestDto dto = leaveService.createDraft(ctx.userId(), ctx.teamId(), body.leaveTypeCode(),
                    start, end, body.reason(), ctx.traceId());
            return ResponseEntity.<Object>ok(dto);
        });
    }

    @PostMapping("/requests/{id}/submit")
    public ResponseEntity<Object> submit(@PathVariable("id") long id,
                                          @RequestHeader(value = IDEMPOTENCY_HEADER, required = false) String idempotencyKey) {
        ScopeGuard.require("oa.leave.write");
        RequestContext ctx = RequestContextHolder.current();
        return idempotencyService.execute(idempotencyKey, () -> {
            LeaveRequestDto dto = leaveService.submit(id, ctx.userId(), ctx.traceId());
            return ResponseEntity.<Object>ok(dto);
        });
    }

    @PostMapping("/requests/{id}/approve")
    public ResponseEntity<Object> approve(@PathVariable("id") long id,
                                           @RequestBody(required = false) LeaveDecisionRequest body,
                                           @RequestHeader(value = IDEMPOTENCY_HEADER, required = false) String idempotencyKey) {
        ScopeGuard.require("oa.leave.approve");
        RequestContext ctx = RequestContextHolder.current();
        String note = body != null ? body.note() : null;
        return idempotencyService.execute(idempotencyKey, () -> {
            LeaveRequestDto dto = leaveService.approve(id, ctx.userId(), note, ctx.traceId(),
                    ctx.teamId(), ctx.isOrgAdmin(), ctx.isTeamAdmin());
            return ResponseEntity.<Object>ok(dto);
        });
    }

    @PostMapping("/requests/{id}/reject")
    public ResponseEntity<Object> reject(@PathVariable("id") long id,
                                          @RequestBody(required = false) LeaveDecisionRequest body,
                                          @RequestHeader(value = IDEMPOTENCY_HEADER, required = false) String idempotencyKey) {
        ScopeGuard.require("oa.leave.approve");
        RequestContext ctx = RequestContextHolder.current();
        String note = body != null ? body.note() : null;
        return idempotencyService.execute(idempotencyKey, () -> {
            LeaveRequestDto dto = leaveService.reject(id, ctx.userId(), note, ctx.traceId(),
                    ctx.teamId(), ctx.isOrgAdmin(), ctx.isTeamAdmin());
            return ResponseEntity.<Object>ok(dto);
        });
    }

    @GetMapping("/requests/{id}")
    public LeaveRequestDto getStatus(@PathVariable("id") long id) {
        ScopeGuard.require("oa.leave.read");
        RequestContext ctx = RequestContextHolder.current();
        return leaveService.getStatus(id, ctx.userId(), ctx.teamId(), ctx.isOrgAdmin(), ctx.isTeamAdmin());
    }

    /** "我的请假"——部门工作台的列表视图，第五轮之外的里程碑1新增。 */
    @GetMapping("/requests/mine")
    public List<LeaveRequestDto> myRequests() {
        ScopeGuard.require("oa.leave.read");
        RequestContext ctx = RequestContextHolder.current();
        return leaveService.listMine(ctx.userId());
    }

    /** "待我审批"——部门负责人/企业管理员查看某个部门的待审批请假单。teamId 是显式查询参数，
     * 不是从 ctx.teamId() 直接取：FastAPI 那边已经决定了"当前在看哪个部门"，这里的
     * TeamAccessGuard 检查（在 LeaveService.listTeamPending 内部）是第二道纵深防御。 */
    @GetMapping("/requests/team-pending")
    public List<LeaveRequestDto> teamPending(@RequestParam long teamId) {
        ScopeGuard.require("oa.leave.read");
        RequestContext ctx = RequestContextHolder.current();
        return leaveService.listTeamPending(teamId, ctx.teamId(), ctx.isOrgAdmin(), ctx.isTeamAdmin());
    }

    /** 已批准的请假（只读）：scopeTeamIds 由 FastAPI 计算并写进已签名的路径，只返回这些部门的请假。 */
    @GetMapping("/requests/approved")
    public List<LeaveRequestDto> approved(@RequestParam List<Long> scopeTeamIds, @RequestParam LocalDate from, @RequestParam LocalDate to) {
        ScopeGuard.require("oa.leave.read");
        return leaveService.listApproved(scopeTeamIds, from, to);
    }
}
