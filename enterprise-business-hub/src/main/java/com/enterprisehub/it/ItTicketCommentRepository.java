package com.enterprisehub.it;

import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;

public interface ItTicketCommentRepository extends JpaRepository<ItTicketComment, Long> {
    List<ItTicketComment> findByTicketIdOrderByIdAsc(long ticketId);
}
