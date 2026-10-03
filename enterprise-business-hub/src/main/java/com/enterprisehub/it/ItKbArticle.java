package com.enterprisehub.it;

import jakarta.persistence.*;

@Entity
@Table(name = "it_kb_article")
public class ItKbArticle {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "category", nullable = false, length = 20)
    private String category;

    @Column(name = "title", nullable = false, length = 120)
    private String title;

    @Column(name = "keywords", nullable = false, length = 300)
    private String keywords;

    @Column(name = "steps", nullable = false, columnDefinition = "TEXT")
    private String steps;

    @Column(name = "enabled", nullable = false)
    private boolean enabled;

    protected ItKbArticle() {
    }

    public Long getId() { return id; }
    public String getCategory() { return category; }
    public String getTitle() { return title; }
    public String getKeywords() { return keywords; }
    public String getSteps() { return steps; }
    public boolean isEnabled() { return enabled; }
}
