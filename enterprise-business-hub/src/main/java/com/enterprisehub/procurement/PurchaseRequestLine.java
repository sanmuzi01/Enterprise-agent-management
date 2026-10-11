package com.enterprisehub.procurement;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.Table;

import java.math.BigDecimal;

/** 采购申请明细：下单时把 `Product.unitPrice` 快照到这里——以后产品改价不会
 * 追溯改动历史申请单的金额。用普通的 `purchaseRequestId` 外键列 + 独立
 * repository 管理（不用 JPA 的 `@OneToMany` 级联），跟这个项目里其它单表
 * 都是"平铺 long 外键"的风格一致，也避开 Hibernate 单向 `@OneToMany` 那种
 * "先插入再补外键"在 NOT NULL 列上会失败的坑。 */
@Entity
@Table(name = "purchase_request_line")
public class PurchaseRequestLine {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "purchase_request_id", nullable = false)
    private long purchaseRequestId;

    /** 目录内物品指向 product；目录外物品为空，用 itemName 记名称、unitPrice 记申请人给的预估单价。 */
    @Column(name = "product_id")
    private Long productId;

    @Column(name = "item_name", length = 200)
    private String itemName;

    @Column(name = "quantity", nullable = false)
    private int quantity;

    @Column(name = "unit_price", nullable = false, precision = 12, scale = 2)
    private BigDecimal unitPrice;

    protected PurchaseRequestLine() {
    }

    public PurchaseRequestLine(long purchaseRequestId, long productId, int quantity, BigDecimal unitPrice) {
        this.purchaseRequestId = purchaseRequestId;
        this.productId = productId;
        this.quantity = quantity;
        this.unitPrice = unitPrice;
    }

    /** 目录外物品：没有 product，只有名称和预估单价。 */
    public static PurchaseRequestLine nonCatalog(long purchaseRequestId, String itemName, int quantity,
                                                BigDecimal estimatedUnitPrice) {
        PurchaseRequestLine line = new PurchaseRequestLine();
        line.purchaseRequestId = purchaseRequestId;
        line.itemName = itemName;
        line.quantity = quantity;
        line.unitPrice = estimatedUnitPrice;
        return line;
    }

    public Long getId() {
        return id;
    }

    public long getPurchaseRequestId() {
        return purchaseRequestId;
    }

    public Long getProductId() {
        return productId;
    }

    public String getItemName() {
        return itemName;
    }

    public boolean isNonCatalog() {
        return productId == null;
    }

    public int getQuantity() {
        return quantity;
    }

    public BigDecimal getUnitPrice() {
        return unitPrice;
    }
}
