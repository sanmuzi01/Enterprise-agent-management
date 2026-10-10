-- CRM Copilot：商机加“预计成交日期”和“下一步”，风险引擎用它们判断“成交日期临近但阶段没更新”“多次延期”“没有明确下一步”。
ALTER TABLE opportunity
    ADD COLUMN expected_close_date DATE NULL,
    ADD COLUMN next_step VARCHAR(500) NULL;
