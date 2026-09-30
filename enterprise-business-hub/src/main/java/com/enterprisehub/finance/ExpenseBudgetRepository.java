package com.enterprisehub.finance;

import org.springframework.data.jpa.repository.JpaRepository;

import java.util.Optional;

public interface ExpenseBudgetRepository extends JpaRepository<ExpenseBudget, Long> {
    Optional<ExpenseBudget> findByTeamIdAndYear(long teamId, int year);
}
