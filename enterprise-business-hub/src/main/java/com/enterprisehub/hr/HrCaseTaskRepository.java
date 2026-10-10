package com.enterprisehub.hr;

import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;

public interface HrCaseTaskRepository extends JpaRepository<HrCaseTask, Long> {
    List<HrCaseTask> findByCaseIdOrderBySeq(long caseId);

    List<HrCaseTask> findByCaseIdIn(java.util.Collection<Long> caseIds);
}
