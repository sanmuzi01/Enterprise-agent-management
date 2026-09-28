-- P1 并发修复（docs/enterprise-rbac-plan.md 第四轮审计）：IdempotencyService 之前是
-- "先查有没有记录、没有就执行、执行完再插入"，两个并发请求可能都查到"没有"，都各自
-- 执行一遍——外部 SAP/CRM 调用因此可能被打两次。改成"抢占式插入占位记录"：请求一进来
-- 先插一行 completed=false 的占位记录，数据库主键唯一约束保证两个并发请求里只有一个
-- 能插入成功；插不进去的直接告诉调用方"重复提交，稍后重试"，不会跟着往下执行业务逻辑。
-- 需要这个字段区分"占位（还没执行完）"和"已完成（可以把 response_body 原样返回）"。
ALTER TABLE idempotency_record
    ADD COLUMN completed BOOLEAN NOT NULL DEFAULT FALSE COMMENT '占位记录还是已完成',
    MODIFY COLUMN status_code INT NULL COMMENT '占位阶段为空，完成后才有真实状态码',
    MODIFY COLUMN response_body MEDIUMTEXT NULL COMMENT '占位阶段为空';
