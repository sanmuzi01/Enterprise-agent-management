package com.enterprisehub.security;

/**
 * 同一次请求内传递已验证过的 {@link RequestContext}——用 ThreadLocal，
 * {@link SignedRequestContextFilter} 负责在请求结束时清理，避免线程池复用线程后串上下文。
 */
public final class RequestContextHolder {
    private static final ThreadLocal<RequestContext> CURRENT = new ThreadLocal<>();

    private RequestContextHolder() {
    }

    public static void set(RequestContext context) {
        CURRENT.set(context);
    }

    public static RequestContext current() {
        RequestContext ctx = CURRENT.get();
        if (ctx == null) {
            throw new IllegalStateException("当前线程没有已验证的 RequestContext——SignedRequestContextFilter 没有生效？");
        }
        return ctx;
    }

    /** 没有已验证的请求上下文时返回 null（不是经 FastAPI 签名请求进来的调用，比如纯逻辑单测直接调 Service）。 */
    public static RequestContext currentOrNull() {
        return CURRENT.get();
    }

    public static void clear() {
        CURRENT.remove();
    }
}
