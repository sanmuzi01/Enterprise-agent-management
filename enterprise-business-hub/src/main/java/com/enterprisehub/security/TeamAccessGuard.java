package com.enterprisehub.security;

import org.springframework.http.HttpStatus;
import org.springframework.web.server.ResponseStatusException;

/**
 * 资源级归属判断：跟 {@link ScopeGuard}（判断"这次调用签的 scope 里有没有这一条"，
 * 只是接口级的粗粒度门）是两层不同的检查——这一层判断"这个具体资源到底归不归你管"
 * （普通员工只能碰自己的申请、部门负责人只能碰本部门的申请、企业管理员才能跨部门）。
 *
 * 只吃显式参数，不读 {@link RequestContextHolder}——Service 方法保持"入参是普通
 * long/boolean，不依赖线程上下文"的既有风格（跟 {@code LeaveService#getOwnedDraft}
 * 这类既有方法一致），方便单测不用伪造 Spring 请求上下文。
 *
 * {@code isOrgAdmin}/{@code isTeamAdmin} 必须是 FastAPI 签好的值（{@link RequestContext}
 * 里那两个字段），不能是这里自己查出来的——企业业务中心自己的数据库没有
 * organization_members/team_members，判断权限的数据源只在 FastAPI 那一侧
 * （docs/enterprise-business-hub-plan.md 第16节）。
 */
public final class TeamAccessGuard {
    private TeamAccessGuard() {
    }

    /** 企业管理员可以跨部门；部门负责人只能管自己那个部门（callerTeamId 跟资源的 teamId 一致）。 */
    public static boolean canActOnTeam(Long callerTeamId, boolean isOrgAdmin, boolean isTeamAdmin, Long resourceTeamId) {
        if (isOrgAdmin) {
            return true;
        }
        return isTeamAdmin && resourceTeamId != null && callerTeamId != null
                && callerTeamId.longValue() == resourceTeamId.longValue();
    }

    /** 审批/处理类操作用这个：只有部门负责人（管得到这个部门）或企业管理员才行，申请人自己不算。 */
    public static void requireTeamAccess(Long callerTeamId, boolean isOrgAdmin, boolean isTeamAdmin, Long resourceTeamId) {
        if (!canActOnTeam(callerTeamId, isOrgAdmin, isTeamAdmin, resourceTeamId)) {
            throw notFound();
        }
    }

    /** 查看类操作用这个：申请人本人，或者管得到这个部门/企业的人。 */
    public static void requireOwnerOrTeamAccess(long resourceOwnerUserId, long callerUserId, Long callerTeamId,
                                                 boolean isOrgAdmin, boolean isTeamAdmin, Long resourceTeamId) {
        if (resourceOwnerUserId == callerUserId) {
            return;
        }
        requireTeamAccess(callerTeamId, isOrgAdmin, isTeamAdmin, resourceTeamId);
    }

    private static ResponseStatusException notFound() {
        // 统一 404，不额外区分"资源不存在"和"资源存在但不归你管"——那条区分本身就是信息泄露，
        // 跟 service/enterprise_access.py 的既有约定一致。
        return new ResponseStatusException(HttpStatus.NOT_FOUND, "资源不存在或无权限");
    }
}
