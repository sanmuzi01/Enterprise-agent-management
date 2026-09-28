package com.enterprisehub.idempotency;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;

import java.util.Optional;
import java.util.function.Supplier;

@Service
public class IdempotencyService {
    private final IdempotencyRepository repository;
    private final ObjectMapper objectMapper;

    public IdempotencyService(IdempotencyRepository repository, ObjectMapper objectMapper) {
        this.repository = repository;
        this.objectMapper = objectMapper;
    }

    /**
     * 有这个 key 的完成记录就直接把当年存的结果原样返回，不重新跑 {@code action}；
     * 没有就跑一遍、把结果存起来再返回。
     *
     * P1 并发修复（第四轮审计）：之前是"先查有没有记录、没有就执行、执行完再插入"，
     * 两个并发请求可能都查到"没有"，都各自执行一遍——外部 SAP/CRM 调用因此可能被打
     * 两次。改成"抢占式插入占位记录"：先插一行 {@code completed=false} 的占位记录，
     * `idempotency_key` 是主键，数据库唯一约束保证两个并发请求里只有一个能插入
     * 成功；插不进去（{@link DataIntegrityViolationException}）说明别人已经抢到了
     * 这个 key、正在执行或者刚执行完，直接告诉调用方"重复提交，稍后重试"，不会跟着
     * 往下执行 {@code action}——绝对不会出现同一个 key 被执行两次。
     *
     * 不做"阻塞等对方结果"（轮询占位记录直到变成 completed）：那需要独立的新事务 +
     * 轮询 + 超时机制，复杂度换来的只是"极短时间内重复提交能不能拿到跟第一次一样的
     * 返回值"这个体验优化，不是正确性问题（正确性靠上面的抢占式插入已经保证了，
     * 不会双重执行）；调用方（FastAPI 工具）看到 409 直接告诉用户"提交太快了，请稍后
     * 重试"就是正确、诚实的行为，不需要在这里做更复杂的等待。
     */
    @Transactional
    public ResponseEntity<Object> execute(String idempotencyKey, Supplier<ResponseEntity<Object>> action) {
        if (idempotencyKey == null || idempotencyKey.isBlank()) {
            return action.get();
        }

        Optional<IdempotencyRecord> existing = repository.findById(idempotencyKey);
        if (existing.isPresent() && existing.get().isCompleted()) {
            return toResponse(existing.get());
        }
        if (existing.isPresent()) {
            // 占位记录存在但还没完成：要么是另一个并发请求正在执行，要么是上一次执行
            // 中途异常退出留下的孤儿占位（比如进程被杀）。两种情况都不安全重新执行，
            // 统一按"重复提交"处理，不去猜测是哪一种。
            throw duplicateSubmission(idempotencyKey);
        }

        try {
            claimPlaceholder(idempotencyKey);
        } catch (DataIntegrityViolationException e) {
            // 刚才 findById 没查到，但插入时撞了唯一键——说明在这两步之间，另一个
            // 并发请求抢先插入了占位记录。这正是这个方案要防的那个并发窗口，能撞上
            // 说明修复生效了，不是 bug。
            throw duplicateSubmission(idempotencyKey);
        }

        ResponseEntity<Object> result = action.get();
        String bodyJson = writeBody(result.getBody());
        IdempotencyRecord record = repository.findById(idempotencyKey).orElseThrow();
        record.markCompleted(result.getStatusCode().value(), bodyJson);
        repository.save(record);
        return result;
    }

    /** 就是个普通私有方法，不单独标 {@code @Transactional}——从 {@link #execute}
     * 内部调用属于同类自调用，不会经过 Spring 的事务代理，单独标了也不会生效，
     * 会误导人以为它有独立的事务边界。实际就在 {@code execute} 已经开启的事务里跑。 */
    private void claimPlaceholder(String idempotencyKey) {
        repository.saveAndFlush(new IdempotencyRecord(idempotencyKey));
    }

    private ResponseStatusException duplicateSubmission(String idempotencyKey) {
        return new ResponseStatusException(HttpStatus.CONFLICT,
                "重复的幂等请求（Idempotency-Key=" + idempotencyKey + "）正在处理中或刚处理完，请稍后重试");
    }

    private ResponseEntity<Object> toResponse(IdempotencyRecord record) {
        Object body = readBody(record.getResponseBody());
        return ResponseEntity.status(record.getStatusCode()).body(body);
    }

    private Object readBody(String json) {
        try {
            return objectMapper.readValue(json, Object.class);
        } catch (Exception e) {
            return null;
        }
    }

    private String writeBody(Object body) {
        try {
            return objectMapper.writeValueAsString(body);
        } catch (Exception e) {
            return "null";
        }
    }
}
