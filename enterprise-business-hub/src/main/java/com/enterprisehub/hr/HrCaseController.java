package com.enterprisehub.hr;

import com.enterprisehub.hr.HrDtos.*;
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
 * 人事事项接口。每个请求都带 scopeTeamIds / roles / headTeamIds（见 {@link HrActor}），由 FastAPI 计算、写在已签名的路径里；
 * scope hr.case.read / hr.case.write 只在 FastAPI 确认调用者企业成员身份有效后签发。
 */
@RestController
@RequestMapping("/hr/cases")
public class HrCaseController {
    private static final String KEY = "Idempotency-Key";

    private final HrCaseService service;
    private final IdempotencyService idempotency;

    public HrCaseController(HrCaseService service, IdempotencyService idempotency) {
        this.service = service;
        this.idempotency = idempotency;
    }

    private static HrActor actor(List<Long> scopeTeamIds, List<String> roles, List<Long> headTeamIds) {
        return HrActor.of(RequestContextHolder.current().userId(), scopeTeamIds, roles, headTeamIds);
    }

    @GetMapping
    public List<CaseSummary> list(@RequestParam List<Long> scopeTeamIds, @RequestParam(required = false) List<String> roles,
                                  @RequestParam(required = false) List<Long> headTeamIds, @RequestParam(defaultValue = "hr") String view,
                                  @RequestParam(required = false) String status, @RequestParam(required = false) String type,
                                  @RequestParam(defaultValue = "100") int limit) {
        ScopeGuard.require("hr.case.read");
        return service.list(actor(scopeTeamIds, roles, headTeamIds), view, status, type, limit);
    }

    @GetMapping("/my-tasks")
    public List<MyTask> myTasks(@RequestParam List<Long> scopeTeamIds, @RequestParam(required = false) List<String> roles,
                                @RequestParam(required = false) List<Long> headTeamIds) {
        ScopeGuard.require("hr.case.read");
        return service.myTasks(actor(scopeTeamIds, roles, headTeamIds));
    }

    @GetMapping("/summary")
    public Map<String, Object> summary(@RequestParam List<Long> scopeTeamIds, @RequestParam(required = false) List<String> roles,
                                       @RequestParam(required = false) List<Long> headTeamIds) {
        ScopeGuard.require("hr.case.read");
        return service.summary(actor(scopeTeamIds, roles, headTeamIds));
    }

    @GetMapping("/{id}")
    public CaseDto get(@PathVariable("id") long id, @RequestParam List<Long> scopeTeamIds,
                       @RequestParam(required = false) List<String> roles, @RequestParam(required = false) List<Long> headTeamIds) {
        ScopeGuard.require("hr.case.read");
        return service.get(actor(scopeTeamIds, roles, headTeamIds), id);
    }

    @PostMapping("/precheck")
    public Map<String, Object> precheck(@RequestParam List<Long> scopeTeamIds, @RequestParam(required = false) List<String> roles,
                                        @RequestParam(required = false) List<Long> headTeamIds, @Valid @RequestBody CreateCase body) {
        ScopeGuard.require("hr.case.read");
        return service.precheck(actor(scopeTeamIds, roles, headTeamIds), body);
    }

    @PostMapping
    public ResponseEntity<Object> create(@RequestParam List<Long> scopeTeamIds, @RequestParam(required = false) List<String> roles,
                                         @RequestParam(required = false) List<Long> headTeamIds, @Valid @RequestBody CreateCase body,
                                         @RequestHeader(value = KEY, required = false) String key) {
        ScopeGuard.require("hr.case.write");
        RequestContext ctx = RequestContextHolder.current();
        HrActor a = actor(scopeTeamIds, roles, headTeamIds);
        return idempotency.execute(key, () -> ResponseEntity.<Object>ok(service.create(a, body, ctx.traceId())));
    }

    @PostMapping("/{id}/{action}")
    public ResponseEntity<Object> act(@PathVariable("id") long id, @PathVariable("action") String action,
                                      @RequestParam List<Long> scopeTeamIds, @RequestParam(required = false) List<String> roles,
                                      @RequestParam(required = false) List<Long> headTeamIds, @RequestBody(required = false) Note body,
                                      @RequestHeader(value = KEY, required = false) String key) {
        ScopeGuard.require("hr.case.write");
        RequestContext ctx = RequestContextHolder.current();
        HrActor a = actor(scopeTeamIds, roles, headTeamIds);
        String note = body != null ? body.note() : null;
        if ("recheck".equals(action)) {
            return ResponseEntity.ok(service.recheck(a, id));
        }
        return idempotency.execute(key, () -> ResponseEntity.<Object>ok(switch (action) {
            case "approve" -> service.approve(a, id, note, ctx.traceId());
            case "reject" -> service.reject(a, id, note, ctx.traceId());
            case "cancel" -> service.cancel(a, id, ctx.traceId());
            case "complete" -> service.complete(a, id, ctx.traceId());
            case "effect-applied" -> service.markEffectApplied(a, id, ctx.traceId());
            default -> throw new ResponseStatusException(HttpStatus.NOT_FOUND, "未知的操作");
        }));
    }

    @PostMapping("/{id}/tasks/{taskId}/{result}")
    public ResponseEntity<Object> task(@PathVariable("id") long id, @PathVariable("taskId") long taskId,
                                       @PathVariable("result") String result, @RequestParam List<Long> scopeTeamIds,
                                       @RequestParam(required = false) List<String> roles, @RequestParam(required = false) List<Long> headTeamIds,
                                       @RequestBody(required = false) Note body, @RequestHeader(value = KEY, required = false) String key) {
        ScopeGuard.require("hr.case.write");
        String status = switch (result) {
            case "done" -> "DONE";
            case "skip" -> "SKIPPED";
            default -> throw new ResponseStatusException(HttpStatus.NOT_FOUND, "未知的操作");
        };
        RequestContext ctx = RequestContextHolder.current();
        HrActor a = actor(scopeTeamIds, roles, headTeamIds);
        String note = body != null ? body.note() : null;
        return idempotency.execute(key, () -> ResponseEntity.<Object>ok(service.finishTask(a, id, taskId, status, note, ctx.traceId())));
    }
}
