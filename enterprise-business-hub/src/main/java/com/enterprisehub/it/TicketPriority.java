package com.enterprisehub.it;

public enum TicketPriority {
    LOW(72), NORMAL(24), HIGH(8), URGENT(4);

    private final int slaHours;

    TicketPriority(int slaHours) {
        this.slaHours = slaHours;
    }

    /** 从开始计时到应当解决的小时数。 */
    public int slaHours() {
        return slaHours;
    }
}
