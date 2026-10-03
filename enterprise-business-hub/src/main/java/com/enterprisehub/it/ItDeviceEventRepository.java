package com.enterprisehub.it;

import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;

public interface ItDeviceEventRepository extends JpaRepository<ItDeviceEvent, Long> {
    List<ItDeviceEvent> findByDeviceIdOrderByIdAsc(long deviceId);
}
