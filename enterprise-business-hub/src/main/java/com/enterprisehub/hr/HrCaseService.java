package com.enterprisehub.hr;

import com.enterprisehub.audit.AuditService;
import com.enterprisehub.hr.HrCheckService.Check;
import com.enterprisehub.hr.HrDtos.*;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.persistence.criteria.Predicate;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.domain.Sort;
import org.springframework.data.jpa.domain.Specification;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;

import java.time.Duration;
import java.time.LocalDate;
import java.time.format.DateTimeParseException;
import java.util.*;
import java.util.stream.Collectors;

/**
 * 人事入转调离：HR 发起（先跑规则检查，有阻断不能提交）→ 员工所在部门负责人批准 → 生成跨部门办理清单 →
 * 各方办各自的任务 → 必办任务全部完成且办结前检查无阻断 → HR 办结（调岗/离职办结后还有“系统变更待落实”）。
 *
 * 权限由 {@link HrActor} 决定（FastAPI 在已签名路径里给出）：发起/办结/重新检查只有 HR；批准只有该员工所在部门的负责人或企业管理员，
 * 且不能批准自己的、不能批准自己发起的；任务只能由对应办理方完成（HR/IT/财务部门成员、员工的负责人、员工本人）。
 */
@Service
public class HrCaseService {
    private final HrCaseRepository cases;
    private final HrCaseTaskRepository tasks;
    private final HrCheckService checker;
    private final AuditService audit;
    private final ObjectMapper mapper;

    public HrCaseService(HrCaseRepository cases, HrCaseTaskRepository tasks, HrCheckService checker, AuditService audit,
                         ObjectMapper mapper) {
        this.cases = cases;
        this.tasks = tasks;
        this.checker = checker;
        this.audit = audit;
        this.mapper = mapper;
    }

    // ---------------------------------------------------------------- 发起与检查

    @Transactional(readOnly = true)
    public Map<String, Object> precheck(HrActor actor, CreateCase request) {
        requireHr(actor);
        ParsedCreate p = parse(actor, request);
        List<Check> checks = checker.check(p.type, request.employeeUserId(), request.teamId(), request.targetTeamId(), p.date,
                request.employeeIsHead(), null, false);
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("checks", checks);
        result.put("riskLevel", HrCheckService.overall(checks));
        result.put("canSubmit", checks.stream().noneMatch(c -> "BLOCK".equals(c.level())));
        return result;
    }

    @Transactional
    public CaseDto create(HrActor actor, CreateCase request, String traceId) {
        requireHr(actor);
        ParsedCreate p = parse(actor, request);
        if (request.employeeUserId() == actor.userId()) {
            throw badRequest("不能为自己办理人事事项，请由其他 HR 同事发起");
        }
        List<Check> checks = checker.check(p.type, request.employeeUserId(), request.teamId(), request.targetTeamId(), p.date,
                request.employeeIsHead(), null, false);
        List<Check> blocks = checks.stream().filter(c -> "BLOCK".equals(c.level())).toList();
        if (!blocks.isEmpty()) {
            throw badRequest("存在必须先处理的问题：" + blocks.stream().map(Check::message).collect(Collectors.joining("；")));
        }
        HrCase hrCase = new HrCase(p.type, request.employeeUserId(), request.teamId(), request.targetTeamId(),
                blankToNull(request.position()), p.date, blankToNull(request.reason()), actor.userId(), request.employeeIsHead());
        hrCase.setChecks(json(checks));
        cases.save(hrCase);
        audit.record(actor.userId(), "hr.case_created", "hr_case", hrCase.getId(),
                "{\"type\":\"" + p.type + "\",\"employee\":" + request.employeeUserId() + "}", traceId);
        return toDto(hrCase, actor);
    }

    @Transactional
    public CaseDto recheck(HrActor actor, long id) {
        requireHr(actor);
        HrCase c = visible(id, actor);
        if (!isActive(c)) {
            return toDto(c, actor);
        }
        c.setChecks(json(runChecks(c, false)));
        return toDto(c, actor);
    }

    // ---------------------------------------------------------------- 审批

