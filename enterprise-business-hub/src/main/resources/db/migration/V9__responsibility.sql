-- 部门责任执行：把会议纪要/工作文本整理成"每名员工都能确认、执行、提交和验收"的责任事项。
-- 计划（plan）= 一次文本整理；责任（task）= 一项正式责任，有且只有一名主责员工；事件（event）= 不可修改的履责记录。
-- 状态机：DRAFT →(负责人发布)→ PENDING_ACCEPT →(员工接受)→ IN_PROGRESS ⇄ BLOCKED → PENDING_REVIEW →(验收人验收)→ DONE；
-- 员工可对责任提出异议（NEGOTIATING），负责人修改后重新回到 PENDING_ACCEPT；验收人可退回（回到 IN_PROGRESS）。

CREATE TABLE responsibility_plan (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    team_id BIGINT NOT NULL,
    title VARCHAR(160) NOT NULL,
    source_type VARCHAR(20) NOT NULL,             -- MEETING / CHAT / EMAIL / NOTICE / OTHER
    source_text MEDIUMTEXT NULL,
    summary VARCHAR(1000) NULL,
    decisions_json TEXT NULL,                      -- [{content, evidence}]
    unresolved_json TEXT NULL,                     -- ["原文没有说明最终验收人"]
    status VARCHAR(20) NOT NULL,                   -- DRAFT / PUBLISHED / COMPLETED / CANCELLED
    created_by BIGINT NOT NULL,
    published_by BIGINT NULL,
    automation_work_id VARCHAR(40) NULL,           -- 来自哪次 AI 整理（同一次整理只能生成一个计划）
    missing_at_creation INT NOT NULL DEFAULT 0,    -- AI 草稿创建时缺失的必填字段数（效率指标：发布前发现的遗漏）
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    published_at DATETIME NULL,
    UNIQUE KEY uq_resp_plan_work (automation_work_id),
    KEY idx_resp_plan_team_status (team_id, status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE responsibility_task (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    plan_id BIGINT NOT NULL,
    team_id BIGINT NOT NULL,                       -- 冗余自计划，便于按部门查询与授权
    seq INT NOT NULL,
    title VARCHAR(160) NOT NULL,
    responsible_user_id BIGINT NULL,               -- 草稿阶段可以为空（待补充）；发布后必须有
    reviewer_user_id BIGINT NULL,
    assigned_by_user_id BIGINT NULL,               -- 发布（正式指派）的负责人
    deliverable VARCHAR(300) NULL,
    acceptance_criteria VARCHAR(500) NULL,
    priority VARCHAR(10) NOT NULL DEFAULT 'NORMAL', -- LOW / NORMAL / HIGH / URGENT
    due_date DATE NULL,
    status VARCHAR(20) NOT NULL,
    source_evidence VARCHAR(500) NULL,
    blocked_reason VARCHAR(300) NULL,
    blocked_since DATETIME NULL,
    waiting_on_user_id BIGINT NULL,
    last_progress VARCHAR(500) NULL,
    progress_percent INT NULL,
    last_progress_at DATETIME NULL,
    objection_note VARCHAR(500) NULL,
    pending_due_date DATE NULL,                    -- 员工申请的延期日期（待负责人决定）
    extension_reason VARCHAR(300) NULL,
    transfer_note VARCHAR(300) NULL,               -- 员工申请转交的说明（待负责人决定）
    rework_count INT NOT NULL DEFAULT 0,
    ai_responsible_user_id BIGINT NULL,            -- AI 草稿建议的主责员工（效率指标：一次匹配准确率）
    assigned_at DATETIME NULL,
    accepted_at DATETIME NULL,
    first_submitted_at DATETIME NULL,
    submitted_at DATETIME NULL,
    verified_at DATETIME NULL,
    completed_at DATETIME NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    KEY idx_resp_task_plan (plan_id),
    KEY idx_resp_task_responsible (responsible_user_id, status),
    KEY idx_resp_task_reviewer (reviewer_user_id, status),
    KEY idx_resp_task_team_status (team_id, status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE responsibility_collaborator (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    task_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    UNIQUE KEY uq_resp_collab (task_id, user_id),
    KEY idx_resp_collab_user (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE responsibility_dependency (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    task_id BIGINT NOT NULL,
    depends_on_task_id BIGINT NOT NULL,
    UNIQUE KEY uq_resp_dep (task_id, depends_on_task_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE responsibility_deliverable (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    task_id BIGINT NOT NULL,
    submission_no INT NOT NULL,                    -- 第几次提交（退回后重新提交会递增）
    summary VARCHAR(1000) NOT NULL,
    link VARCHAR(500) NULL,
    submitted_by BIGINT NOT NULL,
    submitted_at DATETIME NOT NULL,
    KEY idx_resp_deliverable_task (task_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 履责事件：只追加，应用层没有任何修改/删除入口。
CREATE TABLE responsibility_event (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    plan_id BIGINT NOT NULL,
    task_id BIGINT NULL,
    event_type VARCHAR(30) NOT NULL,
    actor_user_id BIGINT NOT NULL,
    note VARCHAR(500) NULL,
    detail_json TEXT NULL,
    created_at DATETIME NOT NULL,
    KEY idx_resp_event_plan (plan_id),
    KEY idx_resp_event_task (task_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
