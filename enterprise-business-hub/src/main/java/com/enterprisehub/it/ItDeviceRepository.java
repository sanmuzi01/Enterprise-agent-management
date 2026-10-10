package com.enterprisehub.it;

import jakarta.persistence.LockModeType;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.JpaSpecificationExecutor;
import org.springframework.data.jpa.repository.Lock;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.util.List;
import java.util.Optional;

public interface ItDeviceRepository extends JpaRepository<ItDevice, Long>, JpaSpecificationExecutor<ItDevice> {
    Optional<ItDevice> findByAssetNo(String assetNo);

    List<ItDevice> findByAssigneeUserIdOrderById(long assigneeUserId);

    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("SELECT d FROM ItDevice d WHERE d.id = :id")
    Optional<ItDevice> findForUpdate(@Param("id") long id);
}
