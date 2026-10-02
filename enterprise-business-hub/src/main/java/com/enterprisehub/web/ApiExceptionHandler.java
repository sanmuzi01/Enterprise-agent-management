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

    @ExceptionHandler(ResponseStatusException.class)
    public ResponseEntity<Map<String, Object>> handle(ResponseStatusException ex) {
        String message = ex.getReason() != null ? ex.getReason() : "请求未被业务服务接受";
        return ResponseEntity.status(ex.getStatusCode())
                .body(Map.of("status", ex.getStatusCode().value(), "message", message));
    }
}
