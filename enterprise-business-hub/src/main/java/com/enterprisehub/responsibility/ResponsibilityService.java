package com.enterprisehub.responsibility;

import com.enterprisehub.audit.AuditService;
import com.enterprisehub.responsibility.RespDtos.*;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.persistence.criteria.Predicate;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.domain.Sort;
import org.springframework.data.jpa.domain.Specification;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDate;
import java.util.*;
import java.util.stream.Collectors;

import static com.enterprisehub.responsibility.RespRules.*;

/**
 * 部门责任执行：文本整理出的责任计划 → 负责人核对并正式指派 → 员工接受 → 执行与反馈 → 提交交付物 → 验收人验收。
 *
 * 三条硬规则（Agent 只能建议，不能替人决定）：
 * 1. 正式指派（发布）、改责任人/期限、取消、强制关闭只有该部门负责人或企业管理员能做；
 * 2. 接受责任、报告阻塞、提交成果只有主责员工本人能做——指派人也不能替员工接受；
 * 3. 验收只有验收人能做，且验收人不能是主责人。
 * 谁是"可被指派的有效成员"由 FastAPI 计算后随请求签名传入（{@link Eligible}），这里只信签名的集合。
 */
@Service
public class ResponsibilityService {
    private static final int MAX_TASKS = 30;

    private final RespPlanRepository plans;
    private final RespTaskRepository tasks;
    private final RespCollaboratorRepository collaborators;
    private final RespDependencyRepository dependencies;
    private final RespDeliverableRepository deliverables;
    private final RespEventRepository events;
    private final AuditService audit;
    private final ObjectMapper mapper;

    public ResponsibilityService(RespPlanRepository plans, RespTaskRepository tasks, RespCollaboratorRepository collaborators,
                                 RespDependencyRepository dependencies, RespDeliverableRepository deliverables,
                                 RespEventRepository events, AuditService audit, ObjectMapper mapper) {
        this.plans = plans;
        this.tasks = tasks;
        this.collaborators = collaborators;
        this.dependencies = dependencies;
        this.deliverables = deliverables;
        this.events = events;
        this.audit = audit;
        this.mapper = mapper;
    }

    // ================================================================ 创建与草稿编辑

    @Transactional
    public PlanDto createPlan(RespActor actor, CreatePlan req, String traceId) {
        if (!actor.canAccess(req.teamId())) {
            throw notFound("部门不存在");
        }
        if (req.automationWorkId() != null) {
            Optional<RespPlan> existing = plans.findByAutomationWorkId(req.automationWorkId());
            if (existing.isPresent()) {
                // 同一次整理只能生成一个计划：重复保存/重试直接返回已生成的那个（要求是同一个人在同一个部门）
                RespPlan plan = existing.get();
                if (plan.getCreatedBy() != actor.userId() || plan.getTeamId() != req.teamId()) {
                    throw badRequest("这次整理结果已被使用");
                }
                return planDto(actor, plan, false);
            }
        }
        List<TaskInput> inputs = req.tasks() == null ? List.of() : req.tasks();
        if (inputs.isEmpty()) {
            throw badRequest("没有可以生成的责任事项");
        }
        if (inputs.size() > MAX_TASKS) {
            throw badRequest("一次最多整理 " + MAX_TASKS + " 项责任");
        }
        RespPlan plan = new RespPlan(req.teamId(), req.title().trim(), sourceType(req.sourceType()), req.sourceText(),
                blankToNull(req.summary()), json(req.decisions() == null ? List.of() : req.decisions()),
                json(req.unresolved() == null ? List.of() : req.unresolved()), actor.userId(), req.automationWorkId());
        plans.save(plan);
        Map<Integer, RespTask> bySeq = new LinkedHashMap<>();
        int seq = 1;
        for (TaskInput input : inputs) {
            RespTask task = new RespTask(plan.getId(), plan.getTeamId(), seq, input.title().trim());
            applyInput(task, input, req.eligible());
            tasks.save(task);
            replaceCollaborators(task, input.collaboratorUserIds(), task.getResponsibleUserId(), req.eligible());
            bySeq.put(seq++, task);
        }
        for (Map.Entry<Integer, RespTask> entry : bySeq.entrySet()) {
            replaceDependencies(entry.getValue(), inputs.get(entry.getKey() - 1).dependsOnSeq(), bySeq);
        }
        int missing = 0;
        for (RespTask task : bySeq.values()) {
            missing += (int) issuesFor(task).stream().filter(i -> i.code().startsWith("NO_")).count();
        }
        plan.setMissingAtCreation(missing);
        event(plan.getId(), null, "PLAN_CREATED", actor.userId(), null, Map.of("tasks", inputs.size(), "missing", missing));
        audit.record(actor.userId(), "responsibility.plan_created", "responsibility_plan", plan.getId(),
                "{\"tasks\":" + inputs.size() + "}", traceId);
        return planDto(actor, plan, false);
    }

    @Transactional
    public PlanDto editPlan(RespActor actor, long planId, EditPlan req) {
        RespPlan plan = lockDraftPlan(actor, planId);
        plan.edit(blankToNull(req.title()), blankToNull(req.summary()), json(req.unresolved() == null ? List.of() : req.unresolved()));
        return planDto(actor, plan, false);
    }

    @Transactional
    public PlanDto editTask(RespActor actor, long taskId, EditTask req) {
        RespTask task = lockTask(actor, taskId);
        RespPlan plan = lockDraftPlan(actor, task.getPlanId());
        if (!RespTask.DRAFT.equals(task.getStatus())) {
            throw badRequest("只有草稿阶段的责任能这样修改；已发布的请由负责人走「变更责任」");
        }
        TaskInput input = req.task();
        List<String> changes = diffNames(task, input);
        task.setTitle(input.title().trim());
        applyInput(task, input, req.eligible());
        replaceCollaborators(task, input.collaboratorUserIds(), task.getResponsibleUserId(), req.eligible());
        Map<Integer, RespTask> bySeq = tasks.findByPlanIdOrderBySeq(plan.getId()).stream()
                .collect(Collectors.toMap(RespTask::getSeq, t -> t, (a, b) -> a, LinkedHashMap::new));
        replaceDependencies(task, input.dependsOnSeq(), bySeq);
        task.touch();
        if (!changes.isEmpty()) {
            event(plan.getId(), task.getId(), "TASK_EDITED", actor.userId(), null, Map.of("fields", changes));
        }
        return planDto(actor, plan, false);
    }

