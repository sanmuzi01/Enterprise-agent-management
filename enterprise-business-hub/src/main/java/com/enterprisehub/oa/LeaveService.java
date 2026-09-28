package com.enterprisehub.oa;

import com.enterprisehub.audit.AuditService;
import com.enterprisehub.oa.dto.LeaveBalanceDto;
import com.enterprisehub.oa.dto.LeaveRequestDto;
import com.enterprisehub.security.TeamAccessGuard;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;

import java.time.LocalDate;
import java.time.temporal.ChronoUnit;
import java.util.List;

/**
 * OA 请假闭环（docs/enterprise-business-hub-plan.md 第4节）：查余额 → 建草稿 → 提交 →
 * 部门负责人审批 → 查状态。余额在**批准时**才真正扣减（不是提交时预扣），
 * 拒绝/撤销不影响余额——第一版这样最简单，够用；预扣/释放这种更严格的库存式占用
 * 留给真有需求再加。
 */
@Service
public class LeaveService {
    private final LeaveTypeRepository leaveTypeRepository;
    private final LeaveBalanceRepository leaveBalanceRepository;
    private final LeaveRequestRepository leaveRequestRepository;
    private final AuditService auditService;

    public LeaveService(LeaveTypeRepository leaveTypeRepository, LeaveBalanceRepository leaveBalanceRepository,
                         LeaveRequestRepository leaveRequestRepository, AuditService auditService) {
        this.leaveTypeRepository = leaveTypeRepository;
        this.leaveBalanceRepository = leaveBalanceRepository;
        this.leaveRequestRepository = leaveRequestRepository;
        this.auditService = auditService;
    }

    public List<LeaveBalanceDto> getBalances(long userId, int year) {
        return leaveBalanceRepository.findByUserIdAndYear(userId, year).stream()
                .map(b -> {
                    LeaveType type = leaveTypeRepository.findById(b.getLeaveTypeId())
                            .orElseThrow(() -> notFound("请假类型不存在"));
                    return new LeaveBalanceDto(type.getCode(), type.getName(), b.getRemainingDays(), b.getYear());
                })
                .toList();
    }

    @Transactional
    public LeaveRequestDto createDraft(long applicantUserId, Long teamId, String leaveTypeCode,
                                        LocalDate startDate, LocalDate endDate, String reason, String traceId) {
        LeaveType type = leaveTypeRepository.findByCode(leaveTypeCode)
                .orElseThrow(() -> badRequest("未知的请假类型: " + leaveTypeCode));
        if (endDate.isBefore(startDate)) {
            throw badRequest("结束日期不能早于开始日期");
        }
        double days = ChronoUnit.DAYS.between(startDate, endDate) + 1;

        LeaveRequest request = new LeaveRequest(applicantUserId, teamId, type.getId(), startDate, endDate, days, reason);
        leaveRequestRepository.save(request);
        auditService.record(applicantUserId, "oa.leave.draft_created", "leave_request", request.getId(),
                "{\"leaveType\":\"" + leaveTypeCode + "\",\"days\":" + days + "}", traceId);
        return LeaveRequestDto.from(request, type);
    }

    @Transactional
    public LeaveRequestDto submit(long requestId, long applicantUserId, String traceId) {
        LeaveRequest request = getOwnedDraft(requestId, applicantUserId);
        LeaveType type = leaveTypeRepository.findById(request.getLeaveTypeId()).orElseThrow();

        int year = request.getStartDate().getYear();
        LeaveBalance balance = leaveBalanceRepository
                .findByUserIdAndLeaveTypeIdAndYear(applicantUserId, request.getLeaveTypeId(), year)
                .orElseThrow(() -> badRequest("没有 " + year + " 年度的 " + type.getName() + " 余额记录"));
        if (balance.getRemainingDays() < request.getDays()) {
            throw badRequest("余额不足：还剩 " + balance.getRemainingDays() + " 天，申请了 " + request.getDays() + " 天");
        }

        request.submit();
        auditService.record(applicantUserId, "oa.leave.submitted", "leave_request", request.getId(), null, traceId);
        return LeaveRequestDto.from(request, type);
    }

