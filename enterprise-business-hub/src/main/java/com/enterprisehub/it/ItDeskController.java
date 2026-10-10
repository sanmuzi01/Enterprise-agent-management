package com.enterprisehub.it;

import com.enterprisehub.idempotency.IdempotencyService;
import com.enterprisehub.it.dto.ItDtos.*;
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
 * IT 服务台接口。scope（it.desk.read/write）只由 FastAPI 在确认调用者是 IT 部门成员后才签发；
 * scopeTeamIds 是同一企业的全部部门，写在已签名的路径里，范围外的工单/设备一律按不存在处理。
 */
@RestController
@RequestMapping("/it/desk")
public class ItDeskController {
    private static final String KEY = "Idempotency-Key";

    private final ItTicketService tickets;
    private final ItDeviceService devices;
    private final IdempotencyService idempotency;

    public ItDeskController(ItTicketService tickets, ItDeviceService devices, IdempotencyService idempotency) {
        this.tickets = tickets;
        this.devices = devices;
        this.idempotency = idempotency;
    }

    // ---------------- 工单 ----------------

    @GetMapping("/tickets")
    public List<TicketSummary> list(@RequestParam List<Long> scopeTeamIds, @RequestParam(required = false) String status,
                                    @RequestParam(required = false) String assignee,
                                    @RequestParam(defaultValue = "false") boolean overdue,
                                    @RequestParam(defaultValue = "100") int limit) {
        ScopeGuard.require("it.desk.read");
        return tickets.deskList(scope(scopeTeamIds), status, assignee, overdue, RequestContextHolder.current().userId(), limit);
    }

    @GetMapping("/tickets/{id}")
    public TicketDto get(@PathVariable("id") long id, @RequestParam List<Long> scopeTeamIds) {
        ScopeGuard.require("it.desk.read");
        return tickets.deskGet(id, scope(scopeTeamIds));
    }

    @GetMapping("/summary")
    public Map<String, Object> summary(@RequestParam List<Long> scopeTeamIds, @RequestParam(defaultValue = "30") int days) {
        ScopeGuard.require("it.desk.read");
        return tickets.deskSummary(scope(scopeTeamIds), days);
    }

    @PostMapping("/tickets/{id}/assign")
    public ResponseEntity<Object> assign(@PathVariable("id") long id, @RequestParam List<Long> scopeTeamIds,
                                         @RequestBody(required = false) Assign body, @RequestHeader(value = KEY, required = false) String key) {
        ScopeGuard.require("it.desk.write");
        RequestContext ctx = RequestContextHolder.current();
        List<Long> scope = scope(scopeTeamIds);
        Long assignee = body != null ? body.assigneeUserId() : null;
        return idempotency.execute(key, () -> ResponseEntity.<Object>ok(tickets.assign(id, assignee, ctx.userId(), scope, ctx.traceId())));
    }

    @PostMapping("/tickets/{id}/status")
    public ResponseEntity<Object> status(@PathVariable("id") long id, @RequestParam List<Long> scopeTeamIds,
                                         @Valid @RequestBody ChangeStatus body, @RequestHeader(value = KEY, required = false) String key) {
        ScopeGuard.require("it.desk.write");
        RequestContext ctx = RequestContextHolder.current();
        List<Long> scope = scope(scopeTeamIds);
        return idempotency.execute(key, () -> ResponseEntity.<Object>ok(
                tickets.changeStatus(id, body.status(), body.note(), ctx.userId(), scope, ctx.traceId())));
    }

    @PostMapping("/tickets/{id}/resolve")
    public ResponseEntity<Object> resolve(@PathVariable("id") long id, @RequestParam List<Long> scopeTeamIds,
                                          @Valid @RequestBody Resolve body, @RequestHeader(value = KEY, required = false) String key) {
        ScopeGuard.require("it.desk.write");
        RequestContext ctx = RequestContextHolder.current();
        List<Long> scope = scope(scopeTeamIds);
        return idempotency.execute(key, () -> ResponseEntity.<Object>ok(
                tickets.resolve(id, body.resolution(), ctx.userId(), scope, ctx.traceId())));
    }

    @PostMapping("/tickets/{id}/comments")
    public ResponseEntity<Object> comment(@PathVariable("id") long id, @RequestParam List<Long> scopeTeamIds,
                                          @Valid @RequestBody Comment body, @RequestHeader(value = KEY, required = false) String key) {
        ScopeGuard.require("it.desk.write");
        RequestContext ctx = RequestContextHolder.current();
        List<Long> scope = scope(scopeTeamIds);
        return idempotency.execute(key, () -> ResponseEntity.<Object>ok(
                tickets.deskComment(id, body.body(), body.internal(), ctx.userId(), scope, ctx.traceId())));
    }

