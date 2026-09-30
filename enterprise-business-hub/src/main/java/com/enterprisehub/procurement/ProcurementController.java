package com.enterprisehub.procurement;

import com.enterprisehub.idempotency.IdempotencyService;
import com.enterprisehub.procurement.dto.CreatePurchaseDraftRequest;
import com.enterprisehub.procurement.dto.PurchaseDecisionRequest;
import com.enterprisehub.procurement.dto.PurchaseRequestDto;
import com.enterprisehub.security.RequestContext;
import com.enterprisehub.security.RequestContextHolder;
import com.enterprisehub.security.ScopeGuard;
import jakarta.validation.Valid;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.time.Year;
import java.util.List;

/** 库存与采购闭环的 REST 接口，供 FastAPI 侧的采购 Agent 工具调用
 * （service/tools/procurement.py，跟 oa_leave.py 是同一种薄工具层）。 */
@RestController
@RequestMapping("/procurement")
public class ProcurementController {
    public static final String IDEMPOTENCY_HEADER = "Idempotency-Key";

    private final ProcurementService procurementService;
    private final IdempotencyService idempotencyService;

    public ProcurementController(ProcurementService procurementService, IdempotencyService idempotencyService) {
        this.procurementService = procurementService;
        this.idempotencyService = idempotencyService;
    }

    @GetMapping("/products/{sku}")
    public Object getProduct(@PathVariable String sku) {
        ScopeGuard.require("procurement.read");
        return procurementService.getProduct(sku);
    }

    @GetMapping("/budget")
    public Object getBudget(@RequestParam(required = false) Integer year) {
        ScopeGuard.require("procurement.read");
        RequestContext ctx = RequestContextHolder.current();
        long teamId = requireTeamId(ctx);
        int y = year != null ? year : Year.now().getValue();
        return procurementService.getBudget(teamId, y);
    }

    @PostMapping("/requests")
    public ResponseEntity<Object> createDraft(@Valid @RequestBody CreatePurchaseDraftRequest body,
                                               @RequestHeader(value = IDEMPOTENCY_HEADER, required = false) String idempotencyKey) {
        ScopeGuard.require("procurement.write");
        RequestContext ctx = RequestContextHolder.current();
        long teamId = requireTeamId(ctx);
        return idempotencyService.execute(idempotencyKey, () -> {
            var dto = procurementService.createDraft(ctx.userId(), teamId, body, ctx.traceId());
            return ResponseEntity.<Object>ok(dto);
        });
    }

    @PostMapping("/requests/{id}/submit")
    public ResponseEntity<Object> submit(@PathVariable("id") long id,
                                          @RequestHeader(value = IDEMPOTENCY_HEADER, required = false) String idempotencyKey) {
        ScopeGuard.require("procurement.write");
        RequestContext ctx = RequestContextHolder.current();
        return idempotencyService.execute(idempotencyKey, () -> {
            var dto = procurementService.submit(id, ctx.userId(), ctx.traceId());
            return ResponseEntity.<Object>ok(dto);
        });
    }

    @PostMapping("/requests/{id}/approve")
    public ResponseEntity<Object> approve(@PathVariable("id") long id,
                                           @RequestBody(required = false) PurchaseDecisionRequest body,
                                           @RequestHeader(value = IDEMPOTENCY_HEADER, required = false) String idempotencyKey) {
        ScopeGuard.require("procurement.approve");
        RequestContext ctx = RequestContextHolder.current();
        String note = body != null ? body.note() : null;
        return idempotencyService.execute(idempotencyKey, () -> {
            var dto = procurementService.approve(id, ctx.userId(), note, ctx.traceId(),
                    ctx.teamId(), ctx.isOrgAdmin(), ctx.isTeamAdmin());
            return ResponseEntity.<Object>ok(dto);
        });
    }

    @PostMapping("/requests/{id}/reject")
    public ResponseEntity<Object> reject(@PathVariable("id") long id,
                                          @RequestBody(required = false) PurchaseDecisionRequest body,
                                          @RequestHeader(value = IDEMPOTENCY_HEADER, required = false) String idempotencyKey) {
        ScopeGuard.require("procurement.approve");
        RequestContext ctx = RequestContextHolder.current();
        String note = body != null ? body.note() : null;
        return idempotencyService.execute(idempotencyKey, () -> {
            var dto = procurementService.reject(id, ctx.userId(), note, ctx.traceId(),
                    ctx.teamId(), ctx.isOrgAdmin(), ctx.isTeamAdmin());
            return ResponseEntity.<Object>ok(dto);
        });
    }

    @GetMapping("/requests/{id}")
    public Object getStatus(@PathVariable("id") long id) {
        ScopeGuard.require("procurement.read");
        RequestContext ctx = RequestContextHolder.current();
        return procurementService.getStatus(id, ctx.userId(), ctx.teamId(), ctx.isOrgAdmin(), ctx.isTeamAdmin());
    }

    /** "我的采购申请"——部门工作台的列表视图，里程碑2新增。 */
    @GetMapping("/requests/mine")
    public List<PurchaseRequestDto> myRequests() {
        ScopeGuard.require("procurement.read");
        RequestContext ctx = RequestContextHolder.current();
        return procurementService.listMine(ctx.userId());
    }

    /** "待我审批"——部门负责人/企业管理员查看某个部门的待审批采购申请。teamId 是显式
     * 查询参数，不是从 ctx.teamId() 直接取——FastAPI 那边已经决定了"当前在看哪个部门"，
     * 这里的 TeamAccessGuard 检查（在 ProcurementService.listTeamPending 内部）是第二道
     * 纵深防御，跟 LeaveController.teamPending 是同一种模式。 */
    @GetMapping("/requests/team-pending")
    public List<PurchaseRequestDto> teamPending(@RequestParam long teamId) {
        ScopeGuard.require("procurement.read");
        RequestContext ctx = RequestContextHolder.current();
        return procurementService.listTeamPending(teamId, ctx.teamId(), ctx.isOrgAdmin(), ctx.isTeamAdmin());
    }

    private long requireTeamId(RequestContext ctx) {
        if (ctx.teamId() == null) {
            throw new org.springframework.web.server.ResponseStatusException(
                    org.springframework.http.HttpStatus.BAD_REQUEST, "采购操作需要部门上下文（team_id）");
        }
        return ctx.teamId();
    }
}
