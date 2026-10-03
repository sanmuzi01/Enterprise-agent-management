-- 财务自动记账：报销单批准后自动生成记账凭证草稿，财务人员核对科目与风险后确认入账。
-- 凭证金额完全来自报销明细，科目由规则表建议；人工可改借方科目，但借贷永远平衡。

-- 科目表（小企业会计准则的常用费用类科目，足够覆盖报销场景；只作为凭证建议的取值范围）
CREATE TABLE account_subject (
    code VARCHAR(20) PRIMARY KEY,
    name VARCHAR(60) NOT NULL,
    category VARCHAR(20) NOT NULL,          -- EXPENSE（费用）/ LIABILITY（负债）
    direction VARCHAR(1) NOT NULL,             -- 正常余额方向 D 借 / C 贷
    enabled TINYINT(1) NOT NULL DEFAULT 1
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT INTO account_subject (code, name, category, direction) VALUES
    ('6601.01', '销售费用-差旅费', 'EXPENSE', 'D'),
    ('6601.02', '销售费用-业务招待费', 'EXPENSE', 'D'),
    ('6601.03', '销售费用-办公费', 'EXPENSE', 'D'),
    ('6601.04', '销售费用-交通费', 'EXPENSE', 'D'),
    ('6601.05', '销售费用-职工福利费', 'EXPENSE', 'D'),
    ('6601.99', '销售费用-其他', 'EXPENSE', 'D'),
    ('6602.01', '管理费用-差旅费', 'EXPENSE', 'D'),
    ('6602.02', '管理费用-业务招待费', 'EXPENSE', 'D'),
    ('6602.03', '管理费用-办公费', 'EXPENSE', 'D'),
    ('6602.04', '管理费用-交通费', 'EXPENSE', 'D'),
    ('6602.05', '管理费用-职工福利费', 'EXPENSE', 'D'),
    ('6602.99', '管理费用-其他', 'EXPENSE', 'D'),
    ('2241.01', '其他应付款-员工报销款', 'LIABILITY', 'C');

-- 科目建议规则：同一费用类别下，priority 大的先匹配；keyword 为空表示该类别的默认科目。
-- subject_suffix 拼在费用大类（销售部门 6601 / 其他部门 6602）后面。
CREATE TABLE subject_rule (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    category VARCHAR(40) NOT NULL,
    keyword VARCHAR(40) NULL,
    subject_suffix VARCHAR(10) NOT NULL,
    priority INT NOT NULL,
    confidence VARCHAR(10) NOT NULL,        -- HIGH / MEDIUM / LOW
    note VARCHAR(120) NOT NULL,
    KEY idx_subject_rule_category (category, priority)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT INTO subject_rule (category, keyword, subject_suffix, priority, confidence, note) VALUES
    ('MEAL', '招待', '02', 90, 'HIGH', '餐饮说明含“招待”，按业务招待费'),
    ('MEAL', '客户', '02', 90, 'HIGH', '餐饮说明含“客户”，按业务招待费'),
    ('MEAL', '宴请', '02', 90, 'HIGH', '餐饮说明含“宴请”，按业务招待费'),
    ('MEAL', '商务', '02', 90, 'HIGH', '餐饮说明含“商务”，按业务招待费'),
    ('MEAL', NULL, '05', 10, 'MEDIUM', '餐饮默认按职工福利费；如属客户招待请改为业务招待费'),
    ('TRAVEL', NULL, '01', 10, 'HIGH', '差旅类默认按差旅费'),
    ('OFFICE_SUPPLY', NULL, '03', 10, 'HIGH', '办公用品类默认按办公费'),
    ('TRANSPORT', NULL, '04', 10, 'HIGH', '交通类默认按交通费'),
    ('OTHER', '招待', '02', 90, 'MEDIUM', '说明含“招待”，按业务招待费'),
    ('OTHER', '宴请', '02', 90, 'MEDIUM', '说明含“宴请”，按业务招待费'),
    ('OTHER', '打车', '04', 80, 'MEDIUM', '说明含“打车”，按交通费'),
    ('OTHER', '出租', '04', 80, 'MEDIUM', '说明含“出租”，按交通费'),
    ('OTHER', '滴滴', '04', 80, 'MEDIUM', '说明含“滴滴”，按交通费'),
    ('OTHER', '地铁', '04', 80, 'MEDIUM', '说明含“地铁”，按交通费'),
    ('OTHER', '加油', '04', 80, 'MEDIUM', '说明含“加油”，按交通费'),
    ('OTHER', '停车', '04', 80, 'MEDIUM', '说明含“停车”，按交通费'),
    ('OTHER', '机票', '01', 80, 'MEDIUM', '说明含“机票”，按差旅费'),
    ('OTHER', '高铁', '01', 80, 'MEDIUM', '说明含“高铁”，按差旅费'),
    ('OTHER', '酒店', '01', 80, 'MEDIUM', '说明含“酒店”，按差旅费'),
    ('OTHER', '住宿', '01', 80, 'MEDIUM', '说明含“住宿”，按差旅费'),
    ('OTHER', '出差', '01', 80, 'MEDIUM', '说明含“出差”，按差旅费'),
    ('OTHER', '文具', '03', 70, 'MEDIUM', '说明含“文具”，按办公费'),
    ('OTHER', '打印', '03', 70, 'MEDIUM', '说明含“打印”，按办公费'),
    ('OTHER', '耗材', '03', 70, 'MEDIUM', '说明含“耗材”，按办公费'),
    ('OTHER', '快递', '03', 70, 'MEDIUM', '说明含“快递”，按办公费'),
    ('OTHER', '办公', '03', 70, 'MEDIUM', '说明含“办公”，按办公费'),
    ('OTHER', NULL, '99', 10, 'LOW', '无法从类别和说明判断科目，暂记其他，请财务核对');

-- 报销单记录申请部门的业务类型（销售部门走销售费用，其他走管理费用），由 FastAPI 在创建/批准时给出
ALTER TABLE expense_claim ADD COLUMN department_code VARCHAR(20) NULL;

CREATE TABLE voucher (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    expense_claim_id BIGINT NOT NULL,
    team_id BIGINT NOT NULL,
    applicant_user_id BIGINT NOT NULL,
    status VARCHAR(20) NOT NULL,            -- DRAFT / POSTED / VOID
    expense_class VARCHAR(10) NOT NULL,     -- SALES（6601）/ ADMIN（6602）
    voucher_date DATE NOT NULL,
    period VARCHAR(7) NOT NULL,                -- YYYY-MM
    summary VARCHAR(200) NOT NULL,
    total_amount DECIMAL(14,2) NOT NULL,
    voucher_no VARCHAR(30) NULL,
    risk_level VARCHAR(10) NOT NULL,        -- NONE / INFO / WARN / BLOCK
    risk_json TEXT NULL,
    created_by BIGINT NOT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    confirmed_by BIGINT NULL,
    confirm_note VARCHAR(500) NULL,
    warnings_acknowledged TINYINT(1) NOT NULL DEFAULT 0,
    posted_at DATETIME NULL,
    voided_by BIGINT NULL,
    void_reason VARCHAR(300) NULL,
    voided_at DATETIME NULL,
    UNIQUE KEY uq_voucher_claim (expense_claim_id),
    UNIQUE KEY uq_voucher_no (voucher_no),
    KEY idx_voucher_team_status (team_id, status),
    KEY idx_voucher_period_status (period, status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE voucher_entry (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    voucher_id BIGINT NOT NULL,
    line_no INT NOT NULL,
    subject_code VARCHAR(20) NOT NULL,
    subject_name VARCHAR(60) NOT NULL,
    direction VARCHAR(1) NOT NULL,             -- D 借 / C 贷
    amount DECIMAL(14,2) NOT NULL,
    summary VARCHAR(200) NOT NULL,
    basis VARCHAR(300) NOT NULL,            -- 科目依据：命中的规则或人工修改说明
    confidence VARCHAR(10) NOT NULL,        -- HIGH / MEDIUM / LOW / MANUAL
    manual_override TINYINT(1) NOT NULL DEFAULT 0,
    claim_line_id BIGINT NULL,
    KEY idx_voucher_entry_voucher (voucher_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 凭证号按期间连续编号：每个期间一行，生成编号时 SELECT ... FOR UPDATE 串行化
CREATE TABLE voucher_sequence (
    period VARCHAR(7) PRIMARY KEY,
    last_no INT NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
