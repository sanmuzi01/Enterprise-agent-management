package com.enterprisehub.it;

import com.enterprisehub.audit.AuditService;
import com.enterprisehub.it.dto.ItDtos.*;
import com.enterprisehub.security.TeamAccessGuard;
import jakarta.persistence.criteria.Predicate;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.domain.Sort;
import org.springframework.data.jpa.domain.Specification;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;

import java.time.Duration;
import java.time.Instant;
import java.time.LocalDate;
import java.time.ZoneId;
import java.util.*;
import java.util.stream.Collectors;

/**
 * IT 工单闭环：员工提交（账号/权限/设备申请先由部门负责人批准）→ IT 人员接单、处理、等待用户补充、解决 →
 * 申请人确认关闭或重新打开。SLA 按优先级计时，等待用户期间暂停；所有状态变更都留痕并写审计。
 *
 * 权限分层：scope（it.ticket.* 给所有员工，it.desk.* 只给 IT 部门成员，由 FastAPI 判断后签发）；
 * IT 台操作用 scopeTeamIds（同一企业的部门，写在已签名路径里）限定范围；
 * 员工侧只能碰自己的工单，审批只能由申请人所在部门的负责人/企业管理员，且不能审批自己的。
 * IT 人员不能处理自己提交的工单（制单与处理分离）。
 */
@Service
public class ItTicketService {
    private static final ZoneId BEIJING = ZoneId.of("Asia/Shanghai");
    private static final Set<TicketStatus> CANCELLABLE = EnumSet.of(TicketStatus.PENDING_APPROVAL, TicketStatus.OPEN,
            TicketStatus.IN_PROGRESS, TicketStatus.WAITING_USER);

    private final ItTicketRepository tickets;
    private final ItTicketCommentRepository comments;
    private final ItKbArticleRepository kb;
    private final TicketClassifier classifier;
    private final AuditService audit;

    public ItTicketService(ItTicketRepository tickets, ItTicketCommentRepository comments, ItKbArticleRepository kb,
                           TicketClassifier classifier, AuditService audit) {
        this.tickets = tickets;
        this.comments = comments;
        this.kb = kb;
        this.classifier = classifier;
        this.audit = audit;
    }

    // ================================================================ 员工侧

    @Transactional
    public TicketDto create(long userId, long teamId, CreateTicket request, String traceId) {
        TicketCategory category = parseCategory(request.category());
        TicketPriority priority = request.priority() == null || request.priority().isBlank()
                ? TicketPriority.NORMAL : parsePriority(request.priority());
        String title = request.title().trim();
        String description = request.description().trim();
        if (title.isEmpty() || description.length() < 4) {
            throw badRequest("请写清标题和问题描述");
        }
        List<ItTicket> duplicates = tickets.findOpenByTitle(userId, title, EnumSet.of(TicketStatus.PENDING_APPROVAL,
                TicketStatus.OPEN, TicketStatus.IN_PROGRESS, TicketStatus.WAITING_USER));
        if (!duplicates.isEmpty()) {
            throw badRequest("你已有标题相同且未结束的工单 #" + duplicates.get(0).getId() + "，请在原工单里补充信息");
        }
        ItTicket ticket = new ItTicket(userId, teamId, category, priority, title, description);
        Classification suggestion = classifier.classify(title + " " + description);
        ticket.setSuggestion(suggestion.category(), suggestion.priority(), clip(String.join("；", suggestion.reasons()), 300));
        tickets.save(ticket);
        comments.save(new ItTicketComment(ticket.getId(), userId, false, "SYSTEM", category.needsApproval()
                ? "已提交，等待所在部门负责人批准后由 IT 处理" : "已提交，等待 IT 接单"));
        audit.record(userId, "it.ticket_created", "it_ticket", ticket.getId(),
                "{\"category\":\"" + category + "\",\"priority\":\"" + priority + "\"}", traceId);
        return toDto(ticket, true);
    }