    @PostMapping("/tickets/{id}/reclassify")
    public ResponseEntity<Object> reclassify(@PathVariable("id") long id, @RequestParam List<Long> scopeTeamIds,
                                             @Valid @RequestBody Reclassify body, @RequestHeader(value = KEY, required = false) String key) {
        ScopeGuard.require("it.desk.write");
        RequestContext ctx = RequestContextHolder.current();
        List<Long> scope = scope(scopeTeamIds);
        return idempotency.execute(key, () -> ResponseEntity.<Object>ok(
                tickets.reclassify(id, body.category(), body.priority(), body.reason(), ctx.userId(), scope, ctx.traceId())));
    }

    // ---------------- 设备 ----------------

    @GetMapping("/devices")
    public List<DeviceDto> devices(@RequestParam List<Long> scopeTeamIds, @RequestParam(required = false) String status,
                                   @RequestParam(required = false) String type, @RequestParam(defaultValue = "100") int limit) {
        ScopeGuard.require("it.desk.read");
        return devices.list(scope(scopeTeamIds), status, type, limit);
    }

    @GetMapping("/devices/{id}")
    public DeviceDto device(@PathVariable("id") long id, @RequestParam List<Long> scopeTeamIds) {
        ScopeGuard.require("it.desk.read");
        return devices.get(id, scope(scopeTeamIds));
    }

    @PostMapping("/devices")
    public ResponseEntity<Object> createDevice(@RequestParam List<Long> scopeTeamIds, @Valid @RequestBody CreateDevice body,
                                               @RequestHeader(value = KEY, required = false) String key) {
        ScopeGuard.require("it.desk.write");
        RequestContext ctx = RequestContextHolder.current();
        List<Long> scope = scope(scopeTeamIds);
        return idempotency.execute(key, () -> ResponseEntity.<Object>ok(devices.create(body, ctx.userId(), scope, ctx.traceId())));
    }

    @PostMapping("/devices/{id}/assign")
    public ResponseEntity<Object> assignDevice(@PathVariable("id") long id, @RequestParam List<Long> scopeTeamIds,
                                               @Valid @RequestBody AssignDevice body, @RequestHeader(value = KEY, required = false) String key) {
        ScopeGuard.require("it.desk.write");
        RequestContext ctx = RequestContextHolder.current();
        List<Long> scope = scope(scopeTeamIds);
        return idempotency.execute(key, () -> ResponseEntity.<Object>ok(devices.assign(id, body, ctx.userId(), scope, ctx.traceId())));
    }

    @PostMapping("/devices/{id}/return")
    public ResponseEntity<Object> returnDevice(@PathVariable("id") long id, @RequestParam List<Long> scopeTeamIds,
                                               @RequestBody(required = false) DeviceNote body, @RequestHeader(value = KEY, required = false) String key) {
        ScopeGuard.require("it.desk.write");
        RequestContext ctx = RequestContextHolder.current();
        List<Long> scope = scope(scopeTeamIds);
        String note = body != null ? body.note() : null;
        return idempotency.execute(key, () -> ResponseEntity.<Object>ok(devices.giveBack(id, note, ctx.userId(), scope, ctx.traceId())));
    }

    @PostMapping("/devices/{id}/repair")
    public ResponseEntity<Object> repair(@PathVariable("id") long id, @RequestParam List<Long> scopeTeamIds,
                                         @RequestBody(required = false) DeviceNote body, @RequestHeader(value = KEY, required = false) String key) {
        ScopeGuard.require("it.desk.write");
        RequestContext ctx = RequestContextHolder.current();
        List<Long> scope = scope(scopeTeamIds);
        String note = body != null ? body.note() : null;
        return idempotency.execute(key, () -> ResponseEntity.<Object>ok(devices.sendToRepair(id, note, ctx.userId(), scope, ctx.traceId())));
    }

    @PostMapping("/devices/{id}/repair-done")
    public ResponseEntity<Object> repairDone(@PathVariable("id") long id, @RequestParam List<Long> scopeTeamIds,
                                             @RequestBody(required = false) DeviceNote body, @RequestHeader(value = KEY, required = false) String key) {
        ScopeGuard.require("it.desk.write");
        RequestContext ctx = RequestContextHolder.current();
        List<Long> scope = scope(scopeTeamIds);
        String note = body != null ? body.note() : null;
        return idempotency.execute(key, () -> ResponseEntity.<Object>ok(devices.repairDone(id, note, ctx.userId(), scope, ctx.traceId())));
    }

    @PostMapping("/devices/{id}/retire")
    public ResponseEntity<Object> retire(@PathVariable("id") long id, @RequestParam List<Long> scopeTeamIds,
                                         @RequestBody(required = false) DeviceNote body, @RequestHeader(value = KEY, required = false) String key) {
        ScopeGuard.require("it.desk.write");
        RequestContext ctx = RequestContextHolder.current();
        List<Long> scope = scope(scopeTeamIds);
        String note = body != null ? body.note() : null;
        return idempotency.execute(key, () -> ResponseEntity.<Object>ok(devices.retire(id, note, ctx.userId(), scope, ctx.traceId())));
    }

    private List<Long> scope(List<Long> scopeTeamIds) {
        if (scopeTeamIds == null || scopeTeamIds.isEmpty()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "缺少 scopeTeamIds");
        }
        return scopeTeamIds;
    }
}
