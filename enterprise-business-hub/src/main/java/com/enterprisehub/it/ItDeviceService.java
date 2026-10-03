package com.enterprisehub.it;

import com.enterprisehub.audit.AuditService;
import com.enterprisehub.it.dto.ItDtos.*;
import jakarta.persistence.criteria.Predicate;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.domain.Sort;
import org.springframework.data.jpa.domain.Specification;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDate;
import java.time.ZoneId;
import java.time.format.DateTimeParseException;
import java.util.ArrayList;
import java.util.Collection;
import java.util.List;
import java.util.Set;

import static com.enterprisehub.it.ItTicketService.badRequest;
import static com.enterprisehub.it.ItTicketService.notFound;

/**
 * 设备台账与生命周期：入库 → 领用（可关联设备申请工单）→ 归还 / 送修 / 修好 → 报废。
 * 每次变动写设备事件（谁、对谁、关联哪张工单），状态机在这里强制：已领用的设备必须先收回才能报废，
 * 同一台设备不会被同时领用给两个人（行锁）。管理范围由 scopeTeamIds（同一企业）限定。
 */
@Service
public class ItDeviceService {
    private static final Set<String> TYPES = Set.of("LAPTOP", "DESKTOP", "MONITOR", "PHONE", "PERIPHERAL", "OTHER");
    private static final ZoneId BEIJING = ZoneId.of("Asia/Shanghai");

    private final ItDeviceRepository devices;
    private final ItDeviceEventRepository events;
    private final ItTicketRepository tickets;
    private final ItTicketCommentRepository comments;
    private final AuditService audit;

    public ItDeviceService(ItDeviceRepository devices, ItDeviceEventRepository events, ItTicketRepository tickets,
                           ItTicketCommentRepository comments, AuditService audit) {
        this.devices = devices;
        this.events = events;
        this.tickets = tickets;
        this.comments = comments;
        this.audit = audit;
    }

    @Transactional
    public DeviceDto create(CreateDevice request, long actorId, Collection<Long> scope, String traceId) {
        if (!scope.contains(request.managingTeamId())) {
            throw notFound("管理部门不存在");
        }
        String type = request.deviceType().trim().toUpperCase();
        if (!TYPES.contains(type)) {
            throw badRequest("未知的设备类型: " + request.deviceType());
        }
        String assetNo = request.assetNo().trim();
        if (devices.findByAssetNo(assetNo).isPresent()) {
            throw badRequest("资产编号 " + assetNo + " 已存在");
        }
        ItDevice device = new ItDevice(assetNo, type, request.model().trim(), request.managingTeamId(),
                parseDate(request.purchasedOn(), "购入日期"), parseDate(request.warrantyUntil(), "保修截止日期"), request.note());
        devices.save(device);
        events.save(new ItDeviceEvent(device.getId(), "CREATED", actorId, null, null, "入库"));
        audit.record(actorId, "it.device_created", "it_device", device.getId(), assetNo, traceId);
        return toDto(device, true);
    }

    @Transactional(readOnly = true)
    public List<DeviceDto> list(Collection<Long> scope, String status, String type, int limit) {
        DeviceStatus parsed = null;
        if (status != null && !status.isBlank()) {
            try {
                parsed = DeviceStatus.valueOf(status.trim().toUpperCase());
            } catch (IllegalArgumentException e) {
                throw badRequest("未知的设备状态: " + status);
            }
        }
        DeviceStatus finalStatus = parsed;
        String finalType = type == null || type.isBlank() ? null : type.trim().toUpperCase();
        Specification<ItDevice> spec = (root, query, cb) -> {
            List<Predicate> predicates = new ArrayList<>();
            predicates.add(root.get("managingTeamId").in(scope));
            if (finalStatus != null) {
                predicates.add(cb.equal(root.get("status"), finalStatus));
            }
            if (finalType != null) {
                predicates.add(cb.equal(root.get("deviceType"), finalType));
            }
            return cb.and(predicates.toArray(new Predicate[0]));
        };
        return devices.findAll(spec, PageRequest.of(0, Math.max(1, Math.min(limit, 200)), Sort.by(Sort.Direction.DESC, "id")))
                .getContent().stream().map(d -> toDto(d, false)).toList();
    }

    @Transactional(readOnly = true)
    public DeviceDto get(long id, Collection<Long> scope) {
        return toDto(devices.findById(id).filter(d -> scope.contains(d.getManagingTeamId()))
                .orElseThrow(() -> notFound("设备不存在")), true);
    }

    /** 员工查看自己名下的设备（不带事件历史）。 */
    @Transactional(readOnly = true)
    public List<DeviceDto> mine(long userId) {
        return devices.findByAssigneeUserIdOrderById(userId).stream().map(d -> toDto(d, false)).toList();
    }