    @Transactional(readOnly = true)
    public List<TicketSummary> listMine(long userId) {
        return tickets.findByRequesterUserIdOrderByIdDesc(userId).stream().map(this::summary).toList();
    }

    /** 申请人本人，或申请人所在部门的负责人/企业管理员可看；申请人和审批人看不到内部备注。 */
    @Transactional(readOnly = true)
    public TicketDto getVisible(long id, long userId, Long callerTeamId, boolean isOrgAdmin, boolean isTeamAdmin) {
        ItTicket ticket = tickets.findById(id).orElseThrow(() -> notFound("工单不存在"));
        TeamAccessGuard.requireOwnerOrTeamAccess(ticket.getRequesterUserId(), userId, callerTeamId, isOrgAdmin,
                isTeamAdmin, ticket.getTeamId());
        return toDto(ticket, false);
    }

    @Transactional
    public TicketDto cancel(long id, long userId, String traceId) {
        ItTicket ticket = lockOwned(id, userId);
        if (!CANCELLABLE.contains(ticket.getStatus())) {
            throw badRequest("当前状态的工单不能撤销: " + ticket.getStatus());
        }
        resumeIfWaiting(ticket);
        ticket.setStatus(TicketStatus.CANCELLED);
        comments.save(new ItTicketComment(id, userId, false, "STATUS", "申请人撤销了工单"));
        audit.record(userId, "it.ticket_cancelled", "it_ticket", id, null, traceId);
        return toDto(ticket, false);
    }

    @Transactional
    public TicketDto comment(long id, long userId, String body, String traceId) {
        ItTicket ticket = lockOwned(id, userId);
        if (EnumSet.of(TicketStatus.CLOSED, TicketStatus.CANCELLED, TicketStatus.REJECTED).contains(ticket.getStatus())) {
            throw badRequest("工单已结束，不能再补充；需要的话请新建工单");
        }
        comments.save(new ItTicketComment(id, userId, false, "COMMENT", body.trim()));
        if (ticket.getStatus() == TicketStatus.WAITING_USER) {
            resumeIfWaiting(ticket);
            ticket.setStatus(ticket.getAssigneeUserId() != null ? TicketStatus.IN_PROGRESS : TicketStatus.OPEN);
        }
        audit.record(userId, "it.ticket_commented", "it_ticket", id, null, traceId);
        return toDto(ticket, false);
    }

    @Transactional
    public TicketDto confirm(long id, long userId, String traceId) {
        ItTicket ticket = lockOwned(id, userId);
        if (ticket.getStatus() != TicketStatus.RESOLVED) {
            throw badRequest("只有已解决的工单能确认关闭，当前状态: " + ticket.getStatus());
        }
        ticket.close();
        comments.save(new ItTicketComment(id, userId, false, "STATUS", "申请人确认问题已解决，工单关闭"));
        audit.record(userId, "it.ticket_closed", "it_ticket", id, null, traceId);
        return toDto(ticket, false);
    }

    @Transactional
    public TicketDto reopen(long id, long userId, String reason, String traceId) {
        ItTicket ticket = lockOwned(id, userId);
        if (ticket.getStatus() != TicketStatus.RESOLVED) {
            throw badRequest("只有已解决、尚未确认的工单能重新打开，当前状态: " + ticket.getStatus());
        }
        ticket.reopen();
        comments.save(new ItTicketComment(id, userId, false, "STATUS", "申请人认为问题没有解决，重新打开：" + reason.trim()));
        audit.record(userId, "it.ticket_reopened", "it_ticket", id, reason.trim(), traceId);
        return toDto(ticket, false);
    }