    @Transactional
    public LeaveRequestDto approve(long requestId, long approverUserId, String note, String traceId,
                                    Long approverTeamId, boolean isOrgAdmin, boolean isTeamAdmin) {
        LeaveRequest request = getSubmitted(requestId);
        TeamAccessGuard.requireTeamAccess(approverTeamId, isOrgAdmin, isTeamAdmin, request.getTeamId());
        if (request.getApplicantUserId() == approverUserId) {
            throw badRequest("不能审批自己提交的申请，需要由部门负责人或企业管理员处理");
        }
        LeaveType type = leaveTypeRepository.findById(request.getLeaveTypeId()).orElseThrow();

        LeaveBalance balance = leaveBalanceRepository
                .findByUserIdAndLeaveTypeIdAndYear(request.getApplicantUserId(), request.getLeaveTypeId(),
                        request.getStartDate().getYear())
                .orElseThrow(() -> badRequest("余额记录不存在，无法批准"));
        if (balance.getRemainingDays() < request.getDays()) {
            throw badRequest("批准时余额不足（可能已被其它已批准的申请占用）");
        }
        balance.deduct(request.getDays());
        request.approve(approverUserId, note);
        auditService.record(approverUserId, "oa.leave.approved", "leave_request", request.getId(), note, traceId);
        return LeaveRequestDto.from(request, type);
    }

    @Transactional
    public LeaveRequestDto reject(long requestId, long approverUserId, String note, String traceId,
                                   Long approverTeamId, boolean isOrgAdmin, boolean isTeamAdmin) {
        LeaveRequest request = getSubmitted(requestId);
        TeamAccessGuard.requireTeamAccess(approverTeamId, isOrgAdmin, isTeamAdmin, request.getTeamId());
        if (request.getApplicantUserId() == approverUserId) {
            throw badRequest("不能处理自己提交的申请，需要由部门负责人或企业管理员处理");
        }
        LeaveType type = leaveTypeRepository.findById(request.getLeaveTypeId()).orElseThrow();
        request.reject(approverUserId, note);
        auditService.record(approverUserId, "oa.leave.rejected", "leave_request", request.getId(), note, traceId);
        return LeaveRequestDto.from(request, type);
    }

    public LeaveRequestDto getStatus(long requestId, long requesterUserId, Long requesterTeamId,
                                      boolean isOrgAdmin, boolean isTeamAdmin) {
        LeaveRequest request = leaveRequestRepository.findById(requestId)
                .orElseThrow(() -> notFound("请假单不存在"));
        TeamAccessGuard.requireOwnerOrTeamAccess(request.getApplicantUserId(), requesterUserId, requesterTeamId,
                isOrgAdmin, isTeamAdmin, request.getTeamId());
        LeaveType type = leaveTypeRepository.findById(request.getLeaveTypeId()).orElseThrow();
        return LeaveRequestDto.from(request, type);
    }

    private LeaveRequest getOwnedDraft(long requestId, long applicantUserId) {
        LeaveRequest request = leaveRequestRepository.findById(requestId)
                .orElseThrow(() -> notFound("请假单不存在"));
        if (request.getApplicantUserId() != applicantUserId) {
            throw new ResponseStatusException(HttpStatus.NOT_FOUND, "请假单不存在");
        }
        if (request.getStatus() != LeaveStatus.DRAFT) {
            throw badRequest("只有草稿状态的请假单能提交，当前状态: " + request.getStatus());
        }
        return request;
    }

    private LeaveRequest getSubmitted(long requestId) {
        LeaveRequest request = leaveRequestRepository.findById(requestId)
                .orElseThrow(() -> notFound("请假单不存在"));
        if (request.getStatus() != LeaveStatus.SUBMITTED) {
            throw badRequest("只有已提交状态的请假单能审批，当前状态: " + request.getStatus());
        }
        return request;
    }

    private ResponseStatusException badRequest(String message) {
        return new ResponseStatusException(HttpStatus.BAD_REQUEST, message);
    }

    private ResponseStatusException notFound(String message) {
        return new ResponseStatusException(HttpStatus.NOT_FOUND, message);
    }
}
