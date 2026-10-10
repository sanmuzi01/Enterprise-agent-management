package com.enterprisehub.it.dto;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;

import java.time.Instant;
import java.util.List;

/** IT 服务台的请求与响应结构。 */
public final class ItDtos {
    private ItDtos() {
    }

    // ---------------- 响应 ----------------

    public record CommentDto(long id, long authorUserId, boolean internal, String kind, String body, Instant createdAt) {
    }

    /** 列表行：不带描述与评论。slaStatus：OK / AT_RISK / BREACHED / MET / PAUSED / NONE。 */
    public record TicketSummary(long id, long requesterUserId, long teamId, String category, String priority,
                                String title, String status, Long assigneeUserId, Instant createdAt, Instant slaDueAt,
                                String slaStatus, Instant updatedAt) {
    }

    public record TicketDto(long id, long requesterUserId, long teamId, String category, String priority, String title,
                            String description, String status, Long assigneeUserId, Long approverUserId,
                            String decisionNote, String suggestedCategory, String suggestedPriority,
                            String classifyReason, Instant createdAt, Instant updatedAt, Instant firstResponseAt,
                            Instant slaDueAt, String slaStatus, Instant resolvedAt, String resolution, Instant closedAt,
                            int reopenCount, List<CommentDto> comments) {
    }

    public record Classification(String category, String priority, List<String> reasons) {
    }

    public record KbSuggestion(long id, String category, String title, String steps, int score) {
    }

    public record DeviceEventDto(String eventType, long actorUserId, Long subjectUserId, Long ticketId, String note,
                                 Instant createdAt) {
    }

    public record DeviceDto(long id, String assetNo, String deviceType, String model, String status,
                            Long assigneeUserId, long managingTeamId, String purchasedOn, String warrantyUntil,
                            String warrantyStatus, String note, List<DeviceEventDto> events) {
    }

    // ---------------- 请求 ----------------

    public record CreateTicket(@NotBlank String category, String priority, @NotBlank @Size(max = 120) String title,
                               @NotBlank @Size(max = 4000) String description) {
    }

    public record Note(@Size(max = 500) String note) {
    }

    public record Comment(@NotBlank @Size(max = 2000) String body, boolean internal) {
    }

    public record Reason(@NotBlank @Size(min = 2, max = 500) String reason) {
    }

    public record Assign(Long assigneeUserId) {
    }

    public record ChangeStatus(@NotBlank String status, @Size(max = 500) String note) {
    }

    public record Resolve(@NotBlank @Size(min = 4, max = 1000) String resolution) {
    }

    public record Reclassify(@NotBlank String category, @NotBlank String priority,
                             @NotBlank @Size(min = 2, max = 200) String reason) {
    }

    public record CreateDevice(@NotBlank @Size(max = 40) String assetNo, @NotBlank String deviceType,
                               @NotBlank @Size(max = 100) String model, long managingTeamId, String purchasedOn,
                               String warrantyUntil, @Size(max = 300) String note) {
    }

    public record AssignDevice(long userId, Long ticketId, @Size(max = 300) String note) {
    }

    public record DeviceNote(@Size(max = 300) String note) {
    }
}