    @Transactional
    public DeviceDto assign(long id, AssignDevice request, long actorId, Collection<Long> scope, String traceId) {
        ItDevice device = lock(id, scope);
        if (device.getStatus() != DeviceStatus.IN_STOCK) {
            throw badRequest("只有在库的设备能领用，当前状态: " + device.getStatus());
        }
        if (request.ticketId() != null) {
            ItTicket ticket = tickets.findById(request.ticketId()).filter(t -> scope.contains(t.getTeamId()))
                    .orElseThrow(() -> notFound("关联的工单不存在"));
            if (ticket.getRequesterUserId() != request.userId()) {
                throw badRequest("关联工单的申请人和领用人不是同一个人");
            }
            comments.save(new ItTicketComment(ticket.getId(), actorId, true, "SYSTEM",
                    "已发放设备 " + device.getAssetNo() + "（" + device.getModel() + "）"));
        }
        device.move(DeviceStatus.ASSIGNED, request.userId());
        events.save(new ItDeviceEvent(id, "ASSIGNED", actorId, request.userId(), request.ticketId(), request.note()));
        audit.record(actorId, "it.device_assigned", "it_device", id, "{\"user\":" + request.userId() + "}", traceId);
        return toDto(device, true);
    }

    @Transactional
    public DeviceDto giveBack(long id, String note, long actorId, Collection<Long> scope, String traceId) {
        ItDevice device = lock(id, scope);
        if (device.getStatus() != DeviceStatus.ASSIGNED) {
            throw badRequest("只有已领用的设备能归还，当前状态: " + device.getStatus());
        }
        Long holder = device.getAssigneeUserId();
        device.move(DeviceStatus.IN_STOCK, null);
        events.save(new ItDeviceEvent(id, "RETURNED", actorId, holder, null, note));
        audit.record(actorId, "it.device_returned", "it_device", id, null, traceId);
        return toDto(device, true);
    }

    @Transactional
    public DeviceDto sendToRepair(long id, String note, long actorId, Collection<Long> scope, String traceId) {
        ItDevice device = lock(id, scope);
        if (device.getStatus() != DeviceStatus.IN_STOCK && device.getStatus() != DeviceStatus.ASSIGNED) {
            throw badRequest("只有在库或已领用的设备能送修，当前状态: " + device.getStatus());
        }
        device.move(DeviceStatus.REPAIR, device.getAssigneeUserId());   // 持有人保留：修好后回到他手上
        events.save(new ItDeviceEvent(id, "REPAIR", actorId, device.getAssigneeUserId(), null, note));
        audit.record(actorId, "it.device_repair", "it_device", id, null, traceId);
        return toDto(device, true);
    }

    @Transactional
    public DeviceDto repairDone(long id, String note, long actorId, Collection<Long> scope, String traceId) {
        ItDevice device = lock(id, scope);
        if (device.getStatus() != DeviceStatus.REPAIR) {
            throw badRequest("只有维修中的设备能标记修好，当前状态: " + device.getStatus());
        }
        Long holder = device.getAssigneeUserId();
        device.move(holder != null ? DeviceStatus.ASSIGNED : DeviceStatus.IN_STOCK, holder);
        events.save(new ItDeviceEvent(id, "REPAIRED", actorId, holder, null, note));
        audit.record(actorId, "it.device_repaired", "it_device", id, null, traceId);
        return toDto(device, true);
    }

    @Transactional
    public DeviceDto retire(long id, String note, long actorId, Collection<Long> scope, String traceId) {
        ItDevice device = lock(id, scope);
        if (device.getStatus() == DeviceStatus.RETIRED) {
            throw badRequest("设备已经报废");
        }
        if (device.getAssigneeUserId() != null) {
            throw badRequest("设备还在员工名下，请先收回再报废");
        }
        device.move(DeviceStatus.RETIRED, null);
        events.save(new ItDeviceEvent(id, "RETIRED", actorId, null, null, note));
        audit.record(actorId, "it.device_retired", "it_device", id, null, traceId);
        return toDto(device, true);
    }

    private ItDevice lock(long id, Collection<Long> scope) {
        return devices.findForUpdate(id).filter(d -> scope.contains(d.getManagingTeamId()))
                .orElseThrow(() -> notFound("设备不存在"));
    }

    private LocalDate parseDate(String raw, String label) {
        if (raw == null || raw.isBlank()) {
            return null;
        }
        try {
            return LocalDate.parse(raw.trim());
        } catch (DateTimeParseException e) {
            throw badRequest(label + "格式应为 YYYY-MM-DD");
        }
    }

    private String warrantyStatus(ItDevice d) {
        if (d.getWarrantyUntil() == null) {
            return "UNKNOWN";
        }
        LocalDate today = LocalDate.now(BEIJING);
        if (d.getWarrantyUntil().isBefore(today)) {
            return "EXPIRED";
        }
        return d.getWarrantyUntil().isBefore(today.plusDays(30)) ? "EXPIRING" : "OK";
    }

    private DeviceDto toDto(ItDevice d, boolean withEvents) {
        List<DeviceEventDto> history = withEvents ? events.findByDeviceIdOrderByIdAsc(d.getId()).stream()
                .map(e -> new DeviceEventDto(e.getEventType(), e.getActorUserId(), e.getSubjectUserId(), e.getTicketId(),
                        e.getNote(), e.getCreatedAt())).toList() : List.of();
        return new DeviceDto(d.getId(), d.getAssetNo(), d.getDeviceType(), d.getModel(), d.getStatus().name(),
                d.getAssigneeUserId(), d.getManagingTeamId(),
                d.getPurchasedOn() == null ? null : d.getPurchasedOn().toString(),
                d.getWarrantyUntil() == null ? null : d.getWarrantyUntil().toString(), warrantyStatus(d), d.getNote(), history);
    }
}
