package com.enterprisehub.responsibility;

import jakarta.persistence.LockModeType;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.JpaSpecificationExecutor;
import org.springframework.data.jpa.repository.Lock;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.time.LocalDate;
import java.util.Collection;
import java.util.List;
import java.util.Optional;

public interface RespTaskRepository extends JpaRepository<RespTask, Long>, JpaSpecificationExecutor<RespTask> {
    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("SELECT t FROM RespTask t WHERE t.id = :id")
    Optional<RespTask> findForUpdate(@Param("id") long id);

    List<RespTask> findByPlanIdOrderBySeq(long planId);

    List<RespTask> findByPlanIdIn(Collection<Long> planIds);

    /** 同一主责人名下、截止日在区间内、尚未结束的高优先级责任（过载提示用）。 */
    @Query("SELECT t FROM RespTask t WHERE t.responsibleUserId = :user AND t.priority IN ('HIGH', 'URGENT') "
            + "AND t.status NOT IN ('DONE', 'CANCELLED') AND t.dueDate BETWEEN :from AND :to AND t.id <> :excludeId")
    List<RespTask> findHeavy(@Param("user") long user, @Param("from") LocalDate from, @Param("to") LocalDate to,
                             @Param("excludeId") long excludeId);
}