    @Transactional
    public PlanDto addTask(RespActor actor, long planId, AddTask req) {
        RespPlan plan = lockDraftPlan(actor, planId);
        List<RespTask> current = tasks.findByPlanIdOrderBySeq(planId);
        if (current.size() >= MAX_TASKS) {
            throw badRequest("一个计划最多 " + MAX_TASKS + " 项责任");
        }
        int seq = current.stream().mapToInt(RespTask::getSeq).max().orElse(0) + 1;
        TaskInput input = req.task();
        RespTask task = new RespTask(planId, plan.getTeamId(), seq, input.title().trim());
        applyInput(task, input, req.eligible());
        tasks.save(task);
        replaceCollaborators(task, input.collaboratorUserIds(), task.getResponsibleUserId(), req.eligible());
        Map<Integer, RespTask> bySeq = current.stream().collect(Collectors.toMap(RespTask::getSeq, t -> t));
        bySeq.put(seq, task);
        replaceDependencies(task, input.dependsOnSeq(), bySeq);
        event(planId, task.getId(), "TASK_ADDED", actor.userId(), null, Map.of("seq", seq));
        return planDto(actor, plan, false);
    }

    @Transactional
    public PlanDto removeTask(RespActor actor, long taskId) {
        RespTask task = lockTask(actor, taskId);
        RespPlan plan = lockDraftPlan(actor, task.getPlanId());
        if (!RespTask.DRAFT.equals(task.getStatus())) {
            throw badRequest("只有草稿阶段的责任能删除");
        }
        collaborators.deleteByTaskId(task.getId());
        dependencies.deleteInvolving(task.getId());
        tasks.delete(task);
        event(plan.getId(), null, "TASK_REMOVED", actor.userId(), task.getTitle(), Map.of("seq", task.getSeq()));
        return planDto(actor, plan, false);
    }

    // ================================================================ 正式指派（发布）

    @Transactional
    public PlanDto publish(RespActor actor, long planId, Publish req, String traceId) {
        RespPlan plan = plans.findForUpdate(planId).filter(p -> actor.canAccess(p.getTeamId())).orElseThrow(() -> notFound("责任计划不存在"));
        if (!actor.isHead(plan.getTeamId())) {
            throw forbidden("只有部门负责人或企业管理员能正式指派责任");
        }
        if (!RespPlan.DRAFT.equals(plan.getStatus())) {
            throw badRequest("只有草稿状态的计划能发布，当前状态: " + plan.getStatus());
        }
        Eligible eligible = req.eligible();
        List<RespTask> list = tasks.findByPlanIdOrderBySeq(planId).stream().filter(t -> !t.isTerminal()).toList();
        if (list.isEmpty()) {
            throw badRequest("计划里没有可发布的责任");
        }
        Map<Long, Set<Long>> collab = collaboratorMap(list);
        List<String> problems = new ArrayList<>();
        for (RespTask t : list) {
            List<String> own = issuesFor(t).stream().filter(i -> "BLOCK".equals(i.level())).map(Issue::message).collect(Collectors.toCollection(ArrayList::new));
            if (t.getResponsibleUserId() != null && (eligible == null || !eligible.canWork(t.getResponsibleUserId()))) {
                own.add("主责员工已不是本部门的有效成员（离开部门或账号已停用），请重新选择");
            }
            if (t.getReviewerUserId() != null && (eligible == null || !eligible.canReview(t.getReviewerUserId()))) {
                own.add("验收人已不是有效成员，请重新选择");
            }
            for (long c : collab.getOrDefault(t.getId(), Set.of())) {
                if (eligible == null || !eligible.canWork(c)) {
                    own.add("协办人（用户 " + c + "）已不是本部门的有效成员");
                }
            }
            if (!own.isEmpty()) {
                problems.add("第 " + t.getSeq() + " 项「" + t.getTitle() + "」：" + String.join("；", own));
            }
        }
        if (!problems.isEmpty()) {
            throw badRequest("还不能发布：" + String.join(" ｜ ", problems));
        }
        for (RespTask t : list) {
            boolean aiPresent = t.getAiResponsibleUserId() != null;
            boolean aiMatched = aiPresent && t.getAiResponsibleUserId().equals(t.getResponsibleUserId());
            t.assign(actor.userId());
            event(planId, t.getId(), "PUBLISHED", actor.userId(), blankToNull(req.note()),
                    Map.of("responsible", t.getResponsibleUserId(), "aiPresent", aiPresent, "aiMatched", aiMatched));
        }
        plan.publish(actor.userId());
        audit.record(actor.userId(), "responsibility.published", "responsibility_plan", planId,
                "{\"tasks\":" + list.size() + "}", traceId);
        return planDto(actor, plan, false);
    }

    @Transactional
    public PlanDto cancelPlan(RespActor actor, long planId, String reason, String traceId) {
        RespPlan plan = plans.findForUpdate(planId).filter(p -> actor.canAccess(p.getTeamId())).orElseThrow(() -> notFound("责任计划不存在"));
        boolean owner = plan.getCreatedBy() == actor.userId() && RespPlan.DRAFT.equals(plan.getStatus());
        if (!actor.isHead(plan.getTeamId()) && !owner) {
            throw forbidden("只有部门负责人或企业管理员能取消已发布的计划");
        }
        if (RespPlan.COMPLETED.equals(plan.getStatus()) || RespPlan.CANCELLED.equals(plan.getStatus())) {
            throw badRequest("计划已经结束，不能取消");
        }
        String why = RespPlan.DRAFT.equals(plan.getStatus()) ? blankToNull(reason) : require(reason, 2, "取消已发布的计划需要写明原因");
        for (RespTask t : tasks.findByPlanIdOrderBySeq(planId)) {
            if (!t.isTerminal()) {
                t.cancel();
                event(planId, t.getId(), "CANCELLED", actor.userId(), why, Map.of("byPlan", true));
            }
        }
        plan.setStatus(RespPlan.CANCELLED);
        event(planId, null, "PLAN_CANCELLED", actor.userId(), why, Map.of());
        audit.record(actor.userId(), "responsibility.plan_cancelled", "responsibility_plan", planId, why, traceId);
        return planDto(actor, plan, false);
    }

    // ================================================================ 员工与验收人的动作

