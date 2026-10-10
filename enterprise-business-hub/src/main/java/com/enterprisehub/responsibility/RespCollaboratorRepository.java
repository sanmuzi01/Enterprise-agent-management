package com.enterprisehub.responsibility;

import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.util.Collection;
import java.util.List;

public interface RespCollaboratorRepository extends JpaRepository<RespCollaborator, Long> {
    List<RespCollaborator> findByTaskIdIn(Collection<Long> taskIds);

    List<RespCollaborator> findByUserId(long userId);

    @Modifying
    @Query("DELETE FROM RespCollaborator c WHERE c.taskId = :taskId")
    void deleteByTaskId(@Param("taskId") long taskId);
}
