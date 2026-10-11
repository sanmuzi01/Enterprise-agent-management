-- 目录外采购：员工要买产品目录里没有的东西（例如新型号显卡）时，明细按“物品名称 + 预估单价”登记，
-- 不再因为没有 SKU 就办不了；审批人在申请里能看到这是目录外物品和采购用途。
ALTER TABLE purchase_request
    ADD COLUMN reason VARCHAR(500) NULL;

ALTER TABLE purchase_request_line
    MODIFY product_id BIGINT NULL,
    ADD COLUMN item_name VARCHAR(200) NULL;
