package com.enterprisehub.crm.dto;

import com.enterprisehub.crm.Contact;

/** 联系人（客户自动关联用：按邮箱、手机号对上客户）。 */
public record ContactDto(long id, long customerId, String name, String title, String phone, String email) {
    public static ContactDto from(Contact c) {
        return new ContactDto(c.getId(), c.getCustomerId(), c.getName(), c.getTitle(), c.getPhone(), c.getEmail());
    }
}
