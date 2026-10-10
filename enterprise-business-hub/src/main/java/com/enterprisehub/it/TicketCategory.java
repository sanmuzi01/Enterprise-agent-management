package com.enterprisehub.it;

public enum TicketCategory {
    INCIDENT, ACCOUNT, PERMISSION, DEVICE, OTHER;

    /** 账号、权限、设备申请涉及资源/成本，必须先由申请人所在部门的负责人批准。 */
    public boolean needsApproval() {
        return this == ACCOUNT || this == PERMISSION || this == DEVICE;
    }
}
