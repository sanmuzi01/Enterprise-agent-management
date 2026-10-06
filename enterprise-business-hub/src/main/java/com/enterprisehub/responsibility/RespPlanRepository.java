package com.enterprisehub.responsibility;

import jakarta.persistence.LockModeType;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.JpaSpecificationExecutor;
import org.springframework.data.jpa.repository.Lock;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.util.Optional;

public interface RespPlanRepository extends JpaRepository<RespPlan, Long>, JpaSpecificationExecutor<RespPlan> {
    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("SELECT p FROM RespPlan p WHERE p.id = :id")
    Optional<RespPlan> findForUpdate(@Param("id") long id);

    Optional<RespPlan> findByAutomationWorkId(String automationWorkId);
}
