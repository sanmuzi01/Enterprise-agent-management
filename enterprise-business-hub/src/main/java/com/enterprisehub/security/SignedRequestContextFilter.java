package com.enterprisehub.security;

import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Instant;
import java.util.Base64;
import java.util.HexFormat;

/**
 * 策略执行点（PEP）：每个业务请求都要带 FastAPI 签发的短时效 RequestContext，
 * 验证签名 + 有效期 + 防重放 + 方法/路径/请求体绑定后才放行——权限判断本身在
 * FastAPI 一侧，这里只验证"这份上下文是不是 FastAPI 真的签发的、有没有过期、
 * 有没有被重放、跟这次真实收到的请求是不是同一次"。
 *
 * 最后一条（P1-8，第四轮审计）是这次新加的：之前只验证 {@code X-Context} 本身的
 * 签名，不管请求方法/URL/请求体是什么——截获一份合法的头之后，理论上能在到达
 * 服务端前把请求体换掉，或者换一个方法/路径打过去，只要签名和 scope 还对得上就
 * 能蒙混过关。现在 {@code RequestContext} 里带着签发时的 method/path/body_sha256，
 * 这三个字段本身也在 HMAC 签名覆盖范围内（整个 {@code X-Context} JSON 都被签了），
 * 这里会跟真实收到的请求做逐项比对，对不上就直接拒绝，相当于把"这份签名只对
 * 这一次具体的请求有效"焊死了。
 *
 * `/actuator/**` 是健康检查，不带业务语义，放行不验证——跟主项目 `/health` 公开
 * 探活是同一个道理。
 */
@Component
public class SignedRequestContextFilter extends OncePerRequestFilter {

    public static final String CONTEXT_HEADER = "X-Context";
    public static final String SIGNATURE_HEADER = "X-Signature";

    private final HmacSignatureVerifier verifier;
    private final NonceStore nonceStore;
    private final ObjectMapper objectMapper;
    private final long maxClockSkewSeconds;

    public SignedRequestContextFilter(
            HmacSignatureVerifier verifier,
            NonceStore nonceStore,
            ObjectMapper objectMapper,
            @Value("${enterprise-hub.security.max-clock-skew-seconds:300}") long maxClockSkewSeconds
    ) {
        this.verifier = verifier;
        this.nonceStore = nonceStore;
        this.objectMapper = objectMapper;
        this.maxClockSkewSeconds = maxClockSkewSeconds;
    }

    @Override
    protected boolean shouldNotFilter(HttpServletRequest request) {
        return request.getRequestURI().startsWith("/actuator/");
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain chain)
            throws ServletException, IOException {
        // 必须先把请求体缓存下来再做任何事——下面校验 body_sha256 要读一遍 body，
        // 原生请求的输入流只能读一次，读完 Controller 的 @RequestBody 就拿不到了。
        CachedBodyHttpServletRequest cachedRequest = new CachedBodyHttpServletRequest(request);

        String contextB64 = cachedRequest.getHeader(CONTEXT_HEADER);
        String signature = cachedRequest.getHeader(SIGNATURE_HEADER);

        if (contextB64 == null || signature == null) {
            reject(response, HttpServletResponse.SC_BAD_REQUEST, "缺少 " + CONTEXT_HEADER + "/" + SIGNATURE_HEADER);
            return;
        }
        if (!verifier.verify(contextB64, signature)) {
            reject(response, HttpServletResponse.SC_UNAUTHORIZED, "签名校验失败");
            return;
        }

        RequestContext context;
        try {
            byte[] decoded = Base64.getDecoder().decode(contextB64);
            context = objectMapper.readValue(new String(decoded, StandardCharsets.UTF_8), RequestContext.class);
        } catch (Exception e) {
            reject(response, HttpServletResponse.SC_BAD_REQUEST, "RequestContext 格式不对");
            return;
        }

        long now = Instant.now().getEpochSecond();
        if (Math.abs(now - context.timestamp()) > maxClockSkewSeconds) {
            reject(response, HttpServletResponse.SC_UNAUTHORIZED, "RequestContext 已过期或时间戳不合理");
            return;
        }
        if (context.nonce() == null || context.nonce().isBlank()
                || !nonceStore.rememberIfNew(context.nonce(), maxClockSkewSeconds)) {
            reject(response, HttpServletResponse.SC_UNAUTHORIZED, "重复的请求（nonce 已被使用）");
            return;
        }

        String requestPath = cachedRequest.getQueryString() == null
                ? cachedRequest.getRequestURI()
                : cachedRequest.getRequestURI() + "?" + cachedRequest.getQueryString();
        if (context.method() == null || !context.method().equalsIgnoreCase(cachedRequest.getMethod())
                || context.path() == null || !context.path().equals(requestPath)) {
            reject(response, HttpServletResponse.SC_UNAUTHORIZED,
                    "签名跟实际请求的方法/路径不匹配（签的是别的请求，可能是重放/篡改）");
            return;
        }
        String actualBodySha256 = sha256Hex(cachedRequest.getCachedBody());
        if (context.bodySha256() == null || !context.bodySha256().equalsIgnoreCase(actualBodySha256)) {
            reject(response, HttpServletResponse.SC_UNAUTHORIZED, "签名跟实际请求体不匹配（请求体可能被篡改）");
            return;
        }

        RequestContextHolder.set(context);
        try {
            chain.doFilter(cachedRequest, response);
        } finally {
            RequestContextHolder.clear();
        }
    }

    private static String sha256Hex(byte[] body) {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            return HexFormat.of().formatHex(digest.digest(body));
        } catch (NoSuchAlgorithmException e) {
            // SHA-256 是 JDK 标配算法，不会真的走到这里；抛运行时异常比静默返回错误哈希更安全。
            throw new IllegalStateException("SHA-256 不可用", e);
        }
    }

    private void reject(HttpServletResponse response, int status, String message) throws IOException {
        response.setStatus(status);
        response.setContentType("application/json;charset=UTF-8");
        response.getWriter().write("{\"detail\":\"" + message.replace("\"", "'") + "\"}");
    }
}
