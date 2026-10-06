package com.enterprisehub.responsibility;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.DayOfWeek;
import java.time.Duration;
import java.time.Instant;
import java.time.LocalDate;
import java.time.temporal.TemporalAdjusters;
import java.util.*;
import java.util.stream.Collectors;

import static com.enterprisehub.responsibility.RespRules.*;

/**
 * 责任协同的汇总与效率指标。刻意不统计"完成任务数量"（鼓励拆小任务），而是看闭环质量：
 * 接受快不快、按时提交、一次验收通过、受阻响应、责任变更、无主/无验收标准的比例，以及 AI 草稿的一次匹配准确率。
 */
@Service
public class RespStatsService {
    private final RespPlanRepository plans;
    private final RespTaskRepository tasks;
    private final RespEventRepository events;
    private final ObjectMapper mapper;

    public RespStatsService(RespPlanRepository plans, RespTaskRepository tasks, RespEventRepository events, ObjectMapper mapper) {
        this.plans = plans;
        this.tasks = tasks;
        this.events = events;
        this.mapper = mapper;
    }

    /** 当前用户的首页卡片数字：自己的待办责任；负责人另有部门维度。 */
    @Transactional(readOnly = true)
    public Map<String, Object> mine(RespActor actor, Long teamId) {
        Set<Long> scope = teamId != null ? (actor.canAccess(teamId) ? Set.of(teamId) : Set.<Long>of()) : actor.accessibleTeams();
        Map<String, Object> result = new LinkedHashMap<>();
        if (scope.isEmpty()) {
            return result;
        }
        List<RespTask> all = tasks.findAll((root, q, cb) -> cb.and(root.get("teamId").in(scope), cb.notEqual(root.get("status"), RespTask.DRAFT)));
        LocalDate today = today();
        long me = actor.userId();
        List<RespTask> mine = all.stream().filter(t -> Objects.equals(t.getResponsibleUserId(), me)).toList();
        result.put("pendingAccept", count(mine, RespTask.PENDING_ACCEPT));
        result.put("inProgress", count(mine, RespTask.IN_PROGRESS));
        result.put("blocked", count(mine, RespTask.BLOCKED));
        result.put("overdue", mine.stream().filter(t -> t.isOpenForWork() && t.getDueDate() != null && t.getDueDate().isBefore(today)).count());
        result.put("dueSoon", mine.stream().filter(t -> t.isOpenForWork() && t.getDueDate() != null
                && !t.getDueDate().isBefore(today) && !t.getDueDate().isAfter(today.plusDays(2))).count());
        result.put("pendingReview", all.stream().filter(t -> Objects.equals(t.getReviewerUserId(), me)
                && RespTask.PENDING_REVIEW.equals(t.getStatus())).count());
        List<RespTask> assigned = all.stream().filter(t -> Objects.equals(t.getAssignedByUserId(), me)).toList();
        result.put("assignedNotAccepted", count(assigned, RespTask.PENDING_ACCEPT) + count(assigned, RespTask.NEGOTIATING));
        Set<Long> heads = scope.stream().filter(actor::isHead).collect(Collectors.toSet());
        boolean head = !heads.isEmpty();
        result.put("isHead", head);
        if (head) {
            List<RespTask> team = all.stream().filter(t -> heads.contains(t.getTeamId())).toList();
            result.put("teamOverdue", team.stream().filter(t -> t.isOpenForWork() && t.getDueDate() != null && t.getDueDate().isBefore(today)).count());
            result.put("teamPendingAccept", count(team, RespTask.PENDING_ACCEPT) + count(team, RespTask.NEGOTIATING));
            result.put("weekCompletionRate", weekRate(team, today));
        }
        return result;
    }

