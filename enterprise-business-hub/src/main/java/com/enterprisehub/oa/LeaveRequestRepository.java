package com.enterprisehub.oa;

import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Lock;
import jakarta.persistence.LockModeType;

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
    /** 并发控制：读出来就要改的行必须加行锁，否则两个事务各读各的、各写各的，后写的覆盖先写的（重复审批、重复扣款）。 */
    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("SELECT r FROM LeaveRequest r WHERE r.id = :id")
    java.util.Optional<LeaveRequest> findForUpdate(@Param("id") long id);
}
