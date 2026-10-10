package com.enterprisehub.finance;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;

/** 科目表条目；凭证建议和人工改科目都只能取这里 enabled 的科目。 */
@Entity
@Table(name = "account_subject")
public class AccountSubject {
    @Id
    @Column(name = "code", length = 20)
    private String code;

    @Column(name = "name", nullable = false, length = 60)
    private String name;

    @Column(name = "category", nullable = false, length = 20)
    private String category;

    @Column(name = "direction", nullable = false, length = 1)
    private String direction;

    @Column(name = "enabled", nullable = false)
    private boolean enabled;

    protected AccountSubject() {
    }

    public String getCode() {
        return code;
    }

    public String getName() {
        return name;
    }

    public String getCategory() {
        return category;
    }

    public String getDirection() {
        return direction;
    }

    public boolean isEnabled() {
        return enabled;
    }
}