    @Transactional
    public TaskDetail act(RespActor actor, long taskId, String action, TaskAction body, String traceId) {
        RespTask t = lockTask(actor, taskId);
        RespPlan plan = plans.findById(t.getPlanId()).orElseThrow(() -> notFound("责任计划不存在"));
        TaskAction b = body == null ? new TaskAction(null, null, null, null, null, null, null, null, null, null, null, null, null, null, null, null) : body;
        switch (action) {
            case "accept" -> accept(actor, t);
            case "object" -> object(actor, t, b);
            case "progress" -> progress(actor, t, b);
            case "block" -> block(actor, t, b);
            case "unblock" -> unblock(actor, t, b);
            case "submit" -> submit(actor, t, b);
            case "verify" -> verify(actor, t, b, traceId);
            case "rework" -> rework(actor, t, b);
            case "request-extension" -> requestExtension(actor, t, b);
            case "decide-extension" -> decideExtension(actor, t, b);
            case "request-transfer" -> requestTransfer(actor, t, b);
            case "decide-transfer" -> decideTransfer(actor, t, b, traceId);
            case "revise" -> revise(actor, t, b, traceId);
            case "cancel" -> cancel(actor, t, b, traceId);
            default -> throw notFound("未知的操作");
        }
        refreshPlan(plan);
        return detail(actor, t.getId());
    }

    private void accept(RespActor actor, RespTask t) {
        requireResponsible(actor, t, "只有主责员工本人能接受责任，指派人和其他人都不能替他接受");
        requireStatus(t, "接受", RespTask.PENDING_ACCEPT);
        t.accept();
        event(t.getPlanId(), t.getId(), "ACCEPTED", actor.userId(), null, Map.of());
    }

    private void object(RespActor actor, RespTask t, TaskAction b) {
        requireResponsible(actor, t, "只有主责员工本人能对责任提出异议");
        requireStatus(t, "提出异议", RespTask.PENDING_ACCEPT);
        String note = require(b.reason() != null ? b.reason() : b.note(), 2, "请写明异议内容（责任人、期限或验收标准哪里不合适）");
        t.object(note);
        event(t.getPlanId(), t.getId(), "OBJECTED", actor.userId(), note, Map.of());
    }

    private void progress(RespActor actor, RespTask t, TaskAction b) {
        requireResponsible(actor, t, "只有主责员工本人能报告进度");
        requireStatus(t, "报告进度", RespTask.IN_PROGRESS, RespTask.BLOCKED);
        String note = require(b.note(), 2, "请写明当前进度");
        Integer percent = b.percent();
        if (percent != null && (percent < 0 || percent > 100)) {
            throw badRequest("完成百分比应在 0 到 100 之间");
        }
        t.progress(note, percent);
        event(t.getPlanId(), t.getId(), "PROGRESS", actor.userId(), note, percent == null ? Map.of() : Map.of("percent", percent));
    }

    private void block(RespActor actor, RespTask t, TaskAction b) {
        requireResponsible(actor, t, "只有主责员工本人能报告阻塞");
        requireStatus(t, "报告阻塞", RespTask.IN_PROGRESS);
        String reason = require(b.reason() != null ? b.reason() : b.note(), 2, "受阻必须写明原因（缺少资料、等待他人、资源不足…）");
        if (b.waitingOnUserId() != null && (b.eligible() == null || !b.eligible().canReview(b.waitingOnUserId()))) {
            throw badRequest("等待的对象不是有效成员");
        }
        t.block(reason, b.waitingOnUserId());
        event(t.getPlanId(), t.getId(), "BLOCKED", actor.userId(), reason,
                b.waitingOnUserId() == null ? Map.of() : Map.of("waitingOn", b.waitingOnUserId()));
    }

    private void unblock(RespActor actor, RespTask t, TaskAction b) {
        boolean isResponsible = actor.userId() == orZero(t.getResponsibleUserId());
        if (!isResponsible && !actor.isHead(t.getTeamId())) {
            throw forbidden("只有主责员工或部门负责人能解除阻塞");
        }
        requireStatus(t, "解除阻塞", RespTask.BLOCKED);
        t.unblock();
        event(t.getPlanId(), t.getId(), "UNBLOCKED", actor.userId(), blankToNull(b.note()), Map.of());
    }

    private void submit(RespActor actor, RespTask t, TaskAction b) {
        requireResponsible(actor, t, "只有主责员工本人能提交成果");
        requireStatus(t, "提交成果", RespTask.IN_PROGRESS);
        String summary = require(b.summary(), 2, "请写明交付物的说明（做了什么、成果在哪里）");
        String link = safeLink(b.link());
        List<Long> depIds = dependencies.findByTaskIdIn(List.of(t.getId())).stream().map(RespDependency::getDependsOnTaskId).toList();
        if (!depIds.isEmpty()) {
            List<String> open = tasks.findAllById(depIds).stream()
                    .filter(d -> !RespTask.DONE.equals(d.getStatus()) && !RespTask.CANCELLED.equals(d.getStatus()))
                    .map(d -> "第 " + d.getSeq() + " 项「" + d.getTitle() + "」").toList();
            if (!open.isEmpty()) {
                throw badRequest("前置事项还没有完成，暂不能提交：" + String.join("、", open));
            }
        }
        int no = deliverables.countByTaskId(t.getId()) + 1;
        deliverables.save(new RespDeliverable(t.getId(), no, summary, link, actor.userId()));
        t.submit();
        event(t.getPlanId(), t.getId(), "SUBMITTED", actor.userId(), summary, Map.of("submission", no));
    }

    private void verify(RespActor actor, RespTask t, TaskAction b, String traceId) {
        requireReviewer(actor, t, "只有指定的验收人能验收，其他人（包括指派人）都不能替他确认完成");
        requireStatus(t, "验收", RespTask.PENDING_REVIEW);
        if (t.getResponsibleUserId() != null && t.getResponsibleUserId() == actor.userId()) {
            throw forbidden("主责人不能验收自己的成果");
        }
        t.verify();
        event(t.getPlanId(), t.getId(), "VERIFIED", actor.userId(), blankToNull(b.note()), Map.of("reworks", t.getReworkCount()));
        audit.record(actor.userId(), "responsibility.verified", "responsibility_task", t.getId(), null, traceId);
    }

    private void rework(RespActor actor, RespTask t, TaskAction b) {
        requireReviewer(actor, t, "只有指定的验收人能退回");
        requireStatus(t, "退回", RespTask.PENDING_REVIEW);
        String reason = require(b.reason() != null ? b.reason() : b.note(), 2, "退回需要写明哪里没有达到验收标准");
        t.rework();
        event(t.getPlanId(), t.getId(), "REWORK", actor.userId(), reason, Map.of("count", t.getReworkCount()));
    }

    private void requestExtension(RespActor actor, RespTask t, TaskAction b) {
        requireResponsible(actor, t, "只有主责员工本人能申请延期");
        requireStatus(t, "申请延期", RespTask.IN_PROGRESS, RespTask.BLOCKED);
        LocalDate proposed = parseDate(b.proposedDate(), "延期日期");
        if (proposed == null || t.getDueDate() == null || !proposed.isAfter(t.getDueDate())) {
            throw badRequest("申请的日期必须晚于当前截止日期");
        }
        String reason = require(b.reason(), 2, "请写明延期原因");
        t.requestExtension(proposed, reason);
        event(t.getPlanId(), t.getId(), "EXTENSION_REQUESTED", actor.userId(), reason,
                Map.of("from", t.getDueDate().toString(), "to", proposed.toString()));
    }

