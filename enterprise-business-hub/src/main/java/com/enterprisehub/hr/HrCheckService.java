package com.enterprisehub.hr;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;

import java.time.LocalDate;
import java.time.ZoneId;
import java.time.temporal.ChronoUnit;
import java.util.ArrayList;
import java.util.List;

/**
 * 人事事项的规则检查：全是确定性规则，直接查业务库里的请假、报销、IT 设备与工单，每一项说明“为什么要看”。
 * BLOCK 不处理就不能提交（或不能办结），WARN 需要办理人确认过，INFO 只是提示。
 * 办结时（forCompletion）更严格：离职时设备没收回、还有未办完的报销，直接阻断。
 */
@Component
public class HrCheckService {
    static final ZoneId BEIJING = ZoneId.of("Asia/Shanghai");

    public record Check(String code, String level, String message) {
    }

    private final JdbcTemplate jdbc;
    private final HrCaseRepository cases;

    public HrCheckService(JdbcTemplate jdbc, HrCaseRepository cases) {
        this.jdbc = jdbc;
        this.cases = cases;
    }

    public List<Check> check(HrCaseType type, long employee, long teamId, Long targetTeamId, LocalDate effective,
                             boolean employeeIsHead, Long excludeCaseId, boolean forCompletion) {
        List<Check> out = new ArrayList<>();
        LocalDate today = LocalDate.now(BEIJING);

        for (HrCase other : cases.findActiveByEmployee(employee)) {
            if (excludeCaseId != null && other.getId().equals(excludeCaseId)) {
                continue;
            }
            if (other.getCaseType() == type) {
                out.add(new Check("DUPLICATE_CASE", "BLOCK", "该员工已有进行中的" + type.label() + "事项 #" + other.getId() + "，不能重复办理"));
            } else if (other.getCaseType() == HrCaseType.OFFBOARDING && type != HrCaseType.OFFBOARDING) {
                out.add(new Check("OFFBOARDING_IN_PROGRESS", "BLOCK", "该员工正在办理离职（事项 #" + other.getId() + "），不能再办理" + type.label()));
            } else {
                out.add(new Check("OTHER_CASE_ACTIVE", "INFO", "该员工还有进行中的" + other.getCaseType().label() + "事项 #" + other.getId()));
            }
        }
        if (effective.isBefore(today.minusDays(30))) {
            out.add(new Check("DATE_PAST", "WARN", "生效日期 " + effective + " 已过去 " + ChronoUnit.DAYS.between(effective, today) + " 天，属于补办，请确认"));
        } else if (effective.isAfter(today.plusDays(365))) {
            out.add(new Check("DATE_FAR", "WARN", "生效日期 " + effective + " 超过一年后，请确认日期是否填错"));
        }

        switch (type) {
            case ONBOARDING -> onboarding(out, employee, teamId, effective);
            case PROBATION -> probation(out, employee, today);
            case TRANSFER -> transfer(out, employee, teamId, targetTeamId, employeeIsHead);
            case OFFBOARDING -> offboarding(out, employee, effective, employeeIsHead, forCompletion);
        }
        return out;
    }

    public static String overall(List<Check> checks) {
        if (checks.stream().anyMatch(c -> "BLOCK".equals(c.level()))) {
            return "BLOCK";
        }
        if (checks.stream().anyMatch(c -> "WARN".equals(c.level()))) {
            return "WARN";
        }
        return checks.isEmpty() ? "NONE" : "INFO";
    }

    private void onboarding(List<Check> out, long employee, long teamId, LocalDate effective) {
        if (!cases.findCompleted(employee, HrCaseType.ONBOARDING).isEmpty()) {
            out.add(new Check("ALREADY_ONBOARDED", "WARN", "该员工已有办结的入职记录，请确认不是重复办理（或是再次入职）"));
        }
        int year = effective.getYear();
        Integer balances = count("SELECT COUNT(*) FROM leave_balance WHERE user_id = ? AND year = ?", employee, year);
        if (balances == 0) {
            out.add(new Check("NO_LEAVE_BALANCE", "WARN", year + " 年度还没有该员工的假期余额，入职后无法请假，办理时请让 OA 初始化余额"));
        }
        Integer budget = count("SELECT COUNT(*) FROM expense_budget WHERE team_id = ? AND year = ?", teamId, year);
        if (budget == 0) {
            out.add(new Check("NO_EXPENSE_BUDGET", "INFO", "所在部门没有 " + year + " 年度的报销预算记录，新员工的报销将无法提交"));
        }
    }

    private void probation(List<Check> out, long employee, LocalDate today) {
        List<HrCase> onboarded = cases.findCompleted(employee, HrCaseType.ONBOARDING);
        if (onboarded.isEmpty()) {
            out.add(new Check("NO_ONBOARDING_RECORD", "INFO", "系统里没有该员工的入职办结记录，无法核对试用期时长"));
        } else {
            LocalDate start = onboarded.get(0).getEffectiveDate();
            long days = ChronoUnit.DAYS.between(start, today);
            if (days < 30) {
                out.add(new Check("SHORT_PROBATION", "WARN", "入职（" + start + "）至今只有 " + Math.max(days, 0) + " 天，不足 30 天，请确认是否提前转正"));
            } else {
                out.add(new Check("TENURE", "INFO", "入职 " + start + "，至今 " + days + " 天"));
            }
        }
        Double sick = jdbc.queryForObject("SELECT COALESCE(SUM(lr.days), 0) FROM leave_request lr JOIN leave_type lt ON lt.id = lr.leave_type_id "
                + "WHERE lr.applicant_user_id = ? AND lt.code = 'sick' AND lr.status = 'APPROVED' AND lr.start_date >= ?", Double.class,
                employee, java.sql.Date.valueOf(today.minusDays(90)));
        if (sick != null && sick >= 10) {
            out.add(new Check("MANY_SICK_DAYS", "WARN", "近 90 天病假累计 " + trim(sick) + " 天，转正评估时请结合考勤核对"));
        }
        pendingLeave(out, employee, "INFO");
    }