    @Transactional
    public TicketDto approve(long id, long approverId, String note, String traceId, Long callerTeamId,
                             boolean isOrgAdmin, boolean isTeamAdmin) {
        ItTicket ticket = lockPendingApproval(id, approverId, callerTeamId, isOrgAdmin, isTeamAdmin);
        ticket.approve(approverId, note);
        comments.save(new ItTicketComment(id, approverId, false, "STATUS",
                "部门负责人已批准" + (note == null || note.isBlank() ? "" : "：" + note.trim()) + "，等待 IT 接单"));
        audit.record(approverId, "it.ticket_approved", "it_ticket", id, note, traceId);
        return toDto(ticket, false);
    }

    @Transactional
    public TicketDto reject(long id, long approverId, String note, String traceId, Long callerTeamId,
                            boolean isOrgAdmin, boolean isTeamAdmin) {
        ItTicket ticket = lockPendingApproval(id, approverId, callerTeamId, isOrgAdmin, isTeamAdmin);
        ticket.reject(approverId, note);
        comments.save(new ItTicketComment(id, approverId, false, "STATUS",
                "部门负责人未批准" + (note == null || note.isBlank() ? "" : "：" + note.trim())));
        audit.record(approverId, "it.ticket_rejected", "it_ticket", id, note, traceId);
        return toDto(ticket, false);
    }

    @Transactional(readOnly = true)
    public List<TicketSummary> listTeamPending(long teamId, Long callerTeamId, boolean isOrgAdmin, boolean isTeamAdmin) {
        TeamAccessGuard.requireTeamAccess(callerTeamId, isOrgAdmin, isTeamAdmin, teamId);
        return tickets.findByTeamIdAndStatusOrderByIdDesc(teamId, TicketStatus.PENDING_APPROVAL).stream()
                .map(this::summary).toList();
    }

    // ================================================================ 自助解决方案与分类建议

    @Transactional(readOnly = true)
    public List<KbSuggestion> suggest(String text) {
        String value = text == null ? "" : text.toLowerCase();
        List<KbSuggestion> result = new ArrayList<>();
        for (ItKbArticle article : kb.findByEnabledTrueOrderById()) {
            int score = (int) Arrays.stream(article.getKeywords().split(","))
                    .map(String::trim).filter(k -> !k.isEmpty() && value.contains(k.toLowerCase())).count();
            if (score > 0) {
                result.add(new KbSuggestion(article.getId(), article.getCategory(), article.getTitle(), article.getSteps(), score));
            }
        }
        result.sort(Comparator.comparingInt(KbSuggestion::score).reversed());
        return result.stream().limit(3).toList();
    }

    public Classification classify(String text) {
        return classifier.classify(text);
    }

    // ================================================================ IT 台

    @Transactional(readOnly = true)
    public List<TicketSummary> deskList(Collection<Long> scope, String status, String assignee, boolean overdueOnly,
                                        long callerId, int limit) {
        TicketStatus parsedStatus = status == null || status.isBlank() ? null : parseStatus(status);
        Instant now = Instant.now();
        Specification<ItTicket> spec = (root, query, cb) -> {
            List<Predicate> predicates = new ArrayList<>();
            predicates.add(root.get("teamId").in(scope));
            if (parsedStatus != null) {
                predicates.add(cb.equal(root.get("status"), parsedStatus));
            } else {
                // 默认队列只放需要 IT 关注的：不含还在等审批的（还没轮到 IT），也不含已关闭/撤销/未获批准的；这些要看时显式选状态
                predicates.add(cb.not(root.get("status").in(TicketStatus.PENDING_APPROVAL, TicketStatus.CLOSED,
                        TicketStatus.CANCELLED, TicketStatus.REJECTED)));
            }
            if ("me".equals(assignee)) {
                predicates.add(cb.equal(root.get("assigneeUserId"), callerId));
            } else if ("unassigned".equals(assignee)) {
                predicates.add(cb.isNull(root.get("assigneeUserId")));
            } else if (assignee != null && !assignee.isBlank()) {
                predicates.add(cb.equal(root.get("assigneeUserId"), parseLong(assignee)));
            }
            if (overdueOnly) {
                predicates.add(cb.lessThan(root.get("slaDueAt"), now));
                predicates.add(root.get("status").in(TicketStatus.OPEN, TicketStatus.IN_PROGRESS));
            }
            return cb.and(predicates.toArray(new Predicate[0]));
        };
        // 超时最急的排前面：先按 SLA 截止时间升序
        return tickets.findAll(spec, PageRequest.of(0, Math.max(1, Math.min(limit, 200)),
                Sort.by(Sort.Direction.ASC, "slaDueAt"))).getContent().stream().map(this::summary).toList();
    }