    private void decideExtension(RespActor actor, RespTask t, TaskAction b) {
        requireHead(actor, t, "只有部门负责人或企业管理员能决定延期");
        if (t.getPendingDueDate() == null) {
            throw badRequest("没有待决定的延期申请");
        }
        LocalDate proposed = t.getPendingDueDate();
        if (Boolean.TRUE.equals(b.approve())) {
            LocalDate before = t.getDueDate();
            t.setDueDate(proposed);
            t.clearExtension();
            event(t.getPlanId(), t.getId(), "EXTENSION_APPROVED", actor.userId(), blankToNull(b.note()),
                    Map.of("from", String.valueOf(before), "to", proposed.toString()));
        } else {
            String note = require(b.note(), 2, "不同意延期请写明原因");
            t.clearExtension();
            event(t.getPlanId(), t.getId(), "EXTENSION_REJECTED", actor.userId(), note, Map.of("proposed", proposed.toString()));
        }
    }

    private void requestTransfer(RespActor actor, RespTask t, TaskAction b) {
        requireResponsible(actor, t, "只有主责员工本人能申请转交");
        requireStatus(t, "申请转交", RespTask.IN_PROGRESS, RespTask.BLOCKED);
        String note = require(b.reason() != null ? b.reason() : b.note(), 2, "请写明转交原因和建议的接手人");
        t.requestTransfer(note);
        event(t.getPlanId(), t.getId(), "TRANSFER_REQUESTED", actor.userId(), note, Map.of());
    }

    private void decideTransfer(RespActor actor, RespTask t, TaskAction b, String traceId) {
        requireHead(actor, t, "只有部门负责人或企业管理员能决定转交");
        if (t.getTransferNote() == null) {
            throw badRequest("没有待决定的转交申请");
        }
        if (Boolean.TRUE.equals(b.approve())) {
            if (b.responsibleUserId() == null) {
                throw badRequest("同意转交需要选择新的主责员工");
            }
            TaskAction revise = new TaskAction(null, b.reason() != null ? b.reason() : "同意转交申请", null, null, null, null, null, null,
                    b.responsibleUserId(), null, null, null, null, null, null, b.eligible());
            revise(actor, t, revise, traceId);
        } else {
            String note = require(b.note(), 2, "不同意转交请写明原因");
            t.clearTransfer();
            event(t.getPlanId(), t.getId(), "TRANSFER_REJECTED", actor.userId(), note, Map.of());
        }
    }

    /** 负责人变更责任人、验收人、期限、验收标准等：必须写原因，逐项记录变更前后。 */
    private void revise(RespActor actor, RespTask t, TaskAction b, String traceId) {
        requireHead(actor, t, "只有部门负责人或企业管理员能变更责任");
        if (t.isTerminal() || RespTask.DRAFT.equals(t.getStatus())) {
            throw badRequest("当前状态不能变更责任: " + t.getStatus());
        }
        String reason = require(b.reason(), 2, "变更责任需要写明原因");
        boolean reviewOnly = RespTask.PENDING_REVIEW.equals(t.getStatus());
        Eligible eligible = b.eligible();
        List<Map<String, Object>> changes = new ArrayList<>();
        boolean responsibleChanged = false;

        if (b.responsibleUserId() != null && !b.responsibleUserId().equals(t.getResponsibleUserId())) {
            if (reviewOnly) {
                throw badRequest("已提交待验收的责任不能更换主责人，请先由验收人退回");
            }
            if (eligible == null || !eligible.canWork(b.responsibleUserId())) {
                throw badRequest("新的主责员工不是本部门的有效成员");
            }
            changes.add(change("responsible", t.getResponsibleUserId(), b.responsibleUserId()));
            t.setResponsibleUserId(b.responsibleUserId());
            responsibleChanged = true;
        }
        if (b.reviewerUserId() != null && !b.reviewerUserId().equals(t.getReviewerUserId())) {
            if (eligible == null || !eligible.canReview(b.reviewerUserId())) {
                throw badRequest("新的验收人不是有效成员");
            }
            changes.add(change("reviewer", t.getReviewerUserId(), b.reviewerUserId()));
            t.setReviewerUserId(b.reviewerUserId());
        }
        if (t.getReviewerUserId() != null && t.getReviewerUserId().equals(t.getResponsibleUserId())) {
            throw badRequest("主责人不能验收自己的成果，请另选验收人");
        }
        LocalDate due = parseDate(b.dueDate(), "截止日期");
        if (due != null && !due.equals(t.getDueDate())) {
            if (reviewOnly) {
                throw badRequest("已提交待验收的责任不能改期限");
            }
            if (due.isBefore(today())) {
                throw badRequest("新的截止日期不能早于今天");
            }
            checkDependencyDue(t, due);
            changes.add(change("dueDate", t.getDueDate(), due));
            t.setDueDate(due);
            t.clearExtension();
        }
        String criteria = blankToNull(b.acceptanceCriteria());
        if (criteria != null && !criteria.equals(t.getAcceptanceCriteria())) {
            if (reviewOnly) {
                throw badRequest("已提交待验收的责任不能改验收标准");
            }
            changes.add(change("acceptanceCriteria", t.getAcceptanceCriteria(), criteria));
            t.setAcceptanceCriteria(criteria);
        }
        String deliverable = blankToNull(b.deliverable());
        if (deliverable != null && !deliverable.equals(t.getDeliverable())) {
            if (reviewOnly) {
                throw badRequest("已提交待验收的责任不能改交付物");
            }
            changes.add(change("deliverable", t.getDeliverable(), deliverable));
            t.setDeliverable(deliverable);
        }
        if (b.priority() != null && !priority(b.priority()).equals(t.getPriority())) {
            changes.add(change("priority", t.getPriority(), priority(b.priority())));
            t.setPriority(priority(b.priority()));
        }
        if (b.collaboratorUserIds() != null) {
            Set<Long> before = collaboratorMap(List.of(t)).getOrDefault(t.getId(), Set.of());
            Set<Long> after = new LinkedHashSet<>(b.collaboratorUserIds());
            if (!before.equals(after)) {
                replaceCollaborators(t, b.collaboratorUserIds(), t.getResponsibleUserId(), eligible);
                changes.add(change("collaborators", before, after));
            }
        }
        if (changes.isEmpty()) {
            throw badRequest("没有任何变更");
        }
        if (responsibleChanged || RespTask.NEGOTIATING.equals(t.getStatus())) {
            // 换了人，或是回应员工的异议：必须让（新）主责员工重新确认，不能静默生效
            t.assign(actor.userId());
            t.clearTransfer();
        }
        t.touch();
        event(t.getPlanId(), t.getId(), "REVISED", actor.userId(), reason, Map.of("changes", changes));
        audit.record(actor.userId(), "responsibility.revised", "responsibility_task", t.getId(), reason, traceId);
    }