    private void transfer(List<Check> out, long employee, long teamId, Long targetTeamId, boolean isHead) {
        if (targetTeamId == null || targetTeamId == teamId) {
            out.add(new Check("TARGET_INVALID", "BLOCK", "调岗必须指定与当前部门不同的目标部门"));
        }
        pendingLeave(out, employee, "WARN");
        pendingClaims(out, employee, "WARN", "报销审批人是原部门负责人，调岗后需要重新提交或由原部门先处理");
        openTickets(out, employee, "WARN");
        assignedDevices(out, employee, "INFO", "名下有设备 %s，请确认设备随人转移还是收回");
        if (isHead) {
            out.add(new Check("EMPLOYEE_IS_HEAD", "WARN", "该员工是原部门负责人，调岗前需要先交接审批权限，否则原部门会出现无人审批"));
        }
    }

    private void offboarding(List<Check> out, long employee, LocalDate effective, boolean isHead, boolean forCompletion) {
        assignedDevices(out, employee, forCompletion ? "BLOCK" : "WARN", "名下还有设备 %s 未收回");
        pendingClaims(out, employee, forCompletion ? "BLOCK" : "WARN", "离职前需要结清");
        openTickets(out, employee, "WARN");
        pendingLeave(out, employee, "WARN");
        Integer future = count("SELECT COUNT(*) FROM leave_request WHERE applicant_user_id = ? AND status = 'APPROVED' AND end_date >= ?",
                employee, java.sql.Date.valueOf(effective));
        if (future > 0) {
            out.add(new Check("LEAVE_AFTER_LAST_DAY", "WARN", "有 " + future + " 张已批准的请假单在离职日之后，请确认是否需要取消"));
        }
        Double annual = jdbc.queryForObject("SELECT COALESCE(SUM(lb.remaining_days), 0) FROM leave_balance lb JOIN leave_type lt ON lt.id = lb.leave_type_id "
                + "WHERE lb.user_id = ? AND lt.code = 'annual' AND lb.year = ?", Double.class, employee, effective.getYear());
        if (annual != null && annual > 0) {
            out.add(new Check("ANNUAL_LEAVE_SETTLEMENT", "INFO", "还有 " + trim(annual) + " 天未休年假，请按公司规定折算结算"));
        }
        if (isHead) {
            out.add(new Check("EMPLOYEE_IS_HEAD", "WARN", "该员工是部门负责人，离职前需要先指定新的负责人，否则部门审批会中断"));
        }
    }

    private void pendingLeave(List<Check> out, long employee, String level) {
        int pending = count("SELECT COUNT(*) FROM leave_request WHERE applicant_user_id = ? AND status = 'SUBMITTED'", employee);
        if (pending > 0) {
            out.add(new Check("PENDING_LEAVE", level, "有 " + pending + " 张请假单还在待审批"));
        }
    }

    private void pendingClaims(List<Check> out, long employee, String level, String hint) {
        int submitted = count("SELECT COUNT(*) FROM expense_claim WHERE applicant_user_id = ? AND status = 'SUBMITTED'", employee);
        if (submitted > 0) {
            out.add(new Check("PENDING_CLAIMS", level, "有 " + submitted + " 张报销单还在待审批；" + hint));
        }
        int drafts = count("SELECT COUNT(*) FROM expense_claim WHERE applicant_user_id = ? AND status = 'DRAFT'", employee);
        if (drafts > 0) {
            out.add(new Check("DRAFT_CLAIMS", "INFO", "有 " + drafts + " 张报销草稿未提交，请提醒员工提交或删除"));
        }
    }

    private void openTickets(List<Check> out, long employee, String level) {
        int open = count("SELECT COUNT(*) FROM it_ticket WHERE requester_user_id = ? AND status IN ('PENDING_APPROVAL', 'OPEN', 'IN_PROGRESS', 'WAITING_USER')", employee);
        if (open > 0) {
            out.add(new Check("OPEN_TICKETS", level, "有 " + open + " 张未结束的 IT 工单"));
        }
    }

    private void assignedDevices(List<Check> out, long employee, String level, String template) {
        List<String> devices = jdbc.queryForList("SELECT CONCAT(asset_no, '（', model, '）') FROM it_device WHERE assignee_user_id = ? "
                + "AND status IN ('ASSIGNED', 'REPAIR') ORDER BY id", String.class, employee);
        if (!devices.isEmpty()) {
            out.add(new Check("DEVICES_ASSIGNED", level, String.format(template, String.join("、", devices))));
        }
    }

    private int count(String sql, Object... args) {
        Integer value = jdbc.queryForObject(sql, Integer.class, args);
        return value == null ? 0 : value;
    }

    private static String trim(double value) {
        return value == Math.rint(value) ? String.valueOf((long) value) : String.valueOf(value);
    }
}