    @Transactional
    public CaseDto approve(HrActor actor, long id, String note, String traceId) {
        HrCase c = lockPending(actor, id);
        c.approve(actor.userId(), blankToNull(note));
        int seq = 1;
        for (HrCaseType.Tpl t : c.getCaseType().tasks()) {
            tasks.save(new HrCaseTask(c.getId(), seq++, t.title(), t.owner(), t.required(), c.getEffectiveDate().plusDays(t.dayOffset())));
        }
        c.setChecks(json(runChecks(c, false)));
        audit.record(actor.userId(), "hr.case_approved", "hr_case", id, note, traceId);
        return toDto(c, actor);
    }

    @Transactional
    public CaseDto reject(HrActor actor, long id, String note, String traceId) {
        HrCase c = lockPending(actor, id);
        c.reject(actor.userId(), blankToNull(note));
        audit.record(actor.userId(), "hr.case_rejected", "hr_case", id, note, traceId);
        return toDto(c, actor);
    }

    @Transactional
    public CaseDto cancel(HrActor actor, long id, String traceId) {
        HrCase c = cases.findForUpdate(id).filter(actor::canView).orElseThrow(() -> notFound("人事事项不存在"));
        if (c.getInitiatorUserId() != actor.userId() && !actor.rolesFor(c).contains("HR")) {
            throw notFound("人事事项不存在");
        }
        if (!isActive(c)) {
            throw badRequest("只有进行中的事项能撤销，当前状态: " + c.getStatus());
        }
        c.cancel();
        audit.record(actor.userId(), "hr.case_cancelled", "hr_case", id, null, traceId);
        return toDto(c, actor);
    }

    // ---------------------------------------------------------------- 办理清单

    @Transactional
    public CaseDto finishTask(HrActor actor, long caseId, long taskId, String status, String note, String traceId) {
        HrCase c = cases.findForUpdate(caseId).filter(actor::canView).orElseThrow(() -> notFound("人事事项不存在"));
        if (!HrCase.IN_PROGRESS.equals(c.getStatus())) {
            throw badRequest("只有办理中的事项能处理任务，当前状态: " + c.getStatus());
        }
        HrCaseTask task = tasks.findById(taskId).filter(t -> t.getCaseId() == caseId).orElseThrow(() -> notFound("任务不存在"));
        if (!"OPEN".equals(task.getStatus())) {
            throw badRequest("任务已处理过了");
        }
        if (!actor.rolesFor(c).contains(task.getOwner())) {
            throw new ResponseStatusException(HttpStatus.FORBIDDEN, "这项任务由「" + ownerLabel(task.getOwner()) + "」办理，你没有办理权限");
        }
        if ("SKIPPED".equals(status)) {
            if (task.isRequired()) {
                throw badRequest("必办任务不能跳过");
            }
            if (note == null || note.trim().length() < 2) {
                throw badRequest("跳过任务需要写明原因");
            }
        }
        task.finish(status, actor.userId(), blankToNull(note));
        tasks.save(task);
        audit.record(actor.userId(), "hr.task_" + status.toLowerCase(), "hr_case", caseId, "{\"task\":" + taskId + "}", traceId);
        return toDto(c, actor);
    }

    @Transactional
    public CaseDto complete(HrActor actor, long id, String traceId) {
        requireHr(actor);
        HrCase c = cases.findForUpdate(id).filter(actor::canView).orElseThrow(() -> notFound("人事事项不存在"));
        if (!HrCase.IN_PROGRESS.equals(c.getStatus())) {
            throw badRequest("只有办理中的事项能办结，当前状态: " + c.getStatus());
        }
        if (!actor.rolesFor(c).contains("HR")) {
            throw notFound("人事事项不存在");
        }
        List<HrCaseTask> open = tasks.findByCaseIdOrderBySeq(id).stream()
                .filter(t -> "OPEN".equals(t.getStatus()) && t.isRequired()).toList();
        if (!open.isEmpty()) {
            throw badRequest("还有 " + open.size() + " 项必办任务没有完成：" + open.stream()
                    .map(t -> t.getTitle() + "（" + ownerLabel(t.getOwner()) + "）").collect(Collectors.joining("、")));
        }
        List<Check> checks = runChecks(c, true);
        c.setChecks(json(checks));
        List<Check> blocks = checks.stream().filter(k -> "BLOCK".equals(k.level())).toList();
        if (!blocks.isEmpty()) {
            throw badRequest("办结前还有必须解决的问题：" + blocks.stream().map(Check::message).collect(Collectors.joining("；")));
        }
        c.complete();
        audit.record(actor.userId(), "hr.case_completed", "hr_case", id, null, traceId);
        return toDto(c, actor);
    }

