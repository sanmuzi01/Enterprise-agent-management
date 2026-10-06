package com.enterprisehub.responsibility;

import com.enterprisehub.responsibility.RespDtos.Issue;
import org.springframework.http.HttpStatus;
import org.springframework.web.server.ResponseStatusException;

import java.time.LocalDate;
import java.time.ZoneId;
import java.time.format.DateTimeParseException;
import java.util.ArrayList;
import java.util.List;
import java.util.Set;

/** 责任事项的确定性规则：完整性检查（缺主责人/期限/交付物/验收标准…）与输入归一化。没有模型参与。 */
public final class RespRules {
    public static final ZoneId BEIJING = ZoneId.of("Asia/Shanghai");
    public static final Set<String> PRIORITIES = Set.of("LOW", "NORMAL", "HIGH", "URGENT");
    public static final Set<String> SOURCE_TYPES = Set.of("MEETING", "CHAT", "EMAIL", "NOTICE", "OTHER");
    private static final List<String> VAGUE = List.of("相关工作", "做好", "配合工作", "跟进一下", "处理一下", "推进一下");

    private RespRules() {
    }

    public static LocalDate today() {
        return LocalDate.now(BEIJING);
    }

    public static LocalDate parseDate(String raw, String label) {
        if (raw == null || raw.isBlank()) {
            return null;
        }
        try {
            return LocalDate.parse(raw.trim());
        } catch (DateTimeParseException e) {
            throw badRequest(label + "格式应为 YYYY-MM-DD");
        }
    }

    public static String priority(String raw) {
        if (raw == null || raw.isBlank()) {
            return "NORMAL";
        }
        String value = raw.trim().toUpperCase();
        if (!PRIORITIES.contains(value)) {
            throw badRequest("未知的优先级: " + raw);
        }
        return value;
    }

    public static String sourceType(String raw) {
        if (raw == null || raw.isBlank()) {
            return "OTHER";
        }
        String value = raw.trim().toUpperCase();
        if (!SOURCE_TYPES.contains(value)) {
            throw badRequest("未知的材料类型: " + raw);
        }
        return value;
    }

    public static String blankToNull(String text) {
        return text == null || text.isBlank() ? null : text.trim();
    }

    /** 链接只允许 http/https，避免把 javascript: 之类的内容当链接渲染。 */
    public static String safeLink(String link) {
        String value = blankToNull(link);
        if (value != null && !value.matches("(?i)^https?://\\S+$")) {
            throw badRequest("交付物链接必须以 http:// 或 https:// 开头");
        }
        return value;
    }

    public static String require(String text, int minLength, String message) {
        String value = blankToNull(text);
        if (value == null || value.length() < minLength) {
            throw badRequest(message);
        }
        return value;
    }

    /**
     * 发布前必须满足（BLOCK）与建议关注（WARN）的问题。草稿阶段允许缺失，发布时 BLOCK 一律拒绝。
     * dependencyDueDates：前置任务的截止日；heavyCount：同一主责人在同一周已有的高优先级未结责任数。
     */
    public static List<Issue> issues(RespTask t, List<LocalDate> dependencyDueDates, int heavyCount) {
        List<Issue> list = new ArrayList<>();
        if (t.getResponsibleUserId() == null) {
            list.add(new Issue("NO_RESPONSIBLE", "BLOCK", "还没有主责员工（原文没有明确，或姓名没有匹配到唯一的人）"));
        }
        if (t.getDueDate() == null) {
            list.add(new Issue("NO_DUE", "BLOCK", "没有截止日期"));
        } else if (t.getDueDate().isBefore(today())) {
            list.add(new Issue("DUE_PAST", "BLOCK", "截止日期 " + t.getDueDate() + " 已经过了"));
        }
        if (blankToNull(t.getDeliverable()) == null) {
            list.add(new Issue("NO_DELIVERABLE", "BLOCK", "没有写明交付物"));
        }
        if (blankToNull(t.getAcceptanceCriteria()) == null) {
            list.add(new Issue("NO_CRITERIA", "BLOCK", "没有验收标准"));
        }
        if (t.getReviewerUserId() == null) {
            list.add(new Issue("NO_REVIEWER", "BLOCK", "没有验收人"));
        } else if (t.getReviewerUserId().equals(t.getResponsibleUserId())) {
            list.add(new Issue("SELF_REVIEW", "BLOCK", "主责人不能验收自己的成果，请另选验收人"));
        }
        if (t.getDueDate() != null) {
            for (LocalDate dep : dependencyDueDates) {
                if (dep != null && t.getDueDate().isBefore(dep)) {
                    list.add(new Issue("DUE_BEFORE_DEPENDENCY", "BLOCK", "截止日期 " + t.getDueDate() + " 早于它依赖的前置事项（" + dep + "）"));
                    break;
                }
            }
        }
        String title = t.getTitle() == null ? "" : t.getTitle().trim();
        if (title.length() < 4 || VAGUE.stream().anyMatch(title::contains)) {
            list.add(new Issue("VAGUE_TITLE", "WARN", "事项表述比较空泛，建议写成可执行、可检查的动作"));
        }
        if (heavyCount >= 2 && ("HIGH".equals(t.getPriority()) || "URGENT".equals(t.getPriority()))) {
            list.add(new Issue("OVERLOAD", "WARN", "主责人同一周已有 " + heavyCount + " 项高优先级责任，请确认是否过载"));
        }
        return list;
    }

    public static ResponseStatusException badRequest(String message) {
        return new ResponseStatusException(HttpStatus.BAD_REQUEST, message);
    }

    public static ResponseStatusException notFound(String message) {
        return new ResponseStatusException(HttpStatus.NOT_FOUND, message);
    }

    public static ResponseStatusException forbidden(String message) {
        return new ResponseStatusException(HttpStatus.FORBIDDEN, message);
    }
}