    @Transactional(readOnly = true)
    public Map<String, Object> summary(RespActor actor, long teamId) {
        if (!actor.canAccess(teamId)) {
            throw notFound("部门不存在");
        }
        if (!actor.isHead(teamId)) {
            throw forbidden("部门责任汇总只有部门负责人或企业管理员能看");
        }
        List<RespTask> all = tasks.findAll((root, q, cb) -> cb.equal(root.get("teamId"), teamId));
        List<RespTask> published = all.stream().filter(t -> !RespTask.DRAFT.equals(t.getStatus())).toList();
        List<RespTask> live = all.stream().filter(t -> !RespTask.CANCELLED.equals(t.getStatus())).toList();
        LocalDate today = today();
        Instant now = Instant.now();
        Map<String, Object> result = new LinkedHashMap<>();

        Map<String, Long> byStatus = new LinkedHashMap<>();
        for (String s : List.of(RespTask.PENDING_ACCEPT, RespTask.NEGOTIATING, RespTask.IN_PROGRESS, RespTask.BLOCKED,
                RespTask.PENDING_REVIEW, RespTask.DONE, RespTask.CANCELLED)) {
            byStatus.put(s, count(published, s));
        }
        result.put("byStatus", byStatus);
        result.put("draftTasks", count(all, RespTask.DRAFT));

        List<RespTask> overdue = published.stream().filter(t -> t.isOpenForWork() && t.getDueDate() != null && t.getDueDate().isBefore(today))
                .sorted(Comparator.comparing(RespTask::getDueDate)).toList();
        result.put("overdueCount", overdue.size());
        result.put("overdue", overdue.stream().limit(10).map(this::brief).toList());

        List<RespTask> waiting = published.stream().filter(t -> RespTask.PENDING_ACCEPT.equals(t.getStatus()) || RespTask.NEGOTIATING.equals(t.getStatus()))
                .sorted(Comparator.comparing(t -> t.getAssignedAt() == null ? Instant.EPOCH : t.getAssignedAt())).toList();
        result.put("waitingAccept", waiting.stream().limit(10).map(t -> {
            Map<String, Object> m = brief(t);
            m.put("hoursWaiting", t.getAssignedAt() == null ? null : Duration.between(t.getAssignedAt(), now).toHours());
            m.put("objection", t.getObjectionNote());
            return m;
        }).toList());

        List<RespTask> blocked = published.stream().filter(t -> RespTask.BLOCKED.equals(t.getStatus())).toList();
        result.put("blocked", blocked.stream().map(t -> {
            Map<String, Object> m = brief(t);
            m.put("reason", t.getBlockedReason());
            m.put("hours", t.getBlockedSince() == null ? 0 : Duration.between(t.getBlockedSince(), now).toHours());
            m.put("waitingOn", t.getWaitingOnUserId());
            return m;
        }).toList());
        result.put("blockedOver2Days", blocked.stream().filter(t -> t.getBlockedSince() != null
                && Duration.between(t.getBlockedSince(), now).toHours() >= 48).count());

        List<RespTask> review = published.stream().filter(t -> RespTask.PENDING_REVIEW.equals(t.getStatus()))
                .sorted(Comparator.comparing(t -> t.getSubmittedAt() == null ? Instant.EPOCH : t.getSubmittedAt())).toList();
        result.put("pendingReview", review.stream().limit(10).map(brief -> {
            Map<String, Object> m = brief(brief);
            m.put("hoursWaiting", brief.getSubmittedAt() == null ? null : Duration.between(brief.getSubmittedAt(), now).toHours());
            return m;
        }).toList());

        result.put("load", load(published));
        result.put("week", weekBlock(published, today));
        result.put("metrics", metrics(all, live, published));
        return result;
    }

    // ------------------------------------------------------------------ 内部

    private List<Map<String, Object>> load(List<RespTask> published) {
        Map<Long, List<RespTask>> byPerson = published.stream().filter(t -> t.getResponsibleUserId() != null
                && (RespTask.PENDING_ACCEPT.equals(t.getStatus()) || RespTask.IN_PROGRESS.equals(t.getStatus()) || RespTask.BLOCKED.equals(t.getStatus())))
                .collect(Collectors.groupingBy(RespTask::getResponsibleUserId));
        List<Map<String, Object>> rows = new ArrayList<>();
        byPerson.forEach((user, list) -> {
            long high = list.stream().filter(t -> "HIGH".equals(t.getPriority()) || "URGENT".equals(t.getPriority())).count();
            Map<String, Object> m = new LinkedHashMap<>();
            m.put("userId", user);
            m.put("open", list.size());
            m.put("highPriority", high);
            m.put("overloaded", high >= 3 || list.size() >= 8);
            rows.add(m);
        });
        rows.sort((a, b) -> Integer.compare((int) b.get("open"), (int) a.get("open")));
        return rows;
    }

    private Map<String, Object> weekBlock(List<RespTask> published, LocalDate today) {
        LocalDate start = today.with(TemporalAdjusters.previousOrSame(DayOfWeek.MONDAY));
        LocalDate end = start.plusDays(6);
        List<RespTask> due = published.stream().filter(t -> !RespTask.CANCELLED.equals(t.getStatus()) && t.getDueDate() != null
                && !t.getDueDate().isBefore(start) && !t.getDueDate().isAfter(end)).toList();
        long done = due.stream().filter(t -> RespTask.DONE.equals(t.getStatus())).count();
        long onTime = due.stream().filter(t -> RespTask.DONE.equals(t.getStatus()) && t.getCompletedAt() != null
                && !t.getCompletedAt().atZone(BEIJING).toLocalDate().isAfter(t.getDueDate())).count();
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("from", start.toString());
        m.put("to", end.toString());
        m.put("due", due.size());
        m.put("done", done);
        m.put("doneOnTime", onTime);
        m.put("rate", due.isEmpty() ? null : Math.round(done * 1000.0 / due.size()) / 10.0);
        return m;
    }

    private Object weekRate(List<RespTask> team, LocalDate today) {
        return weekBlock(team, today).get("rate");
    }

