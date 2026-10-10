package com.enterprisehub.finance;

import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.repository.query.Param;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.jpa.repository.Lock;
import jakarta.persistence.LockModeType;

import java.util.Optional;

public interface ExpenseBudgetRepository extends JpaRepository<ExpenseBudget, Long> {
    Optional<ExpenseBudget> findByTeamIdAndYear(long teamId, int year);
    /** 并发控制：读出来就要改的行必须加行锁，否则两个事务各读各的、各写各的，后写的覆盖先写的（重复审批、重复扣款）。 */
    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("SELECT b FROM ExpenseBudget b WHERE b.teamId = :teamId AND b.year = :year")
    Optional<ExpenseBudget> findForUpdate(@Param("teamId") long teamId, @Param("year") int year);
}