    @Transactional(readOnly = true)
    public TicketDto deskGet(long id, Collection<Long> scope) {
        ItTicket ticket = tickets.findById(id).filter(t -> scope.contains(t.getTeamId()))
                .orElseThrow(() -> notFound("工单不存在"));
        return toDto(ticket, true);
    }

    @Transactional
    public TicketDto assign(long id, Long assigneeId, long actorId, Collection<Long> scope, String traceId) {
        ItTicket ticket = lockActive(id, actorId, scope);
        if (assigneeId != null && assigneeId == ticket.getRequesterUserId()) {
            throw badRequest("不能把工单指派给申请人本人");
        }
        ticket.recordFirstResponse();
        ticket.assignTo(assigneeId);
        if (assigneeId == null) {
            if (ticket.getStatus() == TicketStatus.IN_PROGRESS) {
                ticket.setStatus(TicketStatus.OPEN);
            }
            comments.save(new ItTicketComment(id, actorId, true, "SYSTEM", "取消指派，回到待接单"));
        } else {
            if (ticket.getStatus() == TicketStatus.OPEN) {
                ticket.setStatus(TicketStatus.IN_PROGRESS);
            }
            comments.save(new ItTicketComment(id, actorId, false, "SYSTEM",
                    assigneeId == actorId ? "IT 人员已接单，开始处理" : "已指派给 IT 人员 #" + assigneeId + " 处理"));
        }
        audit.record(actorId, "it.ticket_assigned", "it_ticket", id, "{\"assignee\":" + assigneeId + "}", traceId);
        return toDto(ticket, true);
    }

    @Transactional
    public TicketDto changeStatus(long id, String status, String note, long actorId, Collection<Long> scope, String traceId) {
        TicketStatus target = parseStatus(status);
        if (target != TicketStatus.IN_PROGRESS && target != TicketStatus.WAITING_USER) {
            throw badRequest("只能切换到处理中或等待用户；解决请用“解决”操作");
        }
        ItTicket ticket = lockActive(id, actorId, scope);
        if (ticket.getStatus() == target) {
            throw badRequest("工单已经是该状态");
        }
        if (target == TicketStatus.WAITING_USER && (note == null || note.trim().length() < 2)) {
            throw badRequest("等待用户时请写明需要用户补充什么");
        }
        ticket.recordFirstResponse();
        if (target == TicketStatus.WAITING_USER) {
            ticket.enterWaiting();
            comments.save(new ItTicketComment(id, actorId, false, "STATUS", "需要你补充信息：" + note.trim()));
        } else {
            resumeIfWaiting(ticket);
            if (ticket.getAssigneeUserId() == null) {
                ticket.assignTo(actorId);
            }
            ticket.setStatus(TicketStatus.IN_PROGRESS);
            comments.save(new ItTicketComment(id, actorId, true, "SYSTEM", "转为处理中"
                    + (note == null || note.isBlank() ? "" : "：" + note.trim())));
        }
        audit.record(actorId, "it.ticket_status", "it_ticket", id, "{\"to\":\"" + target + "\"}", traceId);
        return toDto(ticket, true);
    }

