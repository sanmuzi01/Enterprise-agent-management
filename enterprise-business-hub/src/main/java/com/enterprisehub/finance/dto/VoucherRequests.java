package com.enterprisehub.finance.dto;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;

/** 凭证相关的请求体。 */
public final class VoucherRequests {
    private VoucherRequests() {
    }

    /** 补生成凭证：departmentCode 是申请人所在部门的业务类型，销售部门走销售费用，其他走管理费用。 */
    public record Generate(@Size(max = 20) String departmentCode) {
    }

    public record Confirm(@Size(max = 500) String note, boolean acknowledgeWarnings) {
    }

    public record UpdateEntry(@NotBlank @Size(max = 20) String subjectCode, @NotBlank @Size(min = 2, max = 200) String reason) {
    }

    public record UpdateVoucher(@NotBlank String voucherDate) {
    }

    public record Void(@NotBlank @Size(min = 2, max = 300) String reason) {
    }
}
