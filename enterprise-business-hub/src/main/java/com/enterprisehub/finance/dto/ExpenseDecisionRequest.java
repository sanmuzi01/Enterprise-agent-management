package com.enterprisehub.finance.dto;

/** note 是处理意见；departmentCode 仅批准时用——申请部门的业务类型，决定自动生成的凭证走销售费用还是管理费用。 */
public record ExpenseDecisionRequest(String note, String departmentCode) {
}