    private void cancel(RespActor actor, RespTask t, TaskAction b, String traceId) {
        requireHead(actor, t, "只有部门负责人或企业管理员能取消或强制关闭责任");
        if (t.isTerminal()) {
            throw badRequest("责任已经结束");
        }
        String reason = require(b.reason(), 2, "取消责任需要写明原因");
        t.cancel();
        event(t.getPlanId(), t.getId(), "CANCELLED", actor.userId(), reason, Map.of());
        audit.record(actor.userId(), "responsibility.cancelled", "responsibility_task", t.getId(), reason, traceId);
    }

    private void checkDependencyDue(RespTask t, LocalDate due) {
        for (RespDependency d : dependencies.findByTaskIdIn(List.of(t.getId()))) {
            tasks.findById(d.getDependsOnTaskId()).ifPresent(dep -> {
                if (dep.getDueDate() != null && due.isBefore(dep.getDueDate()) && !dep.isTerminal()) {
                    throw badRequest("截止日期不能早于它依赖的前置事项（第 " + dep.getSeq() + " 项，" + dep.getDueDate() + "）");
                }
            });
        }
    }

    // ================================================================ 查询

    @Transactional(readOnly = true)
    public TaskDetail getTask(RespActor actor, long taskId) {
        return detail(actor, taskId);
    }

    @Transactional(readOnly = true)
    public PlanDto getPlan(RespActor actor, long planId) {
        RespPlan plan = plans.findById(planId).filter(p -> actor.canAccess(p.getTeamId())).orElseThrow(() -> notFound("责任计划不存在"));
        requirePlanVisible(actor, plan);
        return planDto(actor, plan, true);
    }

    /** view：mine 我主责的 / collab 我协办的 / review 等我验收（或我验收的）/ assigned 我指派的 / team 部门全部（仅负责人）。 */
    @Transactional(readOnly = true)
    public List<TaskDto> listTasks(RespActor actor, String view, String status, Long teamId, int limit) {
        String mode = view == null ? "mine" : view;
        Set<Long> collabTaskIds = "collab".equals(mode)
                ? collaborators.findByUserId(actor.userId()).stream().map(RespCollaborator::getTaskId).collect(Collectors.toSet()) : Set.of();
        if ("collab".equals(mode) && collabTaskIds.isEmpty()) {
            return List.of();
        }
        Set<Long> teamScope = teamId != null ? (actor.canAccess(teamId) ? Set.of(teamId) : Set.<Long>of()) : actor.accessibleTeams();
        if (teamScope.isEmpty()) {
            return List.of();
        }
        if ("team".equals(mode) && teamScope.stream().noneMatch(actor::isHead)) {
            throw forbidden("部门责任看板只有部门负责人或企业管理员能看");
        }
        Specification<RespTask> spec = (root, query, cb) -> {
            List<Predicate> p = new ArrayList<>();
            p.add(root.get("teamId").in(teamScope));
            p.add(cb.notEqual(root.get("status"), RespTask.DRAFT));
            switch (mode) {
                case "mine" -> p.add(cb.equal(root.get("responsibleUserId"), actor.userId()));
                case "collab" -> p.add(root.get("id").in(collabTaskIds));
                case "review" -> p.add(cb.equal(root.get("reviewerUserId"), actor.userId()));
                case "assigned" -> p.add(cb.equal(root.get("assignedByUserId"), actor.userId()));
                case "team" -> p.add(root.get("teamId").in(teamScope.stream().filter(actor::isHead).toList()));
                default -> throw badRequest("未知的视图: " + view);
            }
            if (status != null && !status.isBlank()) {
                p.add(root.get("status").in(Arrays.stream(status.split(",")).map(s -> s.trim().toUpperCase()).toList()));
            }
            return cb.and(p.toArray(new Predicate[0]));
        };
        List<RespTask> rows = tasks.findAll(spec, PageRequest.of(0, Math.max(1, Math.min(limit, 300)),
                Sort.by(Sort.Order.asc("dueDate"), Sort.Order.asc("id")))).getContent();
        return toDtos(actor, rows, false);
    }

    @Transactional(readOnly = true)
    public List<PlanSummary> listPlans(RespActor actor, String status, Long teamId, int limit) {
        Set<Long> teamScope = teamId != null ? (actor.canAccess(teamId) ? Set.of(teamId) : Set.<Long>of()) : actor.accessibleTeams();
        if (teamScope.isEmpty()) {
            return List.of();
        }
        Set<Long> involvedPlans = new HashSet<>();   // 只含已发布的计划：草稿对相关员工不可见
        for (RespTask t : tasks.findAll((root, q, cb) -> cb.and(root.get("teamId").in(teamScope), cb.or(
                cb.equal(root.get("responsibleUserId"), actor.userId()), cb.equal(root.get("reviewerUserId"), actor.userId()),
                cb.equal(root.get("assignedByUserId"), actor.userId()))))) {
            involvedPlans.add(t.getPlanId());
        }
        for (RespCollaborator c : collaborators.findByUserId(actor.userId())) {
            tasks.findById(c.getTaskId()).ifPresent(t -> involvedPlans.add(t.getPlanId()));
        }
        Specification<RespPlan> spec = (root, query, cb) -> {
            List<Predicate> p = new ArrayList<>();
            p.add(root.get("teamId").in(teamScope));
            Predicate seesAll = root.get("teamId").in(teamScope.stream().filter(actor::isHead).toList());
            Predicate involved = involvedPlans.isEmpty() ? cb.disjunction()
                    : cb.and(root.get("id").in(involvedPlans), cb.notEqual(root.get("status"), RespPlan.DRAFT));
            Predicate mine = cb.or(cb.equal(root.get("createdBy"), actor.userId()), involved);
            p.add(teamScope.stream().anyMatch(actor::isHead) ? cb.or(seesAll, mine) : mine);
            if (status != null && !status.isBlank()) {
                p.add(cb.equal(root.get("status"), status.trim().toUpperCase()));
            }
            return cb.and(p.toArray(new Predicate[0]));
        };
        List<RespPlan> rows = plans.findAll(spec, PageRequest.of(0, Math.max(1, Math.min(limit, 200)), Sort.by(Sort.Direction.DESC, "id"))).getContent();
        Map<Long, List<RespTask>> byPlan = tasks.findByPlanIdIn(rows.stream().map(RespPlan::getId).toList()).stream()
                .collect(Collectors.groupingBy(RespTask::getPlanId));
        return rows.stream().map(p -> {
            List<RespTask> list = byPlan.getOrDefault(p.getId(), List.of()).stream().filter(t -> !RespTask.CANCELLED.equals(t.getStatus())).toList();
            int issueCount = RespPlan.DRAFT.equals(p.getStatus())
                    ? (int) list.stream().filter(t -> issuesFor(t).stream().anyMatch(i -> "BLOCK".equals(i.level()))).count() : 0;
            return new PlanSummary(p.getId(), p.getTeamId(), p.getTitle(), p.getSourceType(), p.getStatus(), p.getCreatedBy(), list.size(),
                    (int) list.stream().filter(t -> RespTask.DONE.equals(t.getStatus())).count(),
                    (int) list.stream().filter(t -> !t.isTerminal()).count(), issueCount, p.getCreatedAt(), p.getPublishedAt());
        }).toList();
    }

