package com.enterprisehub.finance;

import jakarta.persistence.LockModeType;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.JpaSpecificationExecutor;
import org.springframework.data.jpa.repository.Lock;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.util.Collection;
import java.util.List;
import java.util.Optional;

public interface VoucherRepository extends JpaRepository<Voucher, Long>, JpaSpecificationExecutor<Voucher> {
    Optional<Voucher> findByExpenseClaimId(long expenseClaimId);

    /** 状态变更（确认/作废/改分录）前锁住凭证行，避免两个财务同时确认出两个凭证号。 */
    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("SELECT v FROM Voucher v WHERE v.id = :id")
    Optional<Voucher> findForUpdate(@Param("id") long id);

    /** 某期间已入账凭证按科目汇总：[code, name, direction, sum(amount), count(分录)]。 */
    @Query("SELECT e.subjectCode, e.subjectName, e.direction, SUM(e.amount), COUNT(e) "
            + "FROM VoucherEntry e, Voucher v WHERE e.voucherId = v.id AND v.status = "
            + "com.enterprisehub.finance.VoucherStatus.POSTED AND v.period = :period AND v.teamId IN :teamIds "
            + "GROUP BY e.subjectCode, e.subjectName, e.direction ORDER BY e.subjectCode, e.direction")
    List<Object[]> postedBySubject(@Param("period") String period, @Param("teamIds") Collection<Long> teamIds);

    /** 某期间已入账凭证按部门汇总：[teamId, count, sum(totalAmount)]。 */
    @Query("SELECT v.teamId, COUNT(v), SUM(v.totalAmount) FROM Voucher v WHERE v.status = "
            + "com.enterprisehub.finance.VoucherStatus.POSTED AND v.period = :period AND v.teamId IN :teamIds "
            + "GROUP BY v.teamId ORDER BY SUM(v.totalAmount) DESC")
    List<Object[]> postedByTeam(@Param("period") String period, @Param("teamIds") Collection<Long> teamIds);

    /** 范围内各状态的凭证数与金额：已入账/作废按期间，待确认草稿不限期间（积压的都要看到）。 */
    @Query("SELECT v.status, COUNT(v), SUM(v.totalAmount) FROM Voucher v WHERE v.teamId IN :teamIds "
            + "AND (v.status = com.enterprisehub.finance.VoucherStatus.DRAFT OR v.period = :period) "
            + "GROUP BY v.status")
    List<Object[]> countByStatus(@Param("period") String period, @Param("teamIds") Collection<Long> teamIds);

    /** 某期间已入账凭证里，至少有一条分录被人工改过科目的凭证数。 */
    @Query("SELECT COUNT(DISTINCT v.id) FROM Voucher v, VoucherEntry e WHERE e.voucherId = v.id AND "
            + "e.manualOverride = true AND v.status = com.enterprisehub.finance.VoucherStatus.POSTED "
            + "AND v.period = :period AND v.teamId IN :teamIds")
    long countPostedWithManualOverride(@Param("period") String period, @Param("teamIds") Collection<Long> teamIds);

    /** 某期间已入账凭证的 [createdAt, postedAt]，用来算从自动生成到财务入账的平均耗时。 */
    @Query("SELECT v.createdAt, v.postedAt FROM Voucher v WHERE v.status = "
            + "com.enterprisehub.finance.VoucherStatus.POSTED AND v.period = :period AND v.teamId IN :teamIds "
            + "AND v.postedAt IS NOT NULL")
    List<Object[]> postedTimings(@Param("period") String period, @Param("teamIds") Collection<Long> teamIds);

    /** 待确认草稿里带风险的数量：[risk_level, count]。 */
    @Query("SELECT v.riskLevel, COUNT(v) FROM Voucher v WHERE v.teamId IN :teamIds AND v.status = "
            + "com.enterprisehub.finance.VoucherStatus.DRAFT GROUP BY v.riskLevel")
    List<Object[]> draftRiskCounts(@Param("teamIds") Collection<Long> teamIds);
}
