package com.enterprisehub.procurement;

import com.enterprisehub.audit.AuditService;
import com.enterprisehub.procurement.dto.BudgetDto;
import com.enterprisehub.procurement.dto.CreatePurchaseDraftRequest;
import com.enterprisehub.procurement.dto.ProductDto;
import com.enterprisehub.procurement.dto.PurchaseRequestDto;
import com.enterprisehub.sap.SapConnector;
import com.enterprisehub.security.TeamAccessGuard;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;

import java.math.BigDecimal;
import java.time.Year;
import java.util.ArrayList;
import java.util.List;
import java.util.Objects;

/**
 * 库存与采购闭环（docs/enterprise-business-hub-plan.md 第4节）：查库存 → 判断是否
 * 低于安全库存（`ProductDto.belowSafetyStock`，由调用方/Agent 自己判断要不要建草稿，
 * 这里不强制"低于才能建"——用户可能提前备货）→ 查部门预算 → 建草稿 → 提交（校验预算）
 * → 部门负责人批准（批准才扣预算 + 生成采购单，查 SAP 拿供应商名称）/拒绝。
 */
@Service
public class ProcurementService {
    private final ProductRepository productRepository;
    private final DepartmentBudgetRepository budgetRepository;
    private final PurchaseRequestRepository requestRepository;
    private final PurchaseRequestLineRepository lineRepository;
    private final PurchaseOrderRepository orderRepository;
    private final SapConnector sapConnector;
    private final AuditService auditService;

    public ProcurementService(ProductRepository productRepository, DepartmentBudgetRepository budgetRepository,
                               PurchaseRequestRepository requestRepository, PurchaseRequestLineRepository lineRepository,
                               PurchaseOrderRepository orderRepository, SapConnector sapConnector,
                               AuditService auditService) {
        this.productRepository = productRepository;
        this.budgetRepository = budgetRepository;
        this.requestRepository = requestRepository;
        this.lineRepository = lineRepository;
        this.orderRepository = orderRepository;
        this.sapConnector = sapConnector;
        this.auditService = auditService;
    }

    public ProductDto getProduct(String sku) {
        Product product = productRepository.findBySku(sku).orElseThrow(() -> notFound("产品不存在: " + sku));
        return ProductDto.from(product);
    }

    public BudgetDto getBudget(long teamId, int year) {
        DepartmentBudget budget = budgetRepository.findByTeamIdAndYear(teamId, year)
                .orElseThrow(() -> badRequest("没有 " + year + " 年度的部门预算记录"));
        return BudgetDto.from(budget);
    }

    @Transactional
    public PurchaseRequestDto createDraft(long requesterUserId, long teamId, CreatePurchaseDraftRequest body,
                                           String traceId) {
        List<Product> products = new ArrayList<>();
        BigDecimal totalAmount = BigDecimal.ZERO;
        for (CreatePurchaseDraftRequest.LineItem item : body.lines()) {
            Product product = productRepository.findBySku(item.sku())
                    .orElseThrow(() -> badRequest("未知产品: " + item.sku()));
            products.add(product);
            totalAmount = totalAmount.add(product.getUnitPrice().multiply(BigDecimal.valueOf(item.quantity())));
        }

        PurchaseRequest request = new PurchaseRequest(requesterUserId, teamId, totalAmount);
        requestRepository.save(request);
        for (int i = 0; i < body.lines().size(); i++) {
            CreatePurchaseDraftRequest.LineItem item = body.lines().get(i);
            Product product = products.get(i);
            lineRepository.save(new PurchaseRequestLine(request.getId(), product.getId(), item.quantity(),
                    product.getUnitPrice()));
        }

        auditService.record(requesterUserId, "procurement.draft_created", "purchase_request", request.getId(),
                "{\"lineCount\":" + body.lines().size() + ",\"totalAmount\":" + totalAmount + "}", traceId);
        return toDto(request);
    }

    @Transactional
    public PurchaseRequestDto submit(long requestId, long requesterUserId, String traceId) {
        PurchaseRequest request = getOwnedDraft(requestId, requesterUserId);
        DepartmentBudget budget = budgetRepository.findByTeamIdAndYear(request.getTeamId(), Year.now().getValue())
                .orElseThrow(() -> badRequest("没有本年度的部门预算记录"));
        if (budget.getRemainingAmount().compareTo(request.getTotalAmount()) < 0) {
            throw badRequest("预算不足：还剩 " + budget.getRemainingAmount() + "，申请了 " + request.getTotalAmount());
        }
        request.submit();
        auditService.record(requesterUserId, "procurement.submitted", "purchase_request", request.getId(),
                null, traceId);
        return toDto(request);
    }

    @Transactional
    public PurchaseRequestDto approve(long requestId, long approverUserId, String note, String traceId,
                                       Long approverTeamId, boolean isOrgAdmin, boolean isTeamAdmin) {
        PurchaseRequest request = getSubmitted(requestId);
        TeamAccessGuard.requireTeamAccess(approverTeamId, isOrgAdmin, isTeamAdmin, request.getTeamId());
        if (request.getRequesterUserId() == approverUserId) {
            throw badRequest("不能审批自己提交的申请，需要由部门负责人或企业管理员处理");
        }
        DepartmentBudget budget = budgetRepository.findForUpdate(request.getTeamId(), Year.now().getValue())     // 预算行锁：同部门两张采购同时批准，不能都读到同一份余额
                .orElseThrow(() -> badRequest("预算记录不存在，无法批准"));
        if (budget.getRemainingAmount().compareTo(request.getTotalAmount()) < 0) {
            throw badRequest("批准时预算不足（可能已被其它已批准的申请占用）");
        }
        budget.deduct(request.getTotalAmount());
        request.approve(approverUserId, note);
        createPurchaseOrder(request);
        auditService.record(approverUserId, "procurement.approved", "purchase_request", request.getId(),
                note, traceId);
        return toDto(request);
    }