    @Transactional
    public TicketDto resolve(long id, String resolution, long actorId, Collection<Long> scope, String traceId) {
        ItTicket ticket = lockActive(id, actorId, scope);
        resumeIfWaiting(ticket);
        ticket.recordFirstResponse();
        if (ticket.getAssigneeUserId() == null) {
            ticket.assignTo(actorId);
        }
        ticket.resolve(resolution.trim());
        comments.save(new ItTicketComment(id, actorId, false, "STATUS", "已解决：" + resolution.trim()
                + "。请确认问题是否已解决；没有解决可以重新打开。"));
        audit.record(actorId, "it.ticket_resolved", "it_ticket", id, resolution.trim(), traceId);
        return toDto(ticket, true);
    }

    @Transactional
    public TicketDto deskComment(long id, String body, boolean internal, long actorId, Collection<Long> scope, String traceId) {
        ItTicket ticket = tickets.findForUpdate(id).filter(t -> scope.contains(t.getTeamId()))
                .orElseThrow(() -> notFound("工单不存在"));
        requireNotRequester(ticket, actorId);
        if (EnumSet.of(TicketStatus.CLOSED, TicketStatus.CANCELLED, TicketStatus.REJECTED).contains(ticket.getStatus())) {
            throw badRequest("工单已结束，不能再评论");
        }
        comments.save(new ItTicketComment(id, actorId, internal, "COMMENT", body.trim()));
        if (!internal && ticket.getStatus().isActive()) {
            ticket.recordFirstResponse();
        }
        audit.record(actorId, "it.ticket_desk_comment", "it_ticket", id, internal ? "internal" : "public", traceId);
        return toDto(ticket, true);
    }

    @Transactional
    public TicketDto reclassify(long id, String category, String priority, String reason, long actorId,
                                Collection<Long> scope, String traceId) {
        TicketCategory newCategory = parseCategory(category);
        TicketPriority newPriority = parsePriority(priority);
        ItTicket ticket = lockActive(id, actorId, scope);
        if (newCategory.needsApproval() && !ticket.getCategory().needsApproval()) {
            throw badRequest("该分类需要先由部门负责人批准，请让申请人重新提交工单");
        }
        String before = ticket.getCategory() + "/" + ticket.getPriority();
        ticket.reclassify(newCategory, newPriority);
        comments.save(new ItTicketComment(id, actorId, true, "SYSTEM",
                "调整分类/优先级：" + before + " → " + newCategory + "/" + newPriority + "（" + reason.trim() + "）"));
        audit.record(actorId, "it.ticket_reclassified", "it_ticket", id, before + "->" + newCategory + "/" + newPriority, traceId);
        return toDto(ticket, true);
    }

