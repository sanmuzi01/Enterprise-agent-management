package com.enterprisehub.responsibility;

import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;

import java.time.Instant;
import java.util.List;

/** 责任协同的请求与响应结构。 */
public final class RespDtos {
    private RespDtos() {
    }

    /** FastAPI 计算的"此刻可以被指派的人"：memberIds 可做主责/协办，reviewerIds 可做验收人（含企业管理员）。 */
    public record Eligible(List<Long> memberIds, List<Long> reviewerIds) {
        public boolean canWork(Long userId) {
            return memberIds != null && memberIds.contains(userId);
        }

        public boolean canReview(Long userId) {
            return reviewerIds != null && reviewerIds.contains(userId);
        }
    }

    public record Issue(String code, String level, String message) {
    }

    public record Decision(@Size(max = 300) String content, @Size(max = 500) String evidence) {
    }

    public record TaskInput(@NotBlank @Size(max = 160) String title, Long responsibleUserId, List<Long> collaboratorUserIds,
                            Long reviewerUserId, String dueDate, @Size(max = 300) String deliverable,
                            @Size(max = 500) String acceptanceCriteria, String priority, @Size(max = 500) String evidence,
                            List<Integer> dependsOnSeq, Long aiResponsibleUserId) {
    }

    public record CreatePlan(long teamId, @NotBlank @Size(max = 160) String title, String sourceType, String sourceText,
                             @Size(max = 1000) String summary, List<@Valid Decision> decisions, List<@Size(max = 300) String> unresolved,
                             @Valid List<@Valid TaskInput> tasks, @Size(max = 40) String automationWorkId, Eligible eligible) {
    }

    public record EditPlan(@Size(max = 160) String title, @Size(max = 1000) String summary,
                           List<@Size(max = 300) String> unresolved) {
    }

    public record EditTask(@Valid TaskInput task, Eligible eligible) {
    }

    public record AddTask(@Valid TaskInput task, Eligible eligible) {
    }

    public record Publish(Eligible eligible, @Size(max = 500) String note) {
    }

    /** 责任上的各类动作共用一个请求体，各动作只读自己需要的字段。 */
    public record TaskAction(@Size(max = 500) String note, @Size(max = 500) String reason, Integer percent, String proposedDate,
                             Boolean approve, Long waitingOnUserId, @Size(max = 1000) String summary, @Size(max = 500) String link,
                             Long responsibleUserId, Long reviewerUserId, String dueDate, @Size(max = 300) String deliverable,
                             @Size(max = 500) String acceptanceCriteria, List<Long> collaboratorUserIds, String priority,
                             Eligible eligible) {
    }

    public record EventDto(long id, Long taskId, String type, long actorUserId, String note, String detail, Instant createdAt) {
    }

    public record DeliverableDto(long id, int submissionNo, String summary, String link, long submittedBy, Instant submittedAt) {
    }

    public record TaskDto(long id, long planId, String planTitle, long teamId, int seq, String title, String status,
                          Long responsibleUserId, List<Long> collaboratorUserIds, Long reviewerUserId, Long assignedByUserId,
                          String deliverable, String acceptanceCriteria, String priority, String dueDate, String sourceEvidence,
                          List<Integer> dependsOnSeq, List<Long> dependsOnTaskIds, String blockedReason, Instant blockedSince,
                          Long waitingOnUserId, String lastProgress, Integer progressPercent, Instant lastProgressAt,
                          String objectionNote, String pendingDueDate, String extensionReason, String transferNote, int reworkCount,
                          Long aiResponsibleUserId, Instant assignedAt, Instant acceptedAt, Instant submittedAt, Instant verifiedAt,
                          Instant completedAt, boolean overdue, List<Issue> issues, List<String> myRoles, List<String> myActions) {
    }

    public record TaskDetail(TaskDto task, List<DeliverableDto> deliverables, List<EventDto> events, String sourceText,
                             String planStatus, long planCreatedBy) {
    }

    public record PlanSummary(long id, long teamId, String title, String sourceType, String status, long createdBy,
                              int taskCount, int doneCount, int openCount, int issueCount, Instant createdAt, Instant publishedAt) {
    }

    public record PlanDto(long id, long teamId, String title, String sourceType, String summary, List<Decision> decisions,
                          List<String> unresolved, String status, long createdBy, Long publishedBy, String sourceText,
                          List<TaskDto> tasks, List<EventDto> events, boolean canPublish, int blockerCount, int warningCount,
                          int missingAtCreation, Instant createdAt, Instant publishedAt, boolean canEdit, boolean isHead) {
    }
}