    // ================================================================ 内部：构造 DTO

    private TaskDetail detail(RespActor actor, long taskId) {
        RespTask t = tasks.findById(taskId).filter(x -> actor.canAccess(x.getTeamId())).orElseThrow(() -> notFound("责任事项不存在"));
        RespPlan plan = plans.findById(t.getPlanId()).orElseThrow(() -> notFound("责任计划不存在"));
        requirePlanVisible(actor, plan);
        TaskDto dto = toDtos(actor, List.of(t), true).get(0);
        List<DeliverableDto> dels = deliverables.findByTaskIdOrderBySubmissionNoDesc(taskId).stream()
                .map(d -> new DeliverableDto(d.getId(), d.getSubmissionNo(), d.getSummary(), d.getLink(), d.getSubmittedBy(), d.getSubmittedAt())).toList();
        List<EventDto> evs = events.findByTaskIdOrderByIdAsc(taskId).stream().map(this::eventDto).toList();
        return new TaskDetail(dto, dels, evs, plan.getSourceText(), plan.getStatus(), plan.getCreatedBy());
    }

    private PlanDto planDto(RespActor actor, RespPlan plan, boolean withEvents) {
        List<RespTask> list = tasks.findByPlanIdOrderBySeq(plan.getId());
        List<TaskDto> dtos = toDtos(actor, list, true);
        boolean draft = RespPlan.DRAFT.equals(plan.getStatus());
        int blockers = 0, warnings = 0;
        if (draft) {
            for (TaskDto d : dtos) {
                if (RespTask.CANCELLED.equals(d.status())) {
                    continue;
                }
                blockers += (int) d.issues().stream().filter(i -> "BLOCK".equals(i.level())).count();
                warnings += (int) d.issues().stream().filter(i -> "WARN".equals(i.level())).count();
            }
        }
        boolean head = actor.isHead(plan.getTeamId());
        List<EventDto> evs = withEvents ? events.findByPlanIdOrderByIdAsc(plan.getId()).stream().map(this::eventDto).toList() : List.of();
        return new PlanDto(plan.getId(), plan.getTeamId(), plan.getTitle(), plan.getSourceType(), plan.getSummary(),
                parse(plan.getDecisionsJson(), new TypeReference<List<Decision>>() { }),
                parse(plan.getUnresolvedJson(), new TypeReference<List<String>>() { }), plan.getStatus(), plan.getCreatedBy(),
                plan.getPublishedBy(), plan.getSourceText(), dtos, evs, draft && head && blockers == 0 && dtos.stream().anyMatch(d -> !RespTask.CANCELLED.equals(d.status())),
                blockers, warnings, plan.getMissingAtCreation(), plan.getCreatedAt(), plan.getPublishedAt(),
                draft && (head || plan.getCreatedBy() == actor.userId()), head);
    }

    private List<TaskDto> toDtos(RespActor actor, List<RespTask> rows, boolean withIssues) {
        if (rows.isEmpty()) {
            return List.of();
        }
        List<Long> ids = rows.stream().map(RespTask::getId).toList();
        Map<Long, Set<Long>> collab = collaboratorMap(rows);
        Map<Long, List<Long>> deps = dependencies.findByTaskIdIn(ids).stream()
                .collect(Collectors.groupingBy(RespDependency::getTaskId, Collectors.mapping(RespDependency::getDependsOnTaskId, Collectors.toList())));
        Map<Long, RespPlan> planMap = plans.findAllById(rows.stream().map(RespTask::getPlanId).collect(Collectors.toSet())).stream()
                .collect(Collectors.toMap(RespPlan::getId, p -> p));
        Set<Long> depTaskIds = deps.values().stream().flatMap(List::stream).collect(Collectors.toSet());
        Map<Long, RespTask> depTasks = tasks.findAllById(depTaskIds).stream().collect(Collectors.toMap(RespTask::getId, t -> t));
        LocalDate today = today();
        List<TaskDto> result = new ArrayList<>();
        for (RespTask t : rows) {
            RespPlan plan = planMap.get(t.getPlanId());
            List<Long> depIds = deps.getOrDefault(t.getId(), List.of());
            List<Integer> depSeq = depIds.stream().map(id -> depTasks.containsKey(id) ? depTasks.get(id).getSeq() : 0).sorted().toList();
            List<Issue> issues = withIssues && RespTask.DRAFT.equals(t.getStatus())
                    ? issuesFor(t, depIds.stream().map(depTasks::get).filter(Objects::nonNull).map(RespTask::getDueDate).toList()) : List.of();
            Set<Long> cs = collab.getOrDefault(t.getId(), Set.of());
            result.add(new TaskDto(t.getId(), t.getPlanId(), plan == null ? null : plan.getTitle(), t.getTeamId(), t.getSeq(), t.getTitle(),
                    t.getStatus(), t.getResponsibleUserId(), new ArrayList<>(cs), t.getReviewerUserId(), t.getAssignedByUserId(),
                    t.getDeliverable(), t.getAcceptanceCriteria(), t.getPriority(), t.getDueDate() == null ? null : t.getDueDate().toString(),
                    t.getSourceEvidence(), depSeq, depIds, t.getBlockedReason(), t.getBlockedSince(), t.getWaitingOnUserId(),
                    t.getLastProgress(), t.getProgressPercent(), t.getLastProgressAt(), t.getObjectionNote(),
                    t.getPendingDueDate() == null ? null : t.getPendingDueDate().toString(), t.getExtensionReason(), t.getTransferNote(),
                    t.getReworkCount(), t.getAiResponsibleUserId(), t.getAssignedAt(), t.getAcceptedAt(), t.getSubmittedAt(),
                    t.getVerifiedAt(), t.getCompletedAt(), t.isOpenForWork() && t.getDueDate() != null && t.getDueDate().isBefore(today),
                    issues, rolesFor(actor, t, cs, plan), actionsFor(actor, t, plan)));
        }
        return result;
    }

