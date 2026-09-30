package com.enterprisehub.crm;

import com.enterprisehub.audit.AuditService;
import com.enterprisehub.crm.dto.CustomerDto;
import com.enterprisehub.crm.dto.CustomerSummaryDto;
import com.enterprisehub.crm.dto.FollowUpDto;
import com.enterprisehub.crm.dto.OpportunityDto;
import com.enterprisehub.crm.dto.UpsertOpportunityRequest;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;

import java.util.List;

/**
 * CRM 客户跟进闭环（docs/enterprise-business-hub-plan.md 第4节）：查客户摘要
 * （联系人+历史跟进+商机，给销售 Agent 生成摘要用）→ 建跟进草稿 → 用户确认 →
 * 创建/更新商机。没有审批环节——设计稿本来就没提这个，用户确认自己的跟进内容
 * 就够了，比 OA/采购简单。
 *
 * 部门数据隔离：客户/商机都带 `teamId`，任何操作都要求调用方 `RequestContext.teamId`
 * 跟客户的 `teamId` 一致，不一致统一 404（不暴露资源存在性，跟 Python 侧
 * `access_control.py` 的隔离原则一致）。
 */
@Service
public class CrmService {
    private final CustomerRepository customerRepository;
    private final ContactRepository contactRepository;
    private final FollowUpRepository followUpRepository;
    private final OpportunityRepository opportunityRepository;
    private final AuditService auditService;

    public CrmService(CustomerRepository customerRepository, ContactRepository contactRepository,
                       FollowUpRepository followUpRepository, OpportunityRepository opportunityRepository,
                       AuditService auditService) {
        this.customerRepository = customerRepository;
        this.contactRepository = contactRepository;
        this.followUpRepository = followUpRepository;
        this.opportunityRepository = opportunityRepository;
        this.auditService = auditService;
    }

    public CustomerSummaryDto getCustomerSummary(long customerId, long requestTeamId) {
        Customer customer = getCustomerInTeam(customerId, requestTeamId);

        List<CustomerSummaryDto.ContactDto> contacts = contactRepository.findByCustomerId(customerId).stream()
                .map(c -> new CustomerSummaryDto.ContactDto(c.getName(), c.getTitle(), c.getPhone(), c.getEmail()))
                .toList();
        List<FollowUpDto> followUps = followUpRepository.findByCustomerIdOrderByCreatedAtDesc(customerId).stream()
                .map(FollowUpDto::from)
                .limit(10)
                .toList();
        List<OpportunityDto> opportunities = opportunityRepository.findByCustomerId(customerId).stream()
                .map(OpportunityDto::from)
                .toList();

        return new CustomerSummaryDto(customer.getId(), customer.getName(), customer.getIndustry(),
                customer.getOwnerUserId(), customer.getTeamId(), contacts, followUps, opportunities);
    }

    @Transactional
    public FollowUpDto createFollowUpDraft(long customerId, long requestTeamId, long authorUserId,
                                            String content, String traceId) {
        getCustomerInTeam(customerId, requestTeamId);
        FollowUp followUp = new FollowUp(customerId, authorUserId, content);
        followUpRepository.save(followUp);
        auditService.record(authorUserId, "crm.followup_draft_created", "follow_up", followUp.getId(),
                null, traceId);
        return FollowUpDto.from(followUp);
    }

    @Transactional
    public FollowUpDto confirmFollowUp(long followUpId, long authorUserId, String traceId) {
        FollowUp followUp = followUpRepository.findById(followUpId)
                .orElseThrow(() -> notFound("跟进记录不存在"));
        if (followUp.getAuthorUserId() != authorUserId) {
            throw notFound("跟进记录不存在");
        }
        if (followUp.getStatus() != FollowUpStatus.DRAFT) {
            throw badRequest("只有草稿状态的跟进记录能确认，当前状态: " + followUp.getStatus());
        }
        followUp.confirm();
        auditService.record(authorUserId, "crm.followup_confirmed", "follow_up", followUp.getId(), null, traceId);
        return FollowUpDto.from(followUp);
    }

    @Transactional
    public OpportunityDto upsertOpportunity(long customerId, long requestTeamId, long ownerUserId,
                                             UpsertOpportunityRequest body, String traceId) {
        getCustomerInTeam(customerId, requestTeamId);
        OpportunityStage stage = parseStage(body.stage());

        Opportunity opportunity;
        String action;
        if (body.opportunityId() != null) {
            opportunity = opportunityRepository.findById(body.opportunityId())
                    .orElseThrow(() -> notFound("商机不存在"));
            if (opportunity.getCustomerId() != customerId || opportunity.getTeamId() != requestTeamId) {
                throw notFound("商机不存在");
            }
            opportunity.update(stage, body.amount());
            action = "crm.opportunity_updated";
        } else {
            opportunity = new Opportunity(customerId, stage, body.amount(), ownerUserId, requestTeamId);
            opportunityRepository.save(opportunity);
            action = "crm.opportunity_created";
        }
        auditService.record(ownerUserId, action, "opportunity", opportunity.getId(),
                "{\"stage\":\"" + stage + "\",\"amount\":" + body.amount() + "}", traceId);
        return OpportunityDto.from(opportunity);
    }

    public List<OpportunityDto> listOpportunities(long customerId, long requestTeamId) {
        getCustomerInTeam(customerId, requestTeamId);
        return opportunityRepository.findByCustomerId(customerId).stream().map(OpportunityDto::from).toList();
    }

    /** 部门工作台里程碑3新增：本部门客户列表。跟审批类操作不同，CRM 里任何部门
     * 成员都能看本部门客户，不需要额外判断负责人角色。 */
    public List<CustomerDto> listCustomers(long teamId) {
        return customerRepository.findByTeamIdOrderByCreatedAtDesc(teamId).stream().map(CustomerDto::from).toList();
    }

    private Customer getCustomerInTeam(long customerId, long requestTeamId) {
        Customer customer = customerRepository.findById(customerId).orElseThrow(() -> notFound("客户不存在"));
        if (customer.getTeamId() != requestTeamId) {
            throw notFound("客户不存在");
        }
        return customer;
    }

    private OpportunityStage parseStage(String raw) {
        try {
            return OpportunityStage.valueOf(raw.toUpperCase());
        } catch (IllegalArgumentException e) {
            throw badRequest("未知的商机阶段: " + raw);
        }
    }

    private ResponseStatusException badRequest(String message) {
        return new ResponseStatusException(HttpStatus.BAD_REQUEST, message);
    }

    private ResponseStatusException notFound(String message) {
        return new ResponseStatusException(HttpStatus.NOT_FOUND, message);
    }
}
