package com.enterprisehub.responsibility;

import com.enterprisehub.idempotency.IdempotencyService;
import com.enterprisehub.responsibility.RespDtos.*;
import com.enterprisehub.security.RequestContext;
import com.enterprisehub.security.RequestContextHolder;
import com.enterprisehub.security.ScopeGuard;
import jakarta.validation.Valid;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.List;
import java.util.Map;

/**
 * 责任协同接口。每个请求都带 scopeTeamIds / memberTeamIds / headTeamIds（见 {@link RespActor}），由 FastAPI 计算、
 * 写在已签名的路径里；scope responsibility.read / responsibility.write 只在 FastAPI 确认企业与部门成员身份有效后签发。
 */
@RestController
@RequestMapping("/responsibility")
public class ResponsibilityController {
    private static final String KEY = "Idempotency-Key";

    private final ResponsibilityService service;
    private final RespStatsService stats;
    private final IdempotencyService idempotency;

    public ResponsibilityController(ResponsibilityService service, RespStatsService stats, IdempotencyService idempotency) {
        this.service = service;
        this.stats = stats;
        this.idempotency = idempotency;
    }

    private static RespActor actor(List<Long> scope, List<Long> members, List<Long> heads) {
        return RespActor.of(RequestContextHolder.current().userId(), scope, members, heads);
    }

    // ---------------------------------------------------------------- 查询

    @GetMapping("/plans")
    public List<PlanSummary> plans(@RequestParam List<Long> scopeTeamIds, @RequestParam(required = false) List<Long> memberTeamIds,
                                   @RequestParam(required = false) List<Long> headTeamIds, @RequestParam(required = false) String status,
                                   @RequestParam(required = false) Long teamId, @RequestParam(defaultValue = "100") int limit) {
        ScopeGuard.require("responsibility.read");
        return service.listPlans(actor(scopeTeamIds, memberTeamIds, headTeamIds), status, teamId, limit);
    }

    @GetMapping("/plans/{id}")
    public PlanDto plan(@PathVariable("id") long id, @RequestParam List<Long> scopeTeamIds, @RequestParam(required = false) List<Long> memberTeamIds,
                        @RequestParam(required = false) List<Long> headTeamIds) {
        ScopeGuard.require("responsibility.read");
        return service.getPlan(actor(scopeTeamIds, memberTeamIds, headTeamIds), id);
    }

    @GetMapping("/tasks")
    public List<TaskDto> tasks(@RequestParam List<Long> scopeTeamIds, @RequestParam(required = false) List<Long> memberTeamIds,
                               @RequestParam(required = false) List<Long> headTeamIds, @RequestParam(defaultValue = "mine") String view,
                               @RequestParam(required = false) String status, @RequestParam(required = false) Long teamId,
                               @RequestParam(defaultValue = "100") int limit) {
        ScopeGuard.require("responsibility.read");
        return service.listTasks(actor(scopeTeamIds, memberTeamIds, headTeamIds), view, status, teamId, limit);
    }

    @GetMapping("/tasks/{id}")
    public TaskDetail task(@PathVariable("id") long id, @RequestParam List<Long> scopeTeamIds, @RequestParam(required = false) List<Long> memberTeamIds,
                           @RequestParam(required = false) List<Long> headTeamIds) {
        ScopeGuard.require("responsibility.read");
        return service.getTask(actor(scopeTeamIds, memberTeamIds, headTeamIds), id);
    }

    @GetMapping("/mine")
    public Map<String, Object> mine(@RequestParam List<Long> scopeTeamIds, @RequestParam(required = false) List<Long> memberTeamIds,
                                    @RequestParam(required = false) List<Long> headTeamIds, @RequestParam(required = false) Long teamId) {
        ScopeGuard.require("responsibility.read");
        return stats.mine(actor(scopeTeamIds, memberTeamIds, headTeamIds), teamId);
    }

    @GetMapping("/summary")
    public Map<String, Object> summary(@RequestParam List<Long> scopeTeamIds, @RequestParam(required = false) List<Long> memberTeamIds,
                                       @RequestParam(required = false) List<Long> headTeamIds, @RequestParam long teamId) {
        ScopeGuard.require("responsibility.read");
        return stats.summary(actor(scopeTeamIds, memberTeamIds, headTeamIds), teamId);
    }

    // ---------------------------------------------------------------- 计划（草稿 → 发布）

    @PostMapping("/plans")
    public ResponseEntity<Object> create(@RequestParam List<Long> scopeTeamIds, @RequestParam(required = false) List<Long> memberTeamIds,
                                         @RequestParam(required = false) List<Long> headTeamIds, @Valid @RequestBody CreatePlan body,
                                         @RequestHeader(value = KEY, required = false) String key) {
        ScopeGuard.require("responsibility.write");
        RequestContext ctx = RequestContextHolder.current();
        RespActor a = actor(scopeTeamIds, memberTeamIds, headTeamIds);
        return idempotency.execute(key, () -> ResponseEntity.<Object>ok(service.createPlan(a, body, ctx.traceId())));
    }