    @Transactional
    public PurchaseRequestDto reject(long requestId, long approverUserId, String note, String traceId,
                                      Long approverTeamId, boolean isOrgAdmin, boolean isTeamAdmin) {
        PurchaseRequest request = getSubmitted(requestId);
        TeamAccessGuard.requireTeamAccess(approverTeamId, isOrgAdmin, isTeamAdmin, request.getTeamId());
        if (request.getRequesterUserId() == approverUserId) {
            throw badRequest("不能处理自己提交的申请，需要由部门负责人或企业管理员处理");
        }
        request.reject(approverUserId, note);
        auditService.record(approverUserId, "procurement.rejected", "purchase_request", request.getId(),
                note, traceId);
        return toDto(request);
    }

    public PurchaseRequestDto getStatus(long requestId, long requesterUserId, Long requesterTeamId,
                                         boolean isOrgAdmin, boolean isTeamAdmin) {
        PurchaseRequest request = requestRepository.findById(requestId)
                .orElseThrow(() -> notFound("采购申请不存在"));
        TeamAccessGuard.requireOwnerOrTeamAccess(request.getRequesterUserId(), requesterUserId, requesterTeamId,
                isOrgAdmin, isTeamAdmin, request.getTeamId());
        return toDto(request);
    }

    /** "我的采购申请"列表——天然按 requesterUserId 过滤，不存在跨用户泄露的可能，
     * 不需要额外的归属校验，跟 LeaveService.listMine 是同一个道理。 */
    public List<PurchaseRequestDto> listMine(long requesterUserId) {
        return requestRepository.findByRequesterUserIdOrderByCreatedAtDesc(requesterUserId).stream()
                .map(this::toDto).toList();
    }

    /** "部门待审批"列表——跟 approve/reject 用同一个 TeamAccessGuard 检查，只有这个
     * 部门的负责人或企业管理员能看，权限判断口径跟审批时完全一致。 */
    public List<PurchaseRequestDto> listTeamPending(long teamId, Long callerTeamId, boolean isOrgAdmin,
                                                     boolean isTeamAdmin) {
        TeamAccessGuard.requireTeamAccess(callerTeamId, isOrgAdmin, isTeamAdmin, teamId);
        return requestRepository.findByTeamIdAndStatusOrderByCreatedAtDesc(teamId, PurchaseStatus.SUBMITTED).stream()
                .map(this::toDto).toList();
    }

    private void createPurchaseOrder(PurchaseRequest request) {
        String supplierCode = lineRepository.findByPurchaseRequestId(request.getId()).stream()
                .map(line -> productRepository.findById(line.getProductId()).orElse(null))
                .filter(Objects::nonNull)
                .map(Product::getSupplierCode)
                .filter(Objects::nonNull)
                .findFirst()
                .orElse(null);
        String supplierName = null;
        if (supplierCode != null) {
            supplierName = sapConnector.getSupplier(supplierCode).name();
        }
        orderRepository.save(new PurchaseOrder(request.getId(), supplierCode, supplierName, "created"));
    }

    private PurchaseRequestDto toDto(PurchaseRequest request) {
        List<PurchaseRequestDto.LineDto> lines = lineRepository.findByPurchaseRequestId(request.getId()).stream()
                .map(line -> {
                    Product product = productRepository.findById(line.getProductId()).orElse(null);
                    String sku = product != null ? product.getSku() : "?";
                    String name = product != null ? product.getName() : "?";
                    return new PurchaseRequestDto.LineDto(sku, name, line.getQuantity(), line.getUnitPrice());
                })
                .toList();

        PurchaseRequestDto.PurchaseOrderDto orderDto = orderRepository.findByPurchaseRequestId(request.getId())
                .map(o -> new PurchaseRequestDto.PurchaseOrderDto(o.getSupplierCode(), o.getSupplierName(), o.getStatus()))
                .orElse(null);

        return new PurchaseRequestDto(
                request.getId(), request.getRequesterUserId(), request.getTeamId(),
                request.getStatus().name(), request.getTotalAmount(), lines,
                request.getApproverUserId(), request.getDecisionNote(),
                request.getCreatedAt(), request.getSubmittedAt(), request.getDecidedAt(), orderDto
        );
    }

    private PurchaseRequest getOwnedDraft(long requestId, long requesterUserId) {
        PurchaseRequest request = requestRepository.findForUpdate(requestId)
                .orElseThrow(() -> notFound("采购申请不存在"));
        if (request.getRequesterUserId() != requesterUserId) {
            throw new ResponseStatusException(HttpStatus.NOT_FOUND, "采购申请不存在");
        }
        if (request.getStatus() != PurchaseStatus.DRAFT) {
            throw badRequest("只有草稿状态的采购申请能提交，当前状态: " + request.getStatus());
        }
        return request;
    }

    private PurchaseRequest getSubmitted(long requestId) {
        PurchaseRequest request = requestRepository.findForUpdate(requestId)
                .orElseThrow(() -> notFound("采购申请不存在"));
        if (request.getStatus() != PurchaseStatus.SUBMITTED) {
            throw badRequest("只有已提交状态的采购申请能审批，当前状态: " + request.getStatus());
        }
        return request;
    }

    private ResponseStatusException badRequest(String message) {
        return new ResponseStatusException(HttpStatus.BAD_REQUEST, message);
    }

    private ResponseStatusException notFound(String message) {
        return new ResponseStatusException(HttpStatus.NOT_FOUND, message);
    }
}
