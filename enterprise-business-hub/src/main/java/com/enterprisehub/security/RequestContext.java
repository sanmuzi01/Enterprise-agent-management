package com.enterprisehub.security;

import com.fasterxml.jackson.annotation.JsonProperty;

import java.util.List;

/**
 * FastAPI 签发的短时效权限上下文（docs/enterprise-business-hub-plan.md 第 6 节）。
 * 权限来源只在 FastAPI 一处维护——这里只验证签名和有效期，不重新判断权限。
 */
public record RequestContext(
        @JsonProperty("user_id") long userId,
        @JsonProperty("team_id") Long teamId,
        @JsonProperty("scopes") List<String> scopes,
        @JsonProperty("operation") String operation,
        @JsonProperty("is_org_admin") boolean isOrgAdmin,
        @JsonProperty("is_team_admin") boolean isTeamAdmin,
        @JsonProperty("trace_id") String traceId,
        @JsonProperty("timestamp") long timestamp,
        @JsonProperty("nonce") String nonce
) {
    public boolean hasScope(String required) {
        return scopes != null && scopes.contains(required);
    }
}