    @Transactional
    public CaseDto markEffectApplied(HrActor actor, long id, String traceId) {
        // 落实变更（改部门归属、停用账号）由企业管理员在 FastAPI 侧执行（FastAPI 只允许企业管理员调用，企业管理员在这里是全部部门的负责人）；
        // 人事也可以在管理员线下处理后标记。其他人不行。
        HrCase c = cases.findForUpdate(id).filter(actor::canView).orElseThrow(() -> notFound("人事事项不存在"));
        Set<String> roles = actor.rolesFor(c);
        if (!roles.contains("HR") && !roles.contains("MANAGER")) {
            throw new ResponseStatusException(HttpStatus.FORBIDDEN, "只有人事或企业管理员能标记系统变更已落实");
        }
        if (!HrCase.COMPLETED.equals(c.getStatus()) || !c.isEffectPending()) {
            throw badRequest("这个事项没有待落实的系统变更");
        }
        c.effectApplied();
        audit.record(actor.userId(), "hr.effect_applied", "hr_case", id, null, traceId);
        return toDto(c, actor);
    }

    // ---------------------------------------------------------------- 查询

    @Transactional(readOnly = true)
    public CaseDto get(HrActor actor, long id) {
        return toDto(visible(id, actor), actor);
    }

    /** view：hr 全部（需 HR 角色）/ approval 我能批准的待批事项 / mine 我作为员工的事项。 */
    @Transactional(readOnly = true)
    public List<CaseSummary> list(HrActor actor, String view, String status, String type, int limit) {
        String mode = view == null ? "hr" : view;
        HrCaseType parsedType = type == null || type.isBlank() ? null : parseType(type);
        if ("hr".equals(mode)) {
            requireHr(actor);
        }
        Specification<HrCase> spec = (root, query, cb) -> {
            List<Predicate> p = new ArrayList<>();
            switch (mode) {
                case "hr" -> p.add(root.get("teamId").in(actor.scope()));
                case "approval" -> {
                    p.add(root.get("status").in(HrCase.PENDING_APPROVAL));
                    p.add(actor.headTeamIds().isEmpty() ? cb.disjunction() : root.get("teamId").in(actor.headTeamIds()));
                }
                case "mine" -> p.add(cb.equal(root.get("employeeUserId"), actor.userId()));
                default -> throw badRequest("未知的视图: " + view);
            }
            if (status != null && !status.isBlank()) {
                p.add(cb.equal(root.get("status"), status.trim().toUpperCase()));
            }
            if (parsedType != null) {
                p.add(cb.equal(root.get("caseType"), parsedType));
            }
            return cb.and(p.toArray(new Predicate[0]));
        };
        List<HrCase> rows = cases.findAll(spec, PageRequest.of(0, Math.max(1, Math.min(limit, 200)), Sort.by(Sort.Direction.DESC, "id"))).getContent();
        Map<Long, List<HrCaseTask>> byCase = tasks.findByCaseIdIn(rows.stream().map(HrCase::getId).toList()).stream()
                .collect(Collectors.groupingBy(HrCaseTask::getCaseId));
        return rows.stream().map(c -> {
            List<HrCaseTask> list = byCase.getOrDefault(c.getId(), List.of());
            return new CaseSummary(c.getId(), c.getCaseType().name(), c.getCaseType().label(), c.getEmployeeUserId(), c.getTeamId(),
                    c.getTargetTeamId(), c.getEffectiveDate().toString(), c.getStatus(),
                    (int) list.stream().filter(t -> "OPEN".equals(t.getStatus())).count(), list.size(), c.isEffectPending(), c.getCreatedAt());
        }).toList();
    }

