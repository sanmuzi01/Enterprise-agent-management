package com.enterprisehub.responsibility;

import org.springframework.http.HttpStatus;
import org.springframework.web.server.ResponseStatusException;

import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;

/**
 * 调用者在责任协同里的身份。三个集合都由 FastAPI 在确认企业/部门成员身份有效后计算，写在已签名的请求路径里，改不了：
 * scope 是同一企业的全部部门；memberTeamIds 是调用者当前仍是有效成员的部门；headTeamIds 是调用者担任负责人的部门
 * （企业管理员 = 全部部门）。离开部门后 memberTeamIds 不再包含该部门，也就不能再看到或操作那个部门的责任事项。
 */
public record RespActor(long userId, Set<Long> scope, Set<Long> memberTeamIds, Set<Long> headTeamIds) {

    public static RespActor of(long userId, List<Long> scopeTeamIds, List<Long> memberTeamIds, List<Long> headTeamIds) {
        if (scopeTeamIds == null || scopeTeamIds.isEmpty()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "缺少 scopeTeamIds");
        }
        Set<Long> scope = new LinkedHashSet<>(scopeTeamIds);
        Set<Long> members = new LinkedHashSet<>(memberTeamIds == null ? List.<Long>of() : memberTeamIds);
        Set<Long> heads = new LinkedHashSet<>(headTeamIds == null ? List.<Long>of() : headTeamIds);
        if (!scope.containsAll(members) || !scope.containsAll(heads)) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "成员或负责人部门不在企业范围内");
        }
        return new RespActor(userId, scope, members, heads);
    }

    public boolean isHead(long teamId) {
        return headTeamIds.contains(teamId);
    }

    /** 当前仍是该部门有效成员，或是该部门的负责人。 */
    public boolean canAccess(long teamId) {
        return memberTeamIds.contains(teamId) || headTeamIds.contains(teamId);
    }

    public Set<Long> accessibleTeams() {
        Set<Long> all = new LinkedHashSet<>(memberTeamIds);
        all.addAll(headTeamIds);
        return all;
    }
}
