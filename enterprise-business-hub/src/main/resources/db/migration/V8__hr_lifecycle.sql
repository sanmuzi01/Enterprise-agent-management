-- 人事入转调离：入职 / 转正 / 调岗 / 离职。HR 发起 → 业务规则检查 → 员工所在部门负责人批准 →
-- 生成跨部门办理清单（人事/IT/财务/负责人/员工各办各的）→ 清单办完且检查无阻断 → HR 办结。

CREATE TABLE hr_case (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    case_type VARCHAR(20) NOT NULL,                -- ONBOARDING / PROBATION / TRANSFER / OFFBOARDING
    employee_user_id BIGINT NOT NULL,
    team_id BIGINT NOT NULL,                       -- 办理时员工所在部门（审批人看这个部门）
    target_team_id BIGINT NULL,                    -- 调岗的目标部门
    position VARCHAR(80) NULL,
    effective_date DATE NOT NULL,
    reason VARCHAR(500) NULL,
    status VARCHAR(20) NOT NULL,                   -- PENDING_APPROVAL / IN_PROGRESS / COMPLETED / REJECTED / CANCELLED
    initiator_user_id BIGINT NOT NULL,
    approver_user_id BIGINT NULL,
    decision_note VARCHAR(500) NULL,
    employee_is_head TINYINT(1) NOT NULL DEFAULT 0, -- 员工是否部门负责人（创建时由 FastAPI 给出，用于检查）
    check_json TEXT NULL,                          -- 最近一次规则检查结果
    effect_pending TINYINT(1) NOT NULL DEFAULT 0,  -- 办结后还需要在系统里落实的变更（调岗换部门、离职停用账号）
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    completed_at DATETIME NULL,
    KEY idx_hr_case_employee (employee_user_id),
    KEY idx_hr_case_team_status (team_id, status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE hr_case_task (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    case_id BIGINT NOT NULL,
    seq INT NOT NULL,
    title VARCHAR(120) NOT NULL,
    owner VARCHAR(20) NOT NULL,                    -- HR / IT / FINANCE / MANAGER / EMPLOYEE
    required TINYINT(1) NOT NULL DEFAULT 1,
    status VARCHAR(10) NOT NULL,                   -- OPEN / DONE / SKIPPED
    due_date DATE NOT NULL,
    done_by BIGINT NULL,
    done_at DATETIME NULL,
    note VARCHAR(300) NULL,
    KEY idx_hr_task_case (case_id),
    KEY idx_hr_task_owner_status (owner, status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