    /** 我当前要办的任务：按我在各事项里的身份（办理角色/负责人/员工本人）汇总。 */
    @Transactional(readOnly = true)
    public List<MyTask> myTasks(HrActor actor) {
        Specification<HrCase> spec = (root, query, cb) -> {
            List<Predicate> any = new ArrayList<>();
            if (!actor.deptRoles().isEmpty()) {
                any.add(root.get("teamId").in(actor.scope()));
            }
            if (!actor.headTeamIds().isEmpty()) {
                any.add(root.get("teamId").in(actor.headTeamIds()));
            }
            any.add(cb.equal(root.get("employeeUserId"), actor.userId()));
            return cb.and(cb.equal(root.get("status"), HrCase.IN_PROGRESS), cb.or(any.toArray(new Predicate[0])));
        };
        List<HrCase> rows = cases.findAll(spec, Sort.by(Sort.Direction.ASC, "effectiveDate"));
        Map<Long, HrCase> byId = rows.stream().collect(Collectors.toMap(HrCase::getId, c -> c));
        LocalDate today = LocalDate.now(HrCheckService.BEIJING);
        List<MyTask> result = new ArrayList<>();
        for (HrCaseTask t : tasks.findByCaseIdIn(byId.keySet())) {
            HrCase c = byId.get(t.getCaseId());
            if ("OPEN".equals(t.getStatus()) && actor.rolesFor(c).contains(t.getOwner())) {
                result.add(new MyTask(t.getId(), c.getId(), c.getCaseType().name(), c.getCaseType().label(), c.getEmployeeUserId(),
                        c.getTeamId(), t.getTitle(), t.getOwner(), t.getDueDate().toString(), t.getDueDate().isBefore(today)));
            }
        }
        result.sort(Comparator.comparing(MyTask::dueDate).thenComparing(MyTask::taskId));
        return result;
    }

    @Transactional(readOnly = true)
    public Map<String, Object> summary(HrActor actor) {
        requireHr(actor);
        Map<String, Map<String, Long>> byType = new LinkedHashMap<>();
        for (HrCaseType t : HrCaseType.values()) {
            byType.put(t.name(), new LinkedHashMap<>(Map.of("PENDING_APPROVAL", 0L, "IN_PROGRESS", 0L, "COMPLETED", 0L, "REJECTED", 0L, "CANCELLED", 0L)));
        }
        for (Object[] row : cases.countByTypeStatus(actor.scope())) {
            byType.get(((HrCaseType) row[0]).name()).put((String) row[1], ((Number) row[2]).longValue());
        }
        double hours = 0;
        int n = 0;
        for (Object[] row : cases.completedTimings(actor.scope())) {
            hours += Duration.between((java.time.Instant) row[0], (java.time.Instant) row[1]).toSeconds() / 3600.0;
            n++;
        }
        long overdue = 0;
        LocalDate today = LocalDate.now(HrCheckService.BEIJING);
        List<HrCase> active = cases.findAll((root, q, cb) -> cb.and(root.get("teamId").in(actor.scope()),
                cb.equal(root.get("status"), HrCase.IN_PROGRESS)));
        for (HrCaseTask t : tasks.findByCaseIdIn(active.stream().map(HrCase::getId).toList())) {
            if ("OPEN".equals(t.getStatus()) && t.getDueDate().isBefore(today)) {
                overdue++;
            }
        }
        long effectPending = cases.count((root, q, cb) -> cb.and(root.get("teamId").in(actor.scope()), cb.isTrue(root.get("effectPending"))));
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("byType", byType);
        result.put("overdueTasks", overdue);
        result.put("effectPending", effectPending);
        result.put("avgDaysToComplete", n == 0 ? null : Math.round(hours / n / 24.0 * 10.0) / 10.0);
        return result;
    }

    // ---------------------------------------------------------------- 内部

    private record ParsedCreate(HrCaseType type, LocalDate date) {
    }

    private ParsedCreate parse(HrActor actor, CreateCase request) {
        HrCaseType type = parseType(request.caseType());
        LocalDate date;
        try {
            date = LocalDate.parse(request.effectiveDate().trim());
        } catch (DateTimeParseException e) {
            throw badRequest("生效日期格式应为 YYYY-MM-DD");
        }
        if (!actor.scope().contains(request.teamId())) {
            throw notFound("部门不存在");
        }
        if (request.targetTeamId() != null && !actor.scope().contains(request.targetTeamId())) {
            throw notFound("目标部门不存在");
        }
        return new ParsedCreate(type, date);
    }