    /** 服务台汇总：积压、超时、处理人负载、近 30 天的处理效果（首次响应、解决耗时、SLA 达成率、重新打开率）。 */
    @Transactional(readOnly = true)
    public Map<String, Object> deskSummary(Collection<Long> scope, int days) {
        Instant now = Instant.now();
        Instant from = now.minus(Duration.ofDays(Math.max(1, Math.min(days, 365))));
        Map<String, Long> byStatus = new LinkedHashMap<>();
        for (TicketStatus s : TicketStatus.values()) {
            byStatus.put(s.name(), 0L);
        }
        for (Object[] row : tickets.countByStatus(scope)) {
            byStatus.put(((TicketStatus) row[0]).name(), ((Number) row[1]).longValue());
        }
        Map<String, Long> byCategory = new LinkedHashMap<>();
        for (TicketCategory c : TicketCategory.values()) {
            byCategory.put(c.name(), 0L);
        }
        for (Object[] row : tickets.countByCategory(scope, from, now)) {
            byCategory.put(((TicketCategory) row[0]).name(), ((Number) row[1]).longValue());
        }
        long resolved = 0;
        long met = 0;
        double resolveHours = 0;
        double responseHours = 0;
        int responded = 0;
        // MySQL DATETIME 只有秒精度，写入时会四舍五入：刚解决的工单 resolved_at 可能比 now 晚不到一秒，上界要留出余量
        for (Object[] row : tickets.resolvedTimings(scope, from, now.plusSeconds(2))) {
            Instant created = (Instant) row[0];
            Instant first = (Instant) row[1];
            Instant done = (Instant) row[2];
            Instant due = (Instant) row[3];
            resolved++;
            resolveHours += Duration.between(created, done).toSeconds() / 3600.0;
            if (first != null) {
                responseHours += Duration.between(created, first).toSeconds() / 3600.0;
                responded++;
            }
            if (!done.isAfter(due)) {
                met++;
            }
        }
        List<Map<String, Object>> load = new ArrayList<>();
        for (Object[] row : tickets.openLoadByAssignee(scope)) {
            Map<String, Object> item = new LinkedHashMap<>();
            item.put("userId", row[0]);
            item.put("open", row[1]);
            load.add(item);
        }
        long unassigned = tickets.count((root, query, cb) -> cb.and(root.get("teamId").in(scope),
                cb.equal(root.get("status"), TicketStatus.OPEN), cb.isNull(root.get("assigneeUserId"))));
        long reopened = tickets.count((root, query, cb) -> cb.and(root.get("teamId").in(scope),
                cb.greaterThan(root.get("reopenCount"), 0), cb.greaterThanOrEqualTo(root.get("createdAt"), from)));
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("days", days);
        result.put("byStatus", byStatus);
        result.put("byCategory", byCategory);
        result.put("unassigned", unassigned);
        result.put("overdue", tickets.countOverdue(scope, now));
        result.put("load", load);
        Map<String, Object> effect = new LinkedHashMap<>();
        effect.put("resolved", resolved);
        effect.put("slaMetRate", resolved == 0 ? null : round1(met * 100.0 / resolved));
        effect.put("avgResolveHours", resolved == 0 ? null : round1(resolveHours / resolved));
        effect.put("avgFirstResponseHours", responded == 0 ? null : round1(responseHours / responded));
        effect.put("reopenedTickets", reopened);
        result.put("effect", effect);
        return result;
    }

    // ================================================================ 内部

    private ItTicket lockOwned(long id, long userId) {
        ItTicket ticket = tickets.findForUpdate(id).orElseThrow(() -> notFound("工单不存在"));
        if (ticket.getRequesterUserId() != userId) {
            throw notFound("工单不存在");
        }
        return ticket;
    }

    private ItTicket lockPendingApproval(long id, long approverId, Long callerTeamId, boolean isOrgAdmin, boolean isTeamAdmin) {
        ItTicket ticket = tickets.findForUpdate(id).orElseThrow(() -> notFound("工单不存在"));
        TeamAccessGuard.requireTeamAccess(callerTeamId, isOrgAdmin, isTeamAdmin, ticket.getTeamId());
        if (ticket.getStatus() != TicketStatus.PENDING_APPROVAL) {
            throw badRequest("只有待批准的工单能处理，当前状态: " + ticket.getStatus());
        }
        if (ticket.getRequesterUserId() == approverId) {
            throw badRequest("不能批准自己提交的工单，需要由部门负责人或企业管理员处理");
        }
        return ticket;
    }

    private ItTicket lockActive(long id, long actorId, Collection<Long> scope) {
        ItTicket ticket = tickets.findForUpdate(id).filter(t -> scope.contains(t.getTeamId()))
                .orElseThrow(() -> notFound("工单不存在"));
        requireNotRequester(ticket, actorId);
        if (!ticket.getStatus().isActive()) {
            throw badRequest("只有处理中的工单能这样操作，当前状态: " + ticket.getStatus());
        }
        return ticket;
    }

    private void requireNotRequester(ItTicket ticket, long actorId) {
        if (ticket.getRequesterUserId() == actorId) {
            throw badRequest("不能处理自己提交的工单，请由其他 IT 人员处理");
        }
    }

    /** 离开“等待用户”：把等待的时长从处理时限里扣掉（顺延截止时间）。 */
    private void resumeIfWaiting(ItTicket ticket) {
        if (ticket.getStatus() == TicketStatus.WAITING_USER) {
            ticket.leaveWaiting();
        }
    }

