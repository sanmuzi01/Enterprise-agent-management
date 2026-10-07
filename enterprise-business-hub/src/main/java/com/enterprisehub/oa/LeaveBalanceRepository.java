package com.enterprisehub.oa;

import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.repository.query.Param;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.jpa.repository.Lock;
import jakarta.persistence.LockModeType;

import java.util.List;
import java.util.Optional;

public interface LeaveBalanceRepository extends JpaRepository<LeaveBalance, Long> {
    Optional<LeaveBalance> findByUserIdAndLeaveTypeIdAndYear(long userId, long leaveTypeId, int year);

    List<LeaveBalance> findByUserIdAndYear(long userId, int year);
    /** 并发控制：读出来就要改的行必须加行锁，否则两个事务各读各的、各写各的，后写的覆盖先写的（重复审批、重复扣款）。 */
    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("SELECT b FROM LeaveBalance b WHERE b.userId = :userId AND b.leaveTypeId = :leaveTypeId AND b.year = :year")
    Optional<LeaveBalance> findForUpdate(@Param("userId") long userId, @Param("leaveTypeId") long leaveTypeId, @Param("year") int year);
}
