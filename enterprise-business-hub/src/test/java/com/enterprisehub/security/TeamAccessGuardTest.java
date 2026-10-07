package com.enterprisehub.security;

import org.junit.jupiter.api.Test;
import org.springframework.http.HttpStatus;
import org.springframework.web.server.ResponseStatusException;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/** 纯逻辑单测，不需要 Spring 上下文——{@link TeamAccessGuard} 只吃显式参数。 */
class TeamAccessGuardTest {

    @Test
    void orgAdmin_canActOnAnyTeam() {
        assertThat(TeamAccessGuard.canActOnTeam(null, true, false, 999L)).isTrue();
        assertThat(TeamAccessGuard.canActOnTeam(1L, true, false, 999L)).isTrue();
    }

    @Test
    void teamAdmin_canOnlyActOnOwnTeam() {
        assertThat(TeamAccessGuard.canActOnTeam(1L, false, true, 1L)).isTrue();
        assertThat(TeamAccessGuard.canActOnTeam(1L, false, true, 2L)).isFalse();
    }

    @Test
    void regularMember_cannotActOnAnyTeam() {
        assertThat(TeamAccessGuard.canActOnTeam(1L, false, false, 1L)).isFalse();
    }

    @Test
    void nullResourceTeamId_onlyOrgAdminCanAct() {
        assertThat(TeamAccessGuard.canActOnTeam(1L, false, true, null)).isFalse();
        assertThat(TeamAccessGuard.canActOnTeam(null, true, false, null)).isTrue();
    }

    @Test
    void requireTeamAccess_throwsNotFound_whenDenied() {
        assertThatThrownBy(() -> TeamAccessGuard.requireTeamAccess(1L, false, false, 2L))
                .isInstanceOf(ResponseStatusException.class)
                .extracting(ex -> ((ResponseStatusException) ex).getStatusCode())
                .isEqualTo(HttpStatus.NOT_FOUND);
    }

    @Test
    void requireOwnerOrTeamAccess_allowsOwnerRegardlessOfRole() {
        TeamAccessGuard.requireOwnerOrTeamAccess(42L, 42L, null, false, false, 999L);
        // 没抛异常就是通过——owner 自己永远能看自己的资源，不用管角色。
    }

    @Test
    void requireOwnerOrTeamAccess_deniesNonOwnerWithoutRole() {
        assertThatThrownBy(() -> TeamAccessGuard.requireOwnerOrTeamAccess(42L, 43L, 1L, false, false, 2L))
                .isInstanceOf(ResponseStatusException.class);
    }

    // ---- 企业管理员只管自己的企业（org_team_ids）----

    private static RequestContext contextWithOrgTeams(java.util.List<Long> orgTeamIds) {
        return new RequestContext(1L, 5L, java.util.List.of(), "op", true, false, orgTeamIds, "t", 0L, "n", "GET", "/x", "");
    }

    @Test
    void orgAdmin_canOnlyActOnTeamsOfTheirOwnOrganization() {
        RequestContextHolder.set(contextWithOrgTeams(java.util.List.of(5L, 6L)));
        try {
            assertThat(TeamAccessGuard.canActOnTeam(5L, true, false, 6L)).isTrue();
            assertThat(TeamAccessGuard.canActOnTeam(5L, true, false, 999L)).isFalse();      // 别的企业的部门
            assertThat(TeamAccessGuard.canActOnTeam(null, true, false, null)).isFalse();    // 资源不存在
        } finally {
            RequestContextHolder.clear();
        }
    }

    @Test
    void orgAdmin_withoutOrgTeamIds_isDenied_whenThereIsARequestContext() {
        RequestContextHolder.set(contextWithOrgTeams(null));
        try {
            assertThat(TeamAccessGuard.canActOnTeam(5L, true, false, 5L)).isFalse();
        } finally {
            RequestContextHolder.clear();
        }
    }

    @Test
    void teamAdminAndMemberRulesAreUnaffectedByOrgTeamIds() {
        RequestContextHolder.set(contextWithOrgTeams(java.util.List.of(5L, 6L)));
        try {
            assertThat(TeamAccessGuard.canActOnTeam(5L, false, true, 5L)).isTrue();
            assertThat(TeamAccessGuard.canActOnTeam(5L, false, true, 6L)).isFalse();         // 部门负责人不因为企业里有别的部门就能管它们
        } finally {
            RequestContextHolder.clear();
        }
    }
}
