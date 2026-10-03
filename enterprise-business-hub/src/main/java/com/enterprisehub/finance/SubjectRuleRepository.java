package com.enterprisehub.finance;

import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;

public interface SubjectRuleRepository extends JpaRepository<SubjectRule, Long> {
    List<SubjectRule> findByCategoryOrderByPriorityDescIdAsc(String category);
}
