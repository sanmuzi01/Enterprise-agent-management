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

/** 员工侧工单接口（scope: it.ticket.read/write/approve）：提交、我的工单、补充、撤销、确认/重开、部门负责人审批。 */
@RestController
@RequestMapping("/it")
public class ItTicketController {
    private static final String KEY = "Idempotency-Key";

    private final ItTicketService service;
    private final ItDeviceService deviceService;
    private final IdempotencyService idempotency;

    public ItTicketController(ItTicketService service, ItDeviceService deviceService, IdempotencyService idempotency) {
        this.service = service;
        this.deviceService = deviceService;
        this.idempotency = idempotency;
    }

    @PostMapping("/tickets")
    public ResponseEntity<Object> create(@Valid @RequestBody CreateTicket body, @RequestHeader(value = KEY, required = false) String key) {
        ScopeGuard.require("it.ticket.write");
        RequestContext ctx = RequestContextHolder.current();
        if (ctx.teamId() == null) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "提交工单需要部门上下文（team_id）");
        }
        return idempotency.execute(key, () -> ResponseEntity.<Object>ok(service.create(ctx.userId(), ctx.teamId(), body, ctx.traceId())));
    }

    @GetMapping("/tickets/mine")
    public List<TicketSummary> mine() {
        ScopeGuard.require("it.ticket.read");
        return service.listMine(RequestContextHolder.current().userId());
    }

    @GetMapping("/tickets/team-pending")
    public List<TicketSummary> teamPending(@RequestParam long teamId) {
        ScopeGuard.require("it.ticket.read");
        RequestContext ctx = RequestContextHolder.current();
        return service.listTeamPending(teamId, ctx.teamId(), ctx.isOrgAdmin(), ctx.isTeamAdmin());
    }

    @GetMapping("/tickets/{id}")
    public TicketDto get(@PathVariable("id") long id) {
        ScopeGuard.require("it.ticket.read");
        RequestContext ctx = RequestContextHolder.current();
        return service.getVisible(id, ctx.userId(), ctx.teamId(), ctx.isOrgAdmin(), ctx.isTeamAdmin());
    }

    @PostMapping("/tickets/{id}/cancel")
    public ResponseEntity<Object> cancel(@PathVariable("id") long id, @RequestHeader(value = KEY, required = false) String key) {
        ScopeGuard.require("it.ticket.write");
        RequestContext ctx = RequestContextHolder.current();
        return idempotency.execute(key, () -> ResponseEntity.<Object>ok(service.cancel(id, ctx.userId(), ctx.traceId())));
    }

    @PostMapping("/tickets/{id}/comments")
    public ResponseEntity<Object> comment(@PathVariable("id") long id, @Valid @RequestBody Comment body,
                                          @RequestHeader(value = KEY, required = false) String key) {
        ScopeGuard.require("it.ticket.write");
        RequestContext ctx = RequestContextHolder.current();
        return idempotency.execute(key, () -> ResponseEntity.<Object>ok(service.comment(id, ctx.userId(), body.body(), ctx.traceId())));
    }

    @PostMapping("/tickets/{id}/confirm")
    public ResponseEntity<Object> confirm(@PathVariable("id") long id, @RequestHeader(value = KEY, required = false) String key) {
        ScopeGuard.require("it.ticket.write");
        RequestContext ctx = RequestContextHolder.current();
        return idempotency.execute(key, () -> ResponseEntity.<Object>ok(service.confirm(id, ctx.userId(), ctx.traceId())));
    }

    @PostMapping("/tickets/{id}/reopen")
    public ResponseEntity<Object> reopen(@PathVariable("id") long id, @Valid @RequestBody Reason body,
                                         @RequestHeader(value = KEY, required = false) String key) {
        ScopeGuard.require("it.ticket.write");
        RequestContext ctx = RequestContextHolder.current();
        return idempotency.execute(key, () -> ResponseEntity.<Object>ok(service.reopen(id, ctx.userId(), body.reason(), ctx.traceId())));
    }

    @PostMapping("/tickets/{id}/approve")
    public ResponseEntity<Object> approve(@PathVariable("id") long id, @RequestBody(required = false) Note body,
                                          @RequestHeader(value = KEY, required = false) String key) {
        ScopeGuard.require("it.ticket.approve");
        RequestContext ctx = RequestContextHolder.current();
        String note = body != null ? body.note() : null;
        return idempotency.execute(key, () -> ResponseEntity.<Object>ok(service.approve(id, ctx.userId(), note, ctx.traceId(),
                ctx.teamId(), ctx.isOrgAdmin(), ctx.isTeamAdmin())));
    }

    @PostMapping("/tickets/{id}/reject")
    public ResponseEntity<Object> reject(@PathVariable("id") long id, @RequestBody(required = false) Note body,
                                         @RequestHeader(value = KEY, required = false) String key) {
        ScopeGuard.require("it.ticket.approve");
        RequestContext ctx = RequestContextHolder.current();
        String note = body != null ? body.note() : null;
        return idempotency.execute(key, () -> ResponseEntity.<Object>ok(service.reject(id, ctx.userId(), note, ctx.traceId(),
                ctx.teamId(), ctx.isOrgAdmin(), ctx.isTeamAdmin())));
    }

    @GetMapping("/kb/suggest")
    public List<KbSuggestion> suggest(@RequestParam String text) {
        ScopeGuard.require("it.ticket.read");
        return service.suggest(text);
    }

    @GetMapping("/classify")
    public Classification classify(@RequestParam String text) {
        ScopeGuard.require("it.ticket.read");
        return service.classify(text);
    }

    @GetMapping("/devices/mine")
    public List<DeviceDto> myDevices() {
        ScopeGuard.require("it.ticket.read");
        return deviceService.mine(RequestContextHolder.current().userId());
    }
}