    private List<String> rolesFor(RespActor actor, RespTask t, Set<Long> collab, RespPlan plan) {
        List<String> roles = new ArrayList<>();
        long me = actor.userId();
        if (t.getResponsibleUserId() != null && t.getResponsibleUserId() == me) roles.add("RESPONSIBLE");
        if (collab.contains(me)) roles.add("COLLABORATOR");
        if (t.getReviewerUserId() != null && t.getReviewerUserId() == me) roles.add("REVIEWER");
        if (t.getAssignedByUserId() != null && t.getAssignedByUserId() == me) roles.add("ASSIGNER");
        if (plan != null && plan.getCreatedBy() == me) roles.add("CREATOR");
        if (actor.isHead(t.getTeamId())) roles.add("HEAD");
        return roles;
    }

    /** 此刻调用者能对这项责任做哪些动作（前端据此显示按钮；后端每个动作仍会自己校验）。 */
    private List<String> actionsFor(RespActor actor, RespTask t, RespPlan plan) {
        List<String> a = new ArrayList<>();
        boolean head = actor.isHead(t.getTeamId());
        boolean resp = t.getResponsibleUserId() != null && t.getResponsibleUserId() == actor.userId();
        boolean rev = t.getReviewerUserId() != null && t.getReviewerUserId() == actor.userId()
                && !(t.getResponsibleUserId() != null && t.getResponsibleUserId().equals(t.getReviewerUserId()));
        switch (t.getStatus()) {
            case RespTask.DRAFT -> {
                if (head || (plan != null && plan.getCreatedBy() == actor.userId())) a.add("edit");
            }
            case RespTask.PENDING_ACCEPT -> {
                if (resp) { a.add("accept"); a.add("object"); }
                if (head) { a.add("revise"); a.add("cancel"); }
            }
            case RespTask.NEGOTIATING -> {
                if (head) { a.add("revise"); a.add("cancel"); }
            }
            case RespTask.IN_PROGRESS -> {
                if (resp) { a.addAll(List.of("progress", "block", "submit")); if (t.getPendingDueDate() == null) a.add("request-extension"); if (t.getTransferNote() == null) a.add("request-transfer"); }
                if (head) { a.add("revise"); a.add("cancel"); }
            }
            case RespTask.BLOCKED -> {
                if (resp) { a.addAll(List.of("progress", "unblock")); if (t.getPendingDueDate() == null) a.add("request-extension"); if (t.getTransferNote() == null) a.add("request-transfer"); }
                if (head) { if (!resp) a.add("unblock"); a.add("revise"); a.add("cancel"); }
            }
            case RespTask.PENDING_REVIEW -> {
                if (rev) { a.add("verify"); a.add("rework"); }
                if (head) { a.add("revise"); a.add("cancel"); }
            }
            default -> { }
        }
        if (head && t.getPendingDueDate() != null && !t.isTerminal()) a.add("decide-extension");
        if (head && t.getTransferNote() != null && !t.isTerminal()) a.add("decide-transfer");
        return a;
    }

    private EventDto eventDto(RespEvent e) {
        return new EventDto(e.getId(), e.getTaskId(), e.getEventType(), e.getActorUserId(), e.getNote(), e.getDetailJson(), e.getCreatedAt());
    }

    // ================================================================ 内部：规则与辅助

    private List<Issue> issuesFor(RespTask t) {
        List<Long> depIds = dependencies.findByTaskIdIn(List.of(t.getId())).stream().map(RespDependency::getDependsOnTaskId).toList();
        List<LocalDate> due = depIds.isEmpty() ? List.of() : tasks.findAllById(depIds).stream().map(RespTask::getDueDate).toList();
        return issuesFor(t, due);
    }

    private List<Issue> issuesFor(RespTask t, List<LocalDate> dependencyDue) {
        int heavy = 0;
        if (t.getResponsibleUserId() != null && t.getDueDate() != null && t.getId() != null) {
            heavy = tasks.findHeavy(t.getResponsibleUserId(), t.getDueDate().minusDays(3), t.getDueDate().plusDays(3), t.getId()).size();
        }
        return RespRules.issues(t, dependencyDue, heavy);
    }

    private void applyInput(RespTask task, TaskInput input, Eligible eligible) {
        if (input.responsibleUserId() != null && (eligible == null || !eligible.canWork(input.responsibleUserId()))) {
            throw badRequest("主责员工不是本部门的有效成员");
        }
        if (input.reviewerUserId() != null && (eligible == null || !eligible.canReview(input.reviewerUserId()))) {
            throw badRequest("验收人不是有效成员");
        }
        LocalDate due = parseDate(input.dueDate(), "截止日期");
        task.setResponsibleUserId(input.responsibleUserId());
        task.setReviewerUserId(input.reviewerUserId());
        task.setDueDate(due);
        task.setDeliverable(blankToNull(input.deliverable()));
        task.setAcceptanceCriteria(blankToNull(input.acceptanceCriteria()));
        task.setPriority(priority(input.priority()));
        task.setSourceEvidence(blankToNull(input.evidence()));
        if (task.getId() == null) {
            task.setAiResponsibleUserId(input.aiResponsibleUserId());
        }
        task.touch();
    }

    private void replaceCollaborators(RespTask task, List<Long> ids, Long responsible, Eligible eligible) {
        Set<Long> clean = new LinkedHashSet<>(ids == null ? List.of() : ids);
        for (Long id : clean) {
            if (id.equals(responsible)) {
                throw badRequest("主责人不能同时是协办人");
            }
            if (eligible == null || !eligible.canWork(id)) {
                throw badRequest("协办人不是本部门的有效成员");
            }
        }
        collaborators.deleteByTaskId(task.getId());
        collaborators.flush();
        for (Long id : clean) {
            collaborators.save(new RespCollaborator(task.getId(), id));
        }
    }

    private void replaceDependencies(RespTask task, List<Integer> dependsOnSeq, Map<Integer, RespTask> bySeq) {
        dependencies.deleteByTaskId(task.getId());
        dependencies.flush();
        Set<Integer> wanted = new LinkedHashSet<>(dependsOnSeq == null ? List.of() : dependsOnSeq);
        for (int seq : wanted) {
            RespTask dep = bySeq.get(seq);
            if (dep == null || dep.getId() == null) {
                throw badRequest("前置事项第 " + seq + " 项不存在");
            }
            if (dep.getId().equals(task.getId())) {
                throw badRequest("责任不能依赖自己");
            }
            if (reaches(dep.getId(), task.getId(), new HashSet<>())) {
                throw badRequest("前置关系形成了循环（第 " + task.getSeq() + " 项与第 " + seq + " 项互相依赖）");
            }
            dependencies.save(new RespDependency(task.getId(), dep.getId()));
        }
    }

