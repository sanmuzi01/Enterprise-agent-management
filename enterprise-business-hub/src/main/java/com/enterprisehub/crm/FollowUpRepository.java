package com.enterprisehub.crm;

import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.repository.query.Param;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.jpa.repository.Lock;
import jakarta.persistence.LockModeType;

import java.util.List;

public interface FollowUpRepository extends JpaRepository<FollowUp, Long> {
    List<FollowUp> findByCustomerIdOrderByCreatedAtDesc(long customerId);
    /** 并发控制：读出来就要改的行必须加行锁，否则两个事务各读各的、各写各的，后写的覆盖先写的（重复审批、重复扣款）。 */
    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("SELECT f FROM FollowUp f WHERE f.id = :id")
    java.util.Optional<FollowUp> findForUpdate(@Param("id") long id);
}
