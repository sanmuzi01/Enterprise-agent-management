package com.enterprisehub.finance;

import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;

public interface AccountSubjectRepository extends JpaRepository<AccountSubject, String> {
    List<AccountSubject> findByEnabledTrueOrderByCode();
}
