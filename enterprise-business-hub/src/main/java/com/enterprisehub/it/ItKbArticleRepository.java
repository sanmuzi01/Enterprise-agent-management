package com.enterprisehub.it;

import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;

public interface ItKbArticleRepository extends JpaRepository<ItKbArticle, Long> {
    List<ItKbArticle> findByEnabledTrueOrderById();
}
