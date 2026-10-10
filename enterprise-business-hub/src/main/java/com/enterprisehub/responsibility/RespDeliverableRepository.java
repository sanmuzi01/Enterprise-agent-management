package com.enterprisehub.responsibility;

import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;

public interface RespDeliverableRepository extends JpaRepository<RespDeliverable, Long> {
    List<RespDeliverable> findByTaskIdOrderBySubmissionNoDesc(long taskId);

    int countByTaskId(long taskId);
}
