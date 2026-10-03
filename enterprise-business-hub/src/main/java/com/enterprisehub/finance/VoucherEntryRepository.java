package com.enterprisehub.finance;

import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;

public interface VoucherEntryRepository extends JpaRepository<VoucherEntry, Long> {
    List<VoucherEntry> findByVoucherIdOrderByLineNo(long voucherId);

    void deleteByVoucherId(long voucherId);
}
