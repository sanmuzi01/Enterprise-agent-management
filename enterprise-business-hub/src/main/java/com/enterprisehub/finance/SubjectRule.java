package com.enterprisehub.finance;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.Table;

/** 科目建议规则：keyword 为空表示该费用类别的默认科目。 */
@Entity
@Table(name = "subject_rule")
public class SubjectRule {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "category", nullable = false, length = 40)
    private String category;

    @Column(name = "keyword", length = 40)
    private String keyword;

    @Column(name = "subject_suffix", nullable = false, length = 10)
    private String subjectSuffix;

    @Column(name = "priority", nullable = false)
    private int priority;

    @Column(name = "confidence", nullable = false, length = 10)
    private String confidence;

    @Column(name = "note", nullable = false, length = 120)
    private String note;

    protected SubjectRule() {
    }

    public String getCategory() {
        return category;
    }

    public String getKeyword() {
        return keyword;
    }

    public String getSubjectSuffix() {
        return subjectSuffix;
    }

    public int getPriority() {
        return priority;
    }

    public String getConfidence() {
        return confidence;
    }

    public String getNote() {
        return note;
    }
}
