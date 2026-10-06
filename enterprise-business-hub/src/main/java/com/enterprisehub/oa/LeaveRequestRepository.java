package com.enterprisehub.oa;

import org.springframework.data.jpa.repository.JpaRepository;

import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.time.LocalDate;
import java.util.Collection;
import java.util.List;

public interface LeaveRequestRepository extends JpaRepository<LeaveRequest, Long> {
    List<LeaveRequest> findByApplicantUserIdOrderByCreatedAtDesc(long applicantUserId);

    List<LeaveRequest> findByTeamIdAndStatusOrderByCreatedAtDesc(long teamId, LeaveStatus status);

    /** 与 [from, to] 有重叠的已批准请假（考勤异常判断用：请假当天缺卡不算旷工）。 */
    @Query("SELECT r FROM LeaveRequest r WHERE r.teamId IN :teamIds AND r.status = com.enterprisehub.oa.LeaveStatus.APPROVED "
            + "AND r.startDate <= :to AND r.endDate >= :from ORDER BY r.startDate")
    List<LeaveRequest> findApprovedOverlapping(@Param("teamIds") Collection<Long> teamIds, @Param("from") LocalDate from, @Param("to") LocalDate to);
}
