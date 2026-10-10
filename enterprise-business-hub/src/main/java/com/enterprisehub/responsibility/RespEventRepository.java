package com.enterprisehub.responsibility;

import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.Repository;
import org.springframework.data.repository.query.Param;

import java.util.Collection;
import java.util.List;

/** 履责事件只允许追加和查询：这里刻意不继承 JpaRepository，所以应用层没有任何更新或删除入口。 */
public interface RespEventRepository extends Repository<RespEvent, Long> {
    RespEvent save(RespEvent event);

    List<RespEvent> findByPlanIdOrderByIdAsc(long planId);

    List<RespEvent> findByTaskIdOrderByIdAsc(long taskId);

    @Query("SELECT e FROM RespEvent e WHERE e.planId IN :planIds AND e.eventType IN :types ORDER BY e.id")
    List<RespEvent> findByPlansAndTypes(@Param("planIds") Collection<Long> planIds, @Param("types") Collection<String> types);
}
