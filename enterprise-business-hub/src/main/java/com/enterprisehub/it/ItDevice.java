package com.enterprisehub.it;

import jakarta.persistence.*;

import java.time.Instant;
import java.time.LocalDate;

@Entity
@Table(name = "it_device")
public class ItDevice {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "asset_no", nullable = false, length = 40)
    private String assetNo;

    @Column(name = "device_type", nullable = false, length = 30)
    private String deviceType;

    @Column(name = "model", nullable = false, length = 100)
    private String model;

    @Enumerated(EnumType.STRING)
    @Column(name = "status", nullable = false, length = 20)
    private DeviceStatus status;

    @Column(name = "assignee_user_id")
    private Long assigneeUserId;

    @Column(name = "managing_team_id", nullable = false)
    private long managingTeamId;

    @Column(name = "purchased_on")
    private LocalDate purchasedOn;

    @Column(name = "warranty_until")
    private LocalDate warrantyUntil;

    @Column(name = "note", length = 300)
    private String note;

    @Column(name = "created_at", nullable = false)
    private Instant createdAt;

    @Column(name = "updated_at", nullable = false)
    private Instant updatedAt;

    protected ItDevice() {
    }

    public ItDevice(String assetNo, String deviceType, String model, long managingTeamId, LocalDate purchasedOn,
                    LocalDate warrantyUntil, String note) {
        this.assetNo = assetNo;
        this.deviceType = deviceType;
        this.model = model;
        this.managingTeamId = managingTeamId;
        this.purchasedOn = purchasedOn;
        this.warrantyUntil = warrantyUntil;
        this.note = note;
        this.status = DeviceStatus.IN_STOCK;
        this.createdAt = Instant.now();
        this.updatedAt = this.createdAt;
    }

    public void move(DeviceStatus status, Long assignee) {
        this.status = status;
        this.assigneeUserId = assignee;
        this.updatedAt = Instant.now();
    }

    public Long getId() { return id; }
    public String getAssetNo() { return assetNo; }
    public String getDeviceType() { return deviceType; }
    public String getModel() { return model; }
    public DeviceStatus getStatus() { return status; }
    public Long getAssigneeUserId() { return assigneeUserId; }
    public long getManagingTeamId() { return managingTeamId; }
    public LocalDate getPurchasedOn() { return purchasedOn; }
    public LocalDate getWarrantyUntil() { return warrantyUntil; }
    public String getNote() { return note; }
    public Instant getCreatedAt() { return createdAt; }
    public Instant getUpdatedAt() { return updatedAt; }
}
