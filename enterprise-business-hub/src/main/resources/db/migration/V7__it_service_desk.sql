-- IT 服务台：工单（故障/账号/权限/设备申请/咨询）、SLA、设备台账与生命周期、自助解决方案库。

CREATE TABLE it_ticket (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    requester_user_id BIGINT NOT NULL,
    team_id BIGINT NOT NULL,                       -- 申请人所在部门
    category VARCHAR(20) NOT NULL,                 -- INCIDENT / ACCOUNT / PERMISSION / DEVICE / OTHER
    priority VARCHAR(10) NOT NULL,                 -- LOW / NORMAL / HIGH / URGENT
    title VARCHAR(120) NOT NULL,
    description TEXT NOT NULL,
    status VARCHAR(20) NOT NULL,                   -- PENDING_APPROVAL / OPEN / IN_PROGRESS / WAITING_USER / RESOLVED / CLOSED / CANCELLED / REJECTED
    assignee_user_id BIGINT NULL,
    approver_user_id BIGINT NULL,
    decision_note VARCHAR(500) NULL,
    suggested_category VARCHAR(20) NULL,           -- 规则对分类/优先级的建议与依据（留痕，便于对比人工调整）
    suggested_priority VARCHAR(10) NULL,
    classify_reason VARCHAR(300) NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    first_response_at DATETIME NULL,
    sla_due_at DATETIME NOT NULL,
    resolved_at DATETIME NULL,
    resolution VARCHAR(1000) NULL,
    closed_at DATETIME NULL,
    reopen_count INT NOT NULL DEFAULT 0,
    waiting_since DATETIME NULL,                   -- 进入“等待用户”的时刻（该期间不计入处理时限）
    KEY idx_it_ticket_requester (requester_user_id),
    KEY idx_it_ticket_team_status (team_id, status),
    KEY idx_it_ticket_status_sla (status, sla_due_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE it_ticket_comment (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    ticket_id BIGINT NOT NULL,
    author_user_id BIGINT NOT NULL,
    internal TINYINT(1) NOT NULL DEFAULT 0,        -- 内部备注：申请人看不到
    kind VARCHAR(20) NOT NULL,                     -- COMMENT / STATUS / SYSTEM
    body VARCHAR(2000) NOT NULL,
    created_at DATETIME NOT NULL,
    KEY idx_it_comment_ticket (ticket_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE it_device (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    asset_no VARCHAR(40) NOT NULL,
    device_type VARCHAR(30) NOT NULL,              -- LAPTOP / DESKTOP / MONITOR / PHONE / PERIPHERAL / OTHER
    model VARCHAR(100) NOT NULL,
    status VARCHAR(20) NOT NULL,                   -- IN_STOCK / ASSIGNED / REPAIR / RETIRED
    assignee_user_id BIGINT NULL,
    managing_team_id BIGINT NOT NULL,              -- 管理该设备的 IT 部门
    purchased_on DATE NULL,
    warranty_until DATE NULL,
    note VARCHAR(300) NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    UNIQUE KEY uq_it_device_asset (asset_no),
    KEY idx_it_device_assignee (assignee_user_id),
    KEY idx_it_device_team_status (managing_team_id, status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE it_device_event (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    device_id BIGINT NOT NULL,
    event_type VARCHAR(20) NOT NULL,               -- CREATED / ASSIGNED / RETURNED / REPAIR / REPAIRED / RETIRED
    actor_user_id BIGINT NOT NULL,
    subject_user_id BIGINT NULL,
    ticket_id BIGINT NULL,
    note VARCHAR(300) NULL,
    created_at DATETIME NOT NULL,
    KEY idx_it_device_event_device (device_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 自助解决方案：按关键词匹配，提交工单前先给用户看，能自己解决的不必等人
CREATE TABLE it_kb_article (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    category VARCHAR(20) NOT NULL,
    title VARCHAR(120) NOT NULL,
    keywords VARCHAR(300) NOT NULL,                -- 逗号分隔
    steps TEXT NOT NULL,
    enabled TINYINT(1) NOT NULL DEFAULT 1
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT INTO it_kb_article (category, title, keywords, steps) VALUES
    ('INCIDENT', '忘记密码或账号被锁定', '密码,忘记,锁定,登录不了,无法登录,登不上',
     '1. 在登录页点击“忘记密码”，用绑定手机号验证后重置。\n2. 连续输错 5 次会锁定 15 分钟，请等待后再试。\n3. 仍无法登录再提交故障工单，并写明账号名和报错提示。'),
    ('INCIDENT', 'VPN 连接失败', 'vpn,连不上,远程办公,无法连接,拨号',
     '1. 确认本机网络正常（能打开公司外的网页）。\n2. 退出 VPN 客户端后重新登录，确认账号密码和动态口令正确。\n3. 重启客户端；仍失败请提交故障工单，附上客户端提示的错误代码。'),
    ('INCIDENT', '打印机无法打印', '打印机,打印,卡纸,脱机,无法打印',
     '1. 检查打印机是否开机、有纸、显示屏有无报错（卡纸/缺墨）。\n2. 电脑里把打印机设为“默认打印机”，取消“脱机使用打印机”。\n3. 重启打印机和打印任务；仍失败提交故障工单并写明打印机位置和型号。'),
    ('INCIDENT', '电脑很慢或卡顿', '很慢,卡顿,卡死,太慢,运行慢,死机',
     '1. 保存工作后重启电脑（超过一周没重启最常见）。\n2. 打开任务管理器，关闭占用 CPU/内存很高的程序。\n3. 磁盘剩余空间低于 10% 时先清理；仍然很慢请提交故障工单。'),
    ('INCIDENT', '邮箱无法收发邮件', '邮箱,邮件,收不到,发不出,outlook',
     '1. 检查邮箱容量是否已满（满了需先清理）。\n2. 在网页版邮箱试一下：网页能用说明是客户端设置问题，重新添加账号即可。\n3. 网页也不行请提交故障工单，附上退信提示。'),
    ('INCIDENT', 'Wi-Fi 或网络不通', 'wifi,wi-fi,无线,网络,断网,上不了网',
     '1. 关闭再打开 Wi-Fi，或拔插网线；换一个位置试试。\n2. 确认连接的是公司网络而不是访客网络。\n3. 多人同时断网说明是网络故障，请直接提交“紧急”故障工单并写明楼层。');
