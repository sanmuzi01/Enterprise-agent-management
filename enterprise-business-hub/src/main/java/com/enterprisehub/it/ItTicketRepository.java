package com.enterprisehub.it;

import jakarta.persistence.LockModeType;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.JpaSpecificationExecutor;
import org.springframework.data.jpa.repository.Lock;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.time.Instant;
import java.util.Collection;
import java.util.List;
import java.util.Optional;

public interface ItTicketRepository extends JpaRepository<ItTicket, Long>, JpaSpecificationExecutor<ItTicket> {
    List<ItTicket> findByRequesterUserIdOrderByIdDesc(long requesterUserId);

    List<ItTicket> findByTeamIdAndStatusOrderByIdDesc(long teamId, TicketStatus status);

    /** 状态变更前锁行：两个 IT 人员同时接单/解决同一张工单时，后到的看到最新状态。 */
    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("SELECT t FROM ItTicket t WHERE t.id = :id")
    Optional<ItTicket> findForUpdate(@Param("id") long id);

    /** 同一申请人名下、同一标题的未结束工单（提交前提示重复）。 */
    @Query("SELECT t FROM ItTicket t WHERE t.requesterUserId = :user AND t.title = :title AND t.status IN :statuses ORDER BY t.id")
    List<ItTicket> findOpenByTitle(@Param("user") long user, @Param("title") String title,
                                   @Param("statuses") Collection<TicketStatus> statuses);

    /** 范围内各状态工单数：[status, count]。 */
    @Query("SELECT t.status, COUNT(t) FROM ItTicket t WHERE t.teamId IN :teamIds GROUP BY t.status")
    List<Object[]> countByStatus(@Param("teamIds") Collection<Long> teamIds);

    /** 范围内处理中的工单按处理人的数量：[assignee, count]。 */
    @Query("SELECT t.assigneeUserId, COUNT(t) FROM ItTicket t WHERE t.teamId IN :teamIds AND t.assigneeUserId IS NOT NULL "
            + "AND t.status IN (com.enterprisehub.it.TicketStatus.IN_PROGRESS, com.enterprisehub.it.TicketStatus.WAITING_USER) "
            + "GROUP BY t.assigneeUserId ORDER BY COUNT(t) DESC")
    List<Object[]> openLoadByAssignee(@Param("teamIds") Collection<Long> teamIds);

    /** 某时间段内创建的工单按分类的数量：[category, count]。 */
    @Query("SELECT t.category, COUNT(t) FROM ItTicket t WHERE t.teamId IN :teamIds AND t.createdAt >= :from AND t.createdAt < :to "
            + "GROUP BY t.category")
    List<Object[]> countByCategory(@Param("teamIds") Collection<Long> teamIds, @Param("from") Instant from,
                                   @Param("to") Instant to);

    /** 某时间段内解决的工单：[createdAt, firstResponseAt, resolvedAt, slaDueAt]。 */
    @Query("SELECT t.createdAt, t.firstResponseAt, t.resolvedAt, t.slaDueAt FROM ItTicket t WHERE t.teamId IN :teamIds "
            + "AND t.resolvedAt IS NOT NULL AND t.resolvedAt >= :from AND t.resolvedAt < :to")
    List<Object[]> resolvedTimings(@Param("teamIds") Collection<Long> teamIds, @Param("from") Instant from,
                                   @Param("to") Instant to);

    /** 范围内当前已超过 SLA 的未处理完工单数。 */
    @Query("SELECT COUNT(t) FROM ItTicket t WHERE t.teamId IN :teamIds AND t.slaDueAt < :now AND t.status IN "
            + "(com.enterprisehub.it.TicketStatus.OPEN, com.enterprisehub.it.TicketStatus.IN_PROGRESS)")
    long countOverdue(@Param("teamIds") Collection<Long> teamIds, @Param("now") Instant now);
}
