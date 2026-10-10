package com.enterprisehub.responsibility;

import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.util.Collection;
import java.util.List;

public interface RespDependencyRepository extends JpaRepository<RespDependency, Long> {
    List<RespDependency> findByTaskIdIn(Collection<Long> taskIds);

    @Modifying
    @Query("DELETE FROM RespDependency d WHERE d.taskId = :taskId OR d.dependsOnTaskId = :taskId")
    void deleteInvolving(@Param("taskId") long taskId);

    @Modifying
    @Query("DELETE FROM RespDependency d WHERE d.taskId = :taskId")
    void deleteByTaskId(@Param("taskId") long taskId);
}
