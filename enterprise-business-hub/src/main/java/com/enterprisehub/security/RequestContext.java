package com.enterprisehub.security;

import com.fasterxml.jackson.annotation.JsonProperty;

import java.util.List;

/**
 * FastAPI 签发的短时效权限上下文（docs/enterprise-business-hub-plan.md 第 6 节）。
 * 权限来源只在 FastAPI 一处维护——这里只验证签名和有效期，不重新判断权限。
 *
 * {@code method}/{@code path}/{@code bodySha256} 是第四轮审计 P1-8 加的：之前
 * HMAC 只签 {@code X-Context} 本身，不含 HTTP 方法/URL/请求体，截获一份合法的
 * {@code X-Context}/{@code X-Signature} 之后理论上能在到达服务端前替换请求体或者
 * 换一个方法/路径打过去，只要 scope 凑巧满足目标接口的要求就能蒙混过关。现在
 * 这三个字段本身也在签名覆盖范围内（整个 {@code X-Context} JSON 都会被 HMAC
 * 签名，见 {@link HmacSignatureVerifier}），{@link SignedRequestContextFilter}
 * 会拿它们跟真实收到的请求方法/路径/请求体哈希做比对，对不上直接拒绝——
 * 相当于把"这份签名只对这一次具体的请求有效"焊死了。
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
        @JsonProperty("nonce") String nonce,
        @JsonProperty("method") String method,
        @JsonProperty("path") String path,
        @JsonProperty("body_sha256") String bodySha256
) {
    public boolean hasScope(String required) {
        return scopes != null && scopes.contains(required);
    }
}
