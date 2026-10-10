package com.enterprisehub.crm.dto;

import com.enterprisehub.crm.Opportunity;

import java.math.BigDecimal;
import java.time.Instant;
import java.time.LocalDate;

public record OpportunityDto(long id, long customerId, String stage, BigDecimal amount,
                              long ownerUserId, long teamId, Instant createdAt, Instant updatedAt,
                              LocalDate expectedCloseDate, String nextStep) {
    public static OpportunityDto from(Opportunity o) {
        return new OpportunityDto(o.getId(), o.getCustomerId(), o.getStage().name(), o.getAmount(),
                o.getOwnerUserId(), o.getTeamId(), o.getCreatedAt(), o.getUpdatedAt(),
                o.getExpectedCloseDate(), o.getNextStep());
    }
}
