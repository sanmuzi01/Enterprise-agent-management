package com.enterprisehub.hr;

import org.springframework.http.HttpStatus;
import org.springframework.web.server.ResponseStatusException;

import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;

/**
 * 调用者在人事事项里的身份。三个集合都由 FastAPI 在确认调用者的企业/部门成员身份有效后计算，
 * 写在已签名的请求路径里，改不了：scope 是同一企业的全部部门；deptRoles 是调用者作为 HR/IT/财务部门有效成员
 * 持有的办理角色；headTeamIds 是调用者作为负责人的部门（企业管理员 = 全部部门）。
 */
public record HrActor(long userId, Set<Long> scope, Set<String> deptRoles, Set<Long> headTeamIds) {
    private static final Set<String> DEPT_ROLES = Set.of("HR", "IT", "FINANCE");

    public static HrActor of(long userId, List<Long> scopeTeamIds, List<String> roles, List<Long> headTeamIds) {
        if (scopeTeamIds == null || scopeTeamIds.isEmpty()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "缺少 scopeTeamIds");
        }
        Set<String> clean = new LinkedHashSet<>();
        for (String role : roles == null ? List.<String>of() : roles) {
            if (!DEPT_ROLES.contains(role)) {
                throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "未知的办理角色: " + role);
            }
            clean.add(role);
        }
        return new HrActor(userId, new LinkedHashSet<>(scopeTeamIds), clean,
                new LinkedHashSet<>(headTeamIds == null ? List.<Long>of() : headTeamIds));
    }

    public boolean isHr() {
        return deptRoles.contains("HR");
    }

    /** 在这个事项里调用者能以什么身份办事。 */
    public Set<String> rolesFor(HrCase c) {
        Set<String> roles = new LinkedHashSet<>();
        if (scope.contains(c.getTeamId())) {
            roles.addAll(deptRoles);
        }
        if (headTeamIds.contains(c.getTeamId())) {
            roles.add("MANAGER");
        }
        if (c.getEmployeeUserId() == userId) {
            roles.add("EMPLOYEE");
        }
        return roles;
    }

    public boolean canView(HrCase c) {
        return !rolesFor(c).isEmpty() || c.getInitiatorUserId() == userId;
    }
}
