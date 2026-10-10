package com.enterprisehub.crm;

import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.util.List;

public interface ContactRepository extends JpaRepository<Contact, Long> {
    List<Contact> findByCustomerId(long customerId);

    @Query("select c from Contact c where c.customerId in (select cu.id from Customer cu where cu.teamId = :teamId) order by c.id")
    List<Contact> findByTeamId(@Param("teamId") long teamId);
}
