package com.enterprisehub.hr;

import jakarta.persistence.LockModeType;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.JpaSpecificationExecutor;
import org.springframework.data.jpa.repository.Lock;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.util.Collection;
import java.util.List;
import java.util.Optional;

public interface HrCaseRepository extends JpaRepository<HrCase, Long>, JpaSpecificationExecutor<HrCase> {
    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("SELECT c FROM HrCase c WHERE c.id = :id")
    Optional<HrCase> findForUpdate(@Param("id") long id);

    /** 同一员工名下进行中（待批准/办理中）的事项，用来拦截重复办理。 */
    @Query("SELECT c FROM HrCase c WHERE c.employeeUserId = :employee AND c.status IN ('PENDING_APPROVAL', 'IN_PROGRESS') ORDER BY c.id")
    List<HrCase> findActiveByEmployee(@Param("employee") long employee);

    @Query("SELECT c FROM HrCase c WHERE c.employeeUserId = :employee AND c.caseType = :type AND c.status = 'COMPLETED' ORDER BY c.id DESC")
    List<HrCase> findCompleted(@Param("employee") long employee, @Param("type") HrCaseType type);

    /** 范围内各类型×状态的数量：[type, status, count]。 */
    @Query("SELECT c.caseType, c.status, COUNT(c) FROM HrCase c WHERE c.teamId IN :teamIds GROUP BY c.caseType, c.status")
    List<Object[]> countByTypeStatus(@Param("teamIds") Collection<Long> teamIds);

    /** 已办结的事项 [createdAt, completedAt]，算平均办理天数。 */
    @Query("SELECT c.createdAt, c.completedAt FROM HrCase c WHERE c.teamId IN :teamIds AND c.status = 'COMPLETED' AND c.completedAt IS NOT NULL")
    List<Object[]> completedTimings(@Param("teamIds") Collection<Long> teamIds);
}