    private List<Check> runChecks(HrCase c, boolean forCompletion) {
        return checker.check(c.getCaseType(), c.getEmployeeUserId(), c.getTeamId(), c.getTargetTeamId(), c.getEffectiveDate(),
                c.isEmployeeIsHead(), c.getId(), forCompletion);
    }

    private HrCase lockPending(HrActor actor, long id) {
        HrCase c = cases.findForUpdate(id).filter(actor::canView).orElseThrow(() -> notFound("人事事项不存在"));
        if (!actor.rolesFor(c).contains("MANAGER")) {
            throw notFound("人事事项不存在");
        }
        if (!HrCase.PENDING_APPROVAL.equals(c.getStatus())) {
            throw badRequest("只有待批准的事项能处理，当前状态: " + c.getStatus());
        }
        if (c.getEmployeeUserId() == actor.userId()) {
            throw badRequest("不能批准与自己有关的人事事项");
        }
        if (c.getInitiatorUserId() == actor.userId()) {
            throw badRequest("不能批准自己发起的事项，需要由部门负责人或企业管理员处理");
        }
        return c;
    }

    private HrCase visible(long id, HrActor actor) {
        return cases.findById(id).filter(actor::canView).orElseThrow(() -> notFound("人事事项不存在"));
    }

    private void requireHr(HrActor actor) {
        if (!actor.isHr()) {
            throw new ResponseStatusException(HttpStatus.FORBIDDEN, "只有人事部门成员能办理这项操作");
        }
    }

    private boolean isActive(HrCase c) {
        return HrCase.PENDING_APPROVAL.equals(c.getStatus()) || HrCase.IN_PROGRESS.equals(c.getStatus());
    }

    private static String ownerLabel(String owner) {
        return switch (owner) {
            case "HR" -> "人事";
            case "IT" -> "IT";
            case "FINANCE" -> "财务";
            case "MANAGER" -> "部门负责人";
            default -> "员工本人";
        };
    }

    private CaseDto toDto(HrCase c, HrActor actor) {
        LocalDate today = LocalDate.now(HrCheckService.BEIJING);
        List<TaskDto> list = tasks.findByCaseIdOrderBySeq(c.getId()).stream()
                .map(t -> new TaskDto(t.getId(), t.getSeq(), t.getTitle(), t.getOwner(), t.isRequired(), t.getStatus(),
                        t.getDueDate().toString(), "OPEN".equals(t.getStatus()) && t.getDueDate().isBefore(today),
                        t.getDoneBy(), t.getDoneAt(), t.getNote())).toList();
        List<Check> checks = parseChecks(c.getCheckJson());
        return new CaseDto(c.getId(), c.getCaseType().name(), c.getCaseType().label(), c.getEmployeeUserId(), c.getTeamId(),
                c.getTargetTeamId(), c.getPosition(), c.getEffectiveDate().toString(), c.getReason(), c.getStatus(),
                c.getInitiatorUserId(), c.getApproverUserId(), c.getDecisionNote(), c.isEmployeeIsHead(), checks,
                HrCheckService.overall(checks), list, c.isEffectPending(), c.getCreatedAt(), c.getUpdatedAt(),
                c.getCompletedAt(), new ArrayList<>(actor.rolesFor(c)));
    }

    private String json(List<Check> checks) {
        try {
            return mapper.writeValueAsString(checks);
        } catch (JsonProcessingException e) {
            throw new IllegalStateException(e);
        }
    }

    private List<Check> parseChecks(String json) {
        if (json == null || json.isBlank()) {
            return List.of();
        }
        try {
            return mapper.readValue(json, new TypeReference<List<Check>>() {
            });
        } catch (JsonProcessingException e) {
            return List.of();
        }
    }

    private HrCaseType parseType(String raw) {
        try {
            return HrCaseType.valueOf(raw.trim().toUpperCase());
        } catch (IllegalArgumentException e) {
            throw badRequest("未知的事项类型: " + raw);
        }
    }

    private static String blankToNull(String text) {
        return text == null || text.isBlank() ? null : text.trim();
    }

    private ResponseStatusException badRequest(String message) {
        return new ResponseStatusException(HttpStatus.BAD_REQUEST, message);
    }

    private ResponseStatusException notFound(String message) {
        return new ResponseStatusException(HttpStatus.NOT_FOUND, message);
    }
}
