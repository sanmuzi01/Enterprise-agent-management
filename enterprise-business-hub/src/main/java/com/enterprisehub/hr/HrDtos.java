package com.enterprisehub.hr;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;

import java.time.Instant;
import java.util.List;

/** 人事事项的请求与响应结构。 */
public final class HrDtos {
    private HrDtos() {
    }

    public record TaskDto(long id, int seq, String title, String owner, boolean required, String status, String dueDate,
                          boolean overdue, Long doneBy, Instant doneAt, String note) {
    }

    public record CaseSummary(long id, String caseType, String caseTypeLabel, long employeeUserId, long teamId,
                              Long targetTeamId, String effectiveDate, String status, int openTasks, int totalTasks,
                              boolean effectPending, Instant createdAt) {
    }

    public record CaseDto(long id, String caseType, String caseTypeLabel, long employeeUserId, long teamId,
                          Long targetTeamId, String position, String effectiveDate, String reason, String status,
                          long initiatorUserId, Long approverUserId, String decisionNote, boolean employeeIsHead,
                          List<HrCheckService.Check> checks, String riskLevel, List<TaskDto> tasks, boolean effectPending,
                          Instant createdAt, Instant updatedAt, Instant completedAt, List<String> myRoles) {
    }

    public record MyTask(long taskId, long caseId, String caseType, String caseTypeLabel, long employeeUserId, long teamId,
                         String title, String owner, String dueDate, boolean overdue) {
    }

    public record CreateCase(@NotBlank String caseType, long employeeUserId, long teamId, Long targetTeamId,
                             @Size(max = 80) String position, @NotBlank String effectiveDate,
                             @Size(max = 500) String reason, boolean employeeIsHead) {
    }

    public record Note(@Size(max = 500) String note) {
    }
}
