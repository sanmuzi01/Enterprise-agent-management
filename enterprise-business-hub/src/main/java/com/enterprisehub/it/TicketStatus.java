package com.enterprisehub.it;

public enum TicketStatus {
    PENDING_APPROVAL, OPEN, IN_PROGRESS, WAITING_USER, RESOLVED, CLOSED, CANCELLED, REJECTED;

    /** 还没有处理完的状态：计入 SLA、可被指派。 */
    public boolean isActive() {
        return this == OPEN || this == IN_PROGRESS || this == WAITING_USER;
    }
}
