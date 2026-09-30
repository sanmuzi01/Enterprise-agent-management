CREATE TABLE expense_budget (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    team_id BIGINT NOT NULL,
    year INT NOT NULL,
    remaining_amount DECIMAL(14,2) NOT NULL,
    UNIQUE KEY uq_expense_budget_team_year (team_id, year)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE expense_claim (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    applicant_user_id BIGINT NOT NULL,
    team_id BIGINT NOT NULL,
    status VARCHAR(20) NOT NULL,
    total_amount DECIMAL(14,2) NOT NULL,
    approver_user_id BIGINT NULL,
    decision_note VARCHAR(500) NULL,
    created_at DATETIME NOT NULL,
    submitted_at DATETIME NULL,
    decided_at DATETIME NULL,
    KEY idx_expense_claim_applicant (applicant_user_id),
    KEY idx_expense_claim_team_status (team_id, status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE expense_line (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    expense_claim_id BIGINT NOT NULL,
    category VARCHAR(40) NOT NULL,
    amount DECIMAL(14,2) NOT NULL,
    description VARCHAR(200) NULL,
    invoice_no VARCHAR(80) NULL,
    KEY idx_expense_line_claim (expense_claim_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