    private Map<String, Object> metrics(List<RespTask> all, List<RespTask> live, List<RespTask> published) {
        Map<String, Object> m = new LinkedHashMap<>();
        // 接受责任的中位时间（小时）
        List<Double> acceptHours = published.stream().filter(t -> t.getAcceptedAt() != null && t.getAssignedAt() != null)
                .map(t -> Duration.between(t.getAssignedAt(), t.getAcceptedAt()).toMinutes() / 60.0).toList();
        m.put("acceptMedianHours", median(acceptHours));
        // 按时提交率：首次提交不晚于截止日
        List<RespTask> submitted = published.stream().filter(t -> t.getFirstSubmittedAt() != null && t.getDueDate() != null).toList();
        m.put("submittedCount", submitted.size());
        m.put("onTimeSubmitRate", rate(submitted.stream().filter(t -> !t.getFirstSubmittedAt().atZone(BEIJING).toLocalDate().isAfter(t.getDueDate())).count(), submitted.size()));
        // 首次验收通过率、平均退回次数
        List<RespTask> done = published.stream().filter(t -> RespTask.DONE.equals(t.getStatus())).toList();
        m.put("doneCount", done.size());
        m.put("firstPassRate", rate(done.stream().filter(t -> t.getReworkCount() == 0).count(), done.size()));
        m.put("avgReworks", done.isEmpty() ? null : Math.round(done.stream().mapToInt(RespTask::getReworkCount).sum() * 100.0 / done.size()) / 100.0);
        // 无主 / 无验收标准比例（含草稿：这是 AI 草稿暴露出来的缺口）
        m.put("ownerlessRatio", rate(live.stream().filter(t -> t.getResponsibleUserId() == null).count(), live.size()));
        m.put("noCriteriaRatio", rate(live.stream().filter(t -> t.getAcceptanceCriteria() == null || t.getAcceptanceCriteria().isBlank()).count(), live.size()));

        Set<Long> planIds = all.stream().map(RespTask::getPlanId).collect(Collectors.toSet());
        List<RespPlan> planList = plans.findAllById(planIds);
        m.put("missingFoundBeforePublish", planList.stream().mapToInt(RespPlan::getMissingAtCreation).sum());
        List<Double> publishHours = planList.stream().filter(p -> p.getPublishedAt() != null)
                .map(p -> Duration.between(p.getCreatedAt(), p.getPublishedAt()).toMinutes() / 60.0).toList();
        m.put("planToPublishMedianHours", median(publishHours));

        List<RespEvent> evs = planIds.isEmpty() ? List.of()
                : events.findByPlansAndTypes(planIds, List.of("PUBLISHED", "REVISED", "BLOCKED", "UNBLOCKED"));
        m.put("changeCount", evs.stream().filter(e -> "REVISED".equals(e.getEventType())).count());
        long aiPresent = 0, aiMatched = 0;
        for (RespEvent e : evs) {
            if (!"PUBLISHED".equals(e.getEventType()) || e.getDetailJson() == null) {
                continue;
            }
            try {
                JsonNode n = mapper.readTree(e.getDetailJson());
                if (n.path("aiPresent").asBoolean(false)) {
                    aiPresent++;
                    if (n.path("aiMatched").asBoolean(false)) {
                        aiMatched++;
                    }
                }
            } catch (Exception ignored) {
                // 损坏的事件明细不影响统计
            }
        }
        m.put("aiMatchRate", rate(aiMatched, aiPresent));
        m.put("aiMatchSamples", aiPresent);
        Map<Long, Instant> blockedAt = new HashMap<>();
        List<Double> responseHours = new ArrayList<>();
        for (RespEvent e : evs) {
            if ("BLOCKED".equals(e.getEventType()) && e.getTaskId() != null) {
                blockedAt.put(e.getTaskId(), e.getCreatedAt());
            } else if ("UNBLOCKED".equals(e.getEventType()) && e.getTaskId() != null && blockedAt.containsKey(e.getTaskId())) {
                responseHours.add(Duration.between(blockedAt.remove(e.getTaskId()), e.getCreatedAt()).toMinutes() / 60.0);
            }
        }
        m.put("blockedResolveAvgHours", responseHours.isEmpty() ? null
                : Math.round(responseHours.stream().mapToDouble(Double::doubleValue).average().orElse(0) * 10.0) / 10.0);
        return m;
    }

    private Map<String, Object> brief(RespTask t) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("id", t.getId());
        m.put("planId", t.getPlanId());
        m.put("title", t.getTitle());
        m.put("responsibleUserId", t.getResponsibleUserId());
        m.put("reviewerUserId", t.getReviewerUserId());
        m.put("dueDate", t.getDueDate() == null ? null : t.getDueDate().toString());
        m.put("status", t.getStatus());
        m.put("priority", t.getPriority());
        return m;
    }

    private static long count(List<RespTask> list, String status) {
        return list.stream().filter(t -> status.equals(t.getStatus())).count();
    }

    private static Object rate(long part, long total) {
        return total == 0 ? null : Math.round(part * 1000.0 / total) / 10.0;
    }

    private static Object median(List<Double> values) {
        if (values.isEmpty()) {
            return null;
        }
        List<Double> sorted = values.stream().sorted().toList();
        int n = sorted.size();
        double m = n % 2 == 1 ? sorted.get(n / 2) : (sorted.get(n / 2 - 1) + sorted.get(n / 2)) / 2.0;
        return Math.round(m * 10.0) / 10.0;
    }
}
