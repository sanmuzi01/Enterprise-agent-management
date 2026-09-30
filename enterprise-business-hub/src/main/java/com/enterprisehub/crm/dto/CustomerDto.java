package com.enterprisehub.crm.dto;

import com.enterprisehub.crm.Customer;

import java.time.Instant;

/** 客户列表用的轻量 DTO——不像 {@link CustomerSummaryDto} 那样带联系人/跟进/商机的
 * 嵌套数据，列表页只需要基本字段，选中某个客户后再单独调
 * GET /crm/customers/{id} 拿完整摘要。 */
public record CustomerDto(long id, String name, String industry, long ownerUserId, long teamId, Instant createdAt) {
    public static CustomerDto from(Customer c) {
        return new CustomerDto(c.getId(), c.getName(), c.getIndustry(), c.getOwnerUserId(), c.getTeamId(),
                c.getCreatedAt());
    }
}
