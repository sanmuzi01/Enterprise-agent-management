package com.enterprisehub.procurement;

import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;

public interface PurchaseRequestRepository extends JpaRepository<PurchaseRequest, Long> {
    List<PurchaseRequest> findByRequesterUserIdOrderByCreatedAtDesc(long requesterUserId);

    List<PurchaseRequest> findByTeamIdAndStatusOrderByCreatedAtDesc(long teamId, PurchaseStatus status);
}