    private boolean reaches(long from, long target, Set<Long> seen) {
        if (from == target) {
            return true;
        }
        if (!seen.add(from)) {
            return false;
        }
        for (RespDependency d : dependencies.findByTaskIdIn(List.of(from))) {
            if (reaches(d.getDependsOnTaskId(), target, seen)) {
                return true;
            }
        }
        return false;
    }

    private Map<Long, Set<Long>> collaboratorMap(List<RespTask> rows) {
        Map<Long, Set<Long>> map = new HashMap<>();
        for (RespCollaborator c : collaborators.findByTaskIdIn(rows.stream().map(RespTask::getId).toList())) {
            map.computeIfAbsent(c.getTaskId(), k -> new LinkedHashSet<>()).add(c.getUserId());
        }
        return map;
    }

    private List<String> diffNames(RespTask t, TaskInput i) {
        List<String> fields = new ArrayList<>();
        if (!Objects.equals(t.getTitle(), i.title().trim())) fields.add("title");
        if (!Objects.equals(t.getResponsibleUserId(), i.responsibleUserId())) fields.add("responsible");
        if (!Objects.equals(t.getReviewerUserId(), i.reviewerUserId())) fields.add("reviewer");
        if (!Objects.equals(t.getDueDate(), parseDate(i.dueDate(), "截止日期"))) fields.add("dueDate");
        if (!Objects.equals(t.getDeliverable(), blankToNull(i.deliverable()))) fields.add("deliverable");
        if (!Objects.equals(t.getAcceptanceCriteria(), blankToNull(i.acceptanceCriteria()))) fields.add("acceptanceCriteria");
        if (!Objects.equals(t.getPriority(), priority(i.priority()))) fields.add("priority");
        return fields;
    }

    private static Map<String, Object> change(String field, Object from, Object to) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("field", field);
        m.put("from", from instanceof Collection<?> c ? new ArrayList<>(c) : from == null ? null : from.toString());
        m.put("to", to instanceof Collection<?> c ? new ArrayList<>(c) : to == null ? null : to.toString());
        return m;
    }

    /** 全部结束后把计划收尾：全部完成（至少一项）= COMPLETED；全部取消 = CANCELLED。 */
    private void refreshPlan(RespPlan plan) {
        if (!RespPlan.PUBLISHED.equals(plan.getStatus())) {
            return;
        }
        List<RespTask> list = tasks.findByPlanIdOrderBySeq(plan.getId());
        if (list.stream().allMatch(RespTask::isTerminal)) {
            plan.setStatus(list.stream().anyMatch(t -> RespTask.DONE.equals(t.getStatus())) ? RespPlan.COMPLETED : RespPlan.CANCELLED);
        }
    }

    private RespTask lockTask(RespActor actor, long id) {
        RespTask t = tasks.findForUpdate(id).filter(x -> actor.canAccess(x.getTeamId())).orElseThrow(() -> notFound("责任事项不存在"));
        return t;
    }

    private RespPlan lockDraftPlan(RespActor actor, long planId) {
        RespPlan plan = plans.findForUpdate(planId).filter(p -> actor.canAccess(p.getTeamId())).orElseThrow(() -> notFound("责任计划不存在"));
        if (!actor.isHead(plan.getTeamId()) && plan.getCreatedBy() != actor.userId()) {
            throw forbidden("只有计划的创建人或部门负责人能修改草稿");
        }
        if (!RespPlan.DRAFT.equals(plan.getStatus())) {
            throw badRequest("计划已经发布或结束，不能再按草稿修改");
        }
        return plan;
    }

    /**
     * 非负责人只能看到自己有关的计划：创建的、或在其中担任主责/协办/验收/指派。
     * 草稿只有创建人和负责人能看——AI 草稿里的人选还没经负责人确认，不能提前让相关员工看到。
     */
    private void requirePlanVisible(RespActor actor, RespPlan plan) {
        if (actor.isHead(plan.getTeamId()) || plan.getCreatedBy() == actor.userId()) {
            return;
        }
        if (RespPlan.DRAFT.equals(plan.getStatus())) {
            throw notFound("责任计划不存在");
        }
        List<RespTask> list = tasks.findByPlanIdOrderBySeq(plan.getId());
        Map<Long, Set<Long>> collab = collaboratorMap(list);
        for (RespTask t : list) {
            if (Objects.equals(t.getResponsibleUserId(), actor.userId()) || Objects.equals(t.getReviewerUserId(), actor.userId())
                    || Objects.equals(t.getAssignedByUserId(), actor.userId()) || collab.getOrDefault(t.getId(), Set.of()).contains(actor.userId())) {
                return;
            }
        }
        throw notFound("责任计划不存在");
    }

    private void requireResponsible(RespActor actor, RespTask t, String message) {
        if (t.getResponsibleUserId() == null || t.getResponsibleUserId() != actor.userId()) {
            throw forbidden(message);
        }
    }

    private void requireReviewer(RespActor actor, RespTask t, String message) {
        if (t.getReviewerUserId() == null || t.getReviewerUserId() != actor.userId()) {
            throw forbidden(message);
        }
    }

    private void requireHead(RespActor actor, RespTask t, String message) {
        if (!actor.isHead(t.getTeamId())) {
            throw forbidden(message);
        }
    }

    private void requireStatus(RespTask t, String what, String... allowed) {
        if (!Arrays.asList(allowed).contains(t.getStatus())) {
            throw badRequest("当前状态（" + t.getStatus() + "）不能" + what + "，请刷新后再看");
        }
    }

    private static long orZero(Long v) {
        return v == null ? 0L : v;
    }

    private void event(long planId, Long taskId, String type, long actorId, String note, Map<String, ?> detail) {
        events.save(new RespEvent(planId, taskId, type, actorId, note, detail == null || detail.isEmpty() ? null : json(detail)));
    }

    private String json(Object value) {
        try {
            return mapper.writeValueAsString(value);
        } catch (JsonProcessingException e) {
            throw new IllegalStateException(e);
        }
    }

    private <T> List<T> parse(String json, TypeReference<List<T>> type) {
        if (json == null || json.isBlank()) {
            return List.of();
        }
        try {
            return mapper.readValue(json, type);
        } catch (JsonProcessingException e) {
            return List.of();
        }
    }
}
