package com.enterprisehub.web;

import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.server.ResponseStatusException;

import java.util.Map;

/**
 * 业务代码主动抛出的 {@link ResponseStatusException}（"余额不足"、"产品不存在"等）的 reason
 * 本来就是写给最终用户看的；Spring Boot 默认 {@code server.error.include-message=never}
 * 会把它丢掉，FastAPI 只能拿到一串没有原因的错误 JSON。这里只放行这一类有意为之的
 * 消息，其它未预期异常仍走默认处理，不把内部异常信息暴露出去。
 */
@RestControllerAdvice
public class ApiExceptionHandler {

    private static final org.slf4j.Logger LOG = org.slf4j.LoggerFactory.getLogger(ApiExceptionHandler.class);

    @ExceptionHandler(ResponseStatusException.class)
    public ResponseEntity<Map<String, Object>> handle(ResponseStatusException ex) {
        String message = ex.getReason() != null ? ex.getReason() : "请求未被业务服务接受";
        Map<String, Object> body = new java.util.LinkedHashMap<>();
        body.put("status", ex.getStatusCode().value());
        body.put("message", message);
        String traceId = org.slf4j.MDC.get("traceId");
        if (traceId != null) {
            body.put("traceId", traceId);
        }
        return ResponseEntity.status(ex.getStatusCode()).body(body);
    }

    /**
     * 未预期的异常：完整堆栈只在服务端日志里写一次（带 traceId），响应里只有 traceId，不暴露内部信息。
     * FastAPI 收到 500 会把它记成问题中心里的“业务服务出错”，用户看到的是统一错误提示。
     */
    @ExceptionHandler(Exception.class)
    public ResponseEntity<Map<String, Object>> handleUnexpected(Exception ex) throws Exception {
        // Spring MVC 自己认识的请求错误（参数校验、类型不匹配、JSON 解析失败、方法/媒体类型不支持…）原样交还给框架，保持原来的 4xx 行为
        if (ex instanceof org.springframework.web.ErrorResponse || ex instanceof org.springframework.beans.TypeMismatchException
                || ex instanceof org.springframework.http.converter.HttpMessageNotReadableException
                || ex instanceof jakarta.validation.ConstraintViolationException) {
            throw ex;
        }
        LOG.error("未处理的异常: {}", ex.toString(), ex);
        Map<String, Object> body = new java.util.LinkedHashMap<>();
        body.put("status", 500);
        body.put("message", "业务服务处理出错");
        String traceId = org.slf4j.MDC.get("traceId");
        if (traceId != null) {
            body.put("traceId", traceId);
        }
        return ResponseEntity.status(500).body(body);
    }
}