    @PostMapping("/plans/{id}/edit")
    public ResponseEntity<Object> editPlan(@PathVariable("id") long id, @RequestParam List<Long> scopeTeamIds,
                                           @RequestParam(required = false) List<Long> memberTeamIds, @RequestParam(required = false) List<Long> headTeamIds,
                                           @Valid @RequestBody EditPlan body, @RequestHeader(value = KEY, required = false) String key) {
        ScopeGuard.require("responsibility.write");
        RespActor a = actor(scopeTeamIds, memberTeamIds, headTeamIds);
        return idempotency.execute(key, () -> ResponseEntity.<Object>ok(service.editPlan(a, id, body)));
    }

    @PostMapping("/plans/{id}/tasks")
    public ResponseEntity<Object> addTask(@PathVariable("id") long id, @RequestParam List<Long> scopeTeamIds,
                                          @RequestParam(required = false) List<Long> memberTeamIds, @RequestParam(required = false) List<Long> headTeamIds,
                                          @Valid @RequestBody AddTask body, @RequestHeader(value = KEY, required = false) String key) {
        ScopeGuard.require("responsibility.write");
        RespActor a = actor(scopeTeamIds, memberTeamIds, headTeamIds);
        return idempotency.execute(key, () -> ResponseEntity.<Object>ok(service.addTask(a, id, body)));
    }

    @PostMapping("/plans/{id}/publish")
    public ResponseEntity<Object> publish(@PathVariable("id") long id, @RequestParam List<Long> scopeTeamIds,
                                          @RequestParam(required = false) List<Long> memberTeamIds, @RequestParam(required = false) List<Long> headTeamIds,
                                          @Valid @RequestBody Publish body, @RequestHeader(value = KEY, required = false) String key) {
        ScopeGuard.require("responsibility.write");
        RequestContext ctx = RequestContextHolder.current();
        RespActor a = actor(scopeTeamIds, memberTeamIds, headTeamIds);
        return idempotency.execute(key, () -> ResponseEntity.<Object>ok(service.publish(a, id, body, ctx.traceId())));
    }

    @PostMapping("/plans/{id}/cancel")
    public ResponseEntity<Object> cancelPlan(@PathVariable("id") long id, @RequestParam List<Long> scopeTeamIds,
                                             @RequestParam(required = false) List<Long> memberTeamIds, @RequestParam(required = false) List<Long> headTeamIds,
                                             @RequestBody(required = false) TaskAction body, @RequestHeader(value = KEY, required = false) String key) {
        ScopeGuard.require("responsibility.write");
        RequestContext ctx = RequestContextHolder.current();
        RespActor a = actor(scopeTeamIds, memberTeamIds, headTeamIds);
        String reason = body == null ? null : (body.reason() != null ? body.reason() : body.note());
        return idempotency.execute(key, () -> ResponseEntity.<Object>ok(service.cancelPlan(a, id, reason, ctx.traceId())));
    }

    // ---------------------------------------------------------------- 责任

    @PostMapping("/tasks/{id}/edit")
    public ResponseEntity<Object> editTask(@PathVariable("id") long id, @RequestParam List<Long> scopeTeamIds,
                                           @RequestParam(required = false) List<Long> memberTeamIds, @RequestParam(required = false) List<Long> headTeamIds,
                                           @Valid @RequestBody EditTask body, @RequestHeader(value = KEY, required = false) String key) {
        ScopeGuard.require("responsibility.write");
        RespActor a = actor(scopeTeamIds, memberTeamIds, headTeamIds);
        return idempotency.execute(key, () -> ResponseEntity.<Object>ok(service.editTask(a, id, body)));
    }

    @PostMapping("/tasks/{id}/remove")
    public ResponseEntity<Object> removeTask(@PathVariable("id") long id, @RequestParam List<Long> scopeTeamIds,
                                             @RequestParam(required = false) List<Long> memberTeamIds, @RequestParam(required = false) List<Long> headTeamIds,
                                             @RequestHeader(value = KEY, required = false) String key) {
        ScopeGuard.require("responsibility.write");
        RespActor a = actor(scopeTeamIds, memberTeamIds, headTeamIds);
        return idempotency.execute(key, () -> ResponseEntity.<Object>ok(service.removeTask(a, id)));
    }

    @PostMapping("/tasks/{id}/{action}")
    public ResponseEntity<Object> act(@PathVariable("id") long id, @PathVariable("action") String action, @RequestParam List<Long> scopeTeamIds,
                                      @RequestParam(required = false) List<Long> memberTeamIds, @RequestParam(required = false) List<Long> headTeamIds,
                                      @Valid @RequestBody(required = false) TaskAction body, @RequestHeader(value = KEY, required = false) String key) {
        ScopeGuard.require("responsibility.write");
        RequestContext ctx = RequestContextHolder.current();
        RespActor a = actor(scopeTeamIds, memberTeamIds, headTeamIds);
        return idempotency.execute(key, () -> ResponseEntity.<Object>ok(service.act(a, id, action, body, ctx.traceId())));
    }
}