    private String slaStatus(ItTicket t) {
        TicketStatus s = t.getStatus();
        if (s == TicketStatus.PENDING_APPROVAL || s == TicketStatus.CANCELLED || s == TicketStatus.REJECTED) {
            return "NONE";
        }
        if (t.getResolvedAt() != null && (s == TicketStatus.RESOLVED || s == TicketStatus.CLOSED)) {
            return t.getResolvedAt().isAfter(t.getSlaDueAt()) ? "BREACHED" : "MET";
        }
        if (s == TicketStatus.WAITING_USER) {
            return "PAUSED";
        }
        Instant now = Instant.now();
        if (now.isAfter(t.getSlaDueAt())) {
            return "BREACHED";
        }
        long total = Duration.ofHours(t.getPriority().slaHours()).toSeconds();
        return Duration.between(now, t.getSlaDueAt()).toSeconds() < total * 0.25 ? "AT_RISK" : "OK";
    }

    private TicketSummary summary(ItTicket t) {
        return new TicketSummary(t.getId(), t.getRequesterUserId(), t.getTeamId(), t.getCategory().name(),
                t.getPriority().name(), t.getTitle(), t.getStatus().name(), t.getAssigneeUserId(), t.getCreatedAt(),
                t.getSlaDueAt(), slaStatus(t), t.getUpdatedAt());
    }

    private TicketDto toDto(ItTicket t, boolean includeInternal) {
        List<CommentDto> list = comments.findByTicketIdOrderByIdAsc(t.getId()).stream()
                .filter(c -> includeInternal || !c.isInternal())
                .map(c -> new CommentDto(c.getId(), c.getAuthorUserId(), c.isInternal(), c.getKind(), c.getBody(), c.getCreatedAt()))
                .toList();
        return new TicketDto(t.getId(), t.getRequesterUserId(), t.getTeamId(), t.getCategory().name(),
                t.getPriority().name(), t.getTitle(), t.getDescription(), t.getStatus().name(), t.getAssigneeUserId(),
                t.getApproverUserId(), t.getDecisionNote(), t.getSuggestedCategory(), t.getSuggestedPriority(),
                t.getClassifyReason(), t.getCreatedAt(), t.getUpdatedAt(), t.getFirstResponseAt(), t.getSlaDueAt(),
                slaStatus(t), t.getResolvedAt(), t.getResolution(), t.getClosedAt(), t.getReopenCount(), list);
    }

    private static double round1(double value) {
        return Math.round(value * 10.0) / 10.0;
    }

    private static String clip(String text, int max) {
        return text.length() <= max ? text : text.substring(0, max);
    }

    private TicketCategory parseCategory(String raw) {
        try {
            return TicketCategory.valueOf(raw.trim().toUpperCase());
        } catch (IllegalArgumentException e) {
            throw badRequest("未知的工单分类: " + raw);
        }
    }

    private TicketPriority parsePriority(String raw) {
        try {
            return TicketPriority.valueOf(raw.trim().toUpperCase());
        } catch (IllegalArgumentException e) {
            throw badRequest("未知的优先级: " + raw);
        }
    }

    private TicketStatus parseStatus(String raw) {
        try {
            return TicketStatus.valueOf(raw.trim().toUpperCase());
        } catch (IllegalArgumentException e) {
            throw badRequest("未知的工单状态: " + raw);
        }
    }

    private long parseLong(String raw) {
        try {
            return Long.parseLong(raw.trim());
        } catch (NumberFormatException e) {
            throw badRequest("处理人参数不正确");
        }
    }

    static ResponseStatusException badRequest(String message) {
        return new ResponseStatusException(HttpStatus.BAD_REQUEST, message);
    }

    static ResponseStatusException notFound(String message) {
        return new ResponseStatusException(HttpStatus.NOT_FOUND, message);
    }
}
