-- 后台增量迁移，适用基线 3b9e7c2a4106；与 alembic upgrade head 二选一。
-- Running upgrade 3b9e7c2a4106 -> a61d8c4e9201

CREATE TABLE admin_users (
    id VARCHAR(36) NOT NULL COMMENT '记录唯一标识（UUID）',
    created_at DATETIME(6) NOT NULL COMMENT '创建时间（UTC）',
    updated_at DATETIME(6) NOT NULL COMMENT '更新时间（UTC）',
    login_name VARCHAR(80) NOT NULL COMMENT '管理员登录名',
    display_name VARCHAR(80) NOT NULL COMMENT '管理员显示姓名',
    password_hash VARCHAR(256) NOT NULL COMMENT 'scrypt 密码散列，包含随机盐；不保存明文',
    `role` VARCHAR(24) NOT NULL COMMENT '角色：SUPER_ADMIN/OPERATIONS/SUPPORT/QUALITY/FINANCE/AUDITOR',
    status VARCHAR(24) NOT NULL COMMENT '状态：ACTIVE=启用，DISABLED=禁用',
    mfa_secret_encrypted LONGTEXT COMMENT '加密的 TOTP 密钥；空表示未启用 MFA',
    last_mfa_step BIGINT COMMENT '最近使用的 TOTP 时间步，防止重放',
    last_login_at DATETIME(6) COMMENT '最近登录时间（UTC）',
    PRIMARY KEY (id),
    UNIQUE (login_name)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='独立后台管理员账号、角色及多因素认证，不复用求职者身份' COLLATE utf8mb4_0900_bin;

CREATE INDEX ix_admin_users_created_at ON admin_users (created_at);

CREATE TABLE payment_channel_configs (
    id VARCHAR(36) NOT NULL COMMENT '记录唯一标识（UUID）',
    created_at DATETIME(6) NOT NULL COMMENT '创建时间（UTC）',
    updated_at DATETIME(6) NOT NULL COMMENT '更新时间（UTC）',
    channel VARCHAR(16) NOT NULL COMMENT '渠道：WECHAT/ALIPAY',
    environment VARCHAR(16) NOT NULL COMMENT '环境：sandbox/production',
    config_encrypted LONGTEXT NOT NULL COMMENT '加密商户标识、HTTPS 回调与密钥引用；无明文私钥',
    enabled BOOL NOT NULL COMMENT '配置开关，不代表真实支付已接通',
    version INTEGER NOT NULL COMMENT '配置版本号',
    PRIMARY KEY (id),
    CONSTRAINT uq_payment_channel_environment UNIQUE (channel, environment)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='支付配置元数据；只保存密钥托管引用，真实适配器另行接入' COLLATE utf8mb4_0900_bin;

CREATE INDEX ix_payment_channel_configs_created_at ON payment_channel_configs (created_at);

CREATE TABLE admin_approval_requests (
    id VARCHAR(36) NOT NULL COMMENT '记录唯一标识（UUID）',
    created_at DATETIME(6) NOT NULL COMMENT '创建时间（UTC）',
    updated_at DATETIME(6) NOT NULL COMMENT '更新时间（UTC）',
    applicant_id VARCHAR(36) NOT NULL COMMENT '申请管理员 ID',
    reviewer_id VARCHAR(36) COMMENT '审核管理员 ID；不得等于申请人',
    kind VARCHAR(40) NOT NULL COMMENT '类型：PRODUCT_PUBLISH/PAYMENT_CONFIG/ENTITLEMENT_GRANT/REFUND',
    resource_id VARCHAR(120) NOT NULL COMMENT '目标资源 ID',
    payload_encrypted LONGTEXT NOT NULL COMMENT '加密申请内容快照',
    reason VARCHAR(500) NOT NULL COMMENT '申请理由及关联工单',
    status VARCHAR(24) NOT NULL COMMENT '状态：PENDING/APPROVED/REJECTED',
    review_note VARCHAR(500) COMMENT '审核意见',
    PRIMARY KEY (id),
    FOREIGN KEY(reviewer_id) REFERENCES admin_users (id),
    FOREIGN KEY(applicant_id) REFERENCES admin_users (id)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='关键操作双人审批，保存不可变更快照' COLLATE utf8mb4_0900_bin;

CREATE INDEX ix_admin_approval_requests_created_at ON admin_approval_requests (created_at);

CREATE TABLE admin_audit_logs (
    id VARCHAR(36) NOT NULL COMMENT '记录唯一标识（UUID）',
    created_at DATETIME(6) NOT NULL COMMENT '创建时间（UTC）',
    updated_at DATETIME(6) NOT NULL COMMENT '更新时间（UTC）',
    admin_id VARCHAR(36) COMMENT '操作管理员 ID；登录失败时可空',
    action VARCHAR(80) NOT NULL COMMENT '操作类型',
    resource_id VARCHAR(120) NOT NULL COMMENT '资源 ID，不记录媒体 URL 和业务正文',
    reason VARCHAR(500) NOT NULL COMMENT '业务操作原因；不应填入敏感数据',
    result VARCHAR(24) NOT NULL COMMENT '结果：SUCCESS/DENIED',
    ip VARCHAR(64) NOT NULL COMMENT '请求来源 IP',
    request_id VARCHAR(80) NOT NULL COMMENT '请求追踪 ID',
    PRIMARY KEY (id),
    FOREIGN KEY(admin_id) REFERENCES admin_users (id)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='后台追加式操作审计，业务 API 不提供更新或删除' COLLATE utf8mb4_0900_bin;

CREATE INDEX ix_admin_audit_logs_created_at ON admin_audit_logs (created_at);

CREATE TABLE admin_idempotency_records (
    id VARCHAR(36) NOT NULL COMMENT '记录唯一标识（UUID）',
    created_at DATETIME(6) NOT NULL COMMENT '创建时间（UTC）',
    updated_at DATETIME(6) NOT NULL COMMENT '更新时间（UTC）',
    admin_id VARCHAR(36) NOT NULL COMMENT '管理员 ID，关联 admin_users.id',
    scope VARCHAR(120) NOT NULL COMMENT '操作作用域',
    `key` VARCHAR(120) NOT NULL COMMENT '客户端幂等键',
    request_hash VARCHAR(64) NOT NULL COMMENT '请求内容 SHA256 摘要',
    response_encrypted LONGTEXT NOT NULL COMMENT '加密响应快照；不保存播放 URL',
    PRIMARY KEY (id),
    CONSTRAINT uq_admin_idempotency UNIQUE (admin_id, scope, `key`),
    FOREIGN KEY(admin_id) REFERENCES admin_users (id)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='管理员写操作幂等请求与加密响应' COLLATE utf8mb4_0900_bin;

CREATE INDEX ix_admin_idempotency_records_created_at ON admin_idempotency_records (created_at);

CREATE TABLE admin_sessions (
    id VARCHAR(36) NOT NULL COMMENT '记录唯一标识（UUID）',
    created_at DATETIME(6) NOT NULL COMMENT '创建时间（UTC）',
    updated_at DATETIME(6) NOT NULL COMMENT '更新时间（UTC）',
    admin_id VARCHAR(36) NOT NULL COMMENT '管理员 ID，关联 admin_users.id',
    token_id VARCHAR(36) NOT NULL COMMENT 'JWT jti 唯一标识',
    expires_at DATETIME(6) NOT NULL COMMENT '会话到期时间（UTC）',
    revoked_at DATETIME(6) COMMENT '撤销时间（UTC）',
    PRIMARY KEY (id),
    FOREIGN KEY(admin_id) REFERENCES admin_users (id),
    UNIQUE (token_id)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='后台可撤销会话；JWT audience 与求职端隔离' COLLATE utf8mb4_0900_bin;

CREATE INDEX ix_admin_sessions_created_at ON admin_sessions (created_at);

CREATE TABLE product_skus (
    id VARCHAR(36) NOT NULL COMMENT '记录唯一标识（UUID）',
    created_at DATETIME(6) NOT NULL COMMENT '创建时间（UTC）',
    updated_at DATETIME(6) NOT NULL COMMENT '更新时间（UTC）',
    code VARCHAR(40) NOT NULL COMMENT '商品 SKU 编码',
    version INTEGER NOT NULL COMMENT '配置版本号，从 1 递增',
    name VARCHAR(100) NOT NULL COMMENT '商品显示名',
    kind VARCHAR(24) NOT NULL COMMENT '类型：SINGLE/MONTH/QUARTER/YEAR/PACK',
    amount_minor INTEGER NOT NULL COMMENT '售价（人民币分）',
    original_minor INTEGER NOT NULL COMMENT '原价（人民币分）',
    credits INTEGER NOT NULL COMMENT '一次性发放的面试次数，不按月重置',
    validity_days INTEGER NOT NULL COMMENT '计划有效天数，首版仅配置，不执行到期回收',
    currency VARCHAR(3) NOT NULL COMMENT '币种：CNY',
    description VARCHAR(500) NOT NULL COMMENT '商品描述',
    status VARCHAR(24) NOT NULL COMMENT '状态：DRAFT/IN_REVIEW/PUBLISHED/RETIRED',
    created_by VARCHAR(36) NOT NULL COMMENT '创建管理员 ID',
    published_at DATETIME(6) COMMENT '发布时间（UTC）',
    PRIMARY KEY (id),
    CONSTRAINT uq_product_sku_version UNIQUE (code, version),
    FOREIGN KEY(created_by) REFERENCES admin_users (id)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='按 SKU 和版本保存商品配置，已发布版本只影响新订单' COLLATE utf8mb4_0900_bin;

CREATE INDEX ix_product_skus_created_at ON product_skus (created_at);

CREATE TABLE quality_review_tasks (
    id VARCHAR(36) NOT NULL COMMENT '记录唯一标识（UUID）',
    created_at DATETIME(6) NOT NULL COMMENT '创建时间（UTC）',
    updated_at DATETIME(6) NOT NULL COMMENT '更新时间（UTC）',
    interview_id VARCHAR(36) NOT NULL COMMENT '关联面试 ID',
    assignee_id VARCHAR(36) NOT NULL COMMENT '受派管理员 ID',
    created_by VARCHAR(36) NOT NULL COMMENT '创建管理员 ID',
    reason VARCHAR(500) NOT NULL COMMENT '抽检原因及关联工单',
    status VARCHAR(24) NOT NULL COMMENT '状态：PENDING/IN_REVIEW/COMPLETED/NEEDS_FIX',
    tags JSON NOT NULL COMMENT '问题标签数组',
    conclusion VARCHAR(2000) COMMENT '处理结论',
    PRIMARY KEY (id),
    FOREIGN KEY(created_by) REFERENCES admin_users (id),
    FOREIGN KEY(assignee_id) REFERENCES admin_users (id),
    FOREIGN KEY(interview_id) REFERENCES interviews (id)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='授权质检样本与问题处理任务，限定审阅人资源范围' COLLATE utf8mb4_0900_bin;

CREATE INDEX ix_quality_review_tasks_created_at ON quality_review_tasks (created_at);

CREATE TABLE reconciliation_batches (
    id VARCHAR(36) NOT NULL COMMENT '记录唯一标识（UUID）',
    created_at DATETIME(6) NOT NULL COMMENT '创建时间（UTC）',
    updated_at DATETIME(6) NOT NULL COMMENT '更新时间（UTC）',
    channel VARCHAR(16) NOT NULL COMMENT '渠道：WECHAT/ALIPAY/DEV',
    bill_date VARCHAR(10) NOT NULL COMMENT '账单日期 YYYY-MM-DD（运营时区）',
    status VARCHAR(24) NOT NULL COMMENT '状态：MATCHED/DIFFERENCES',
    total_rows INTEGER NOT NULL COMMENT '导入账单行数',
    difference_count INTEGER NOT NULL COMMENT '未匹配条目数',
    created_by VARCHAR(36) NOT NULL COMMENT '导入管理员 ID',
    PRIMARY KEY (id),
    FOREIGN KEY(created_by) REFERENCES admin_users (id)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='导入渠道账单后与业务订单核对，不伪造渠道拉单' COLLATE utf8mb4_0900_bin;

CREATE INDEX ix_reconciliation_batches_created_at ON reconciliation_batches (created_at);

CREATE TABLE order_product_snapshots (
    id VARCHAR(36) NOT NULL COMMENT '记录唯一标识（UUID）',
    created_at DATETIME(6) NOT NULL COMMENT '创建时间（UTC）',
    updated_at DATETIME(6) NOT NULL COMMENT '更新时间（UTC）',
    order_id VARCHAR(36) NOT NULL COMMENT '订单 ID，关联 orders.id',
    product_id VARCHAR(36) COMMENT '商品版本 ID；历史常量商品可空',
    snapshot_json JSON NOT NULL COMMENT 'SKU、版本、金额、币种和次数快照',
    PRIMARY KEY (id),
    CONSTRAINT uq_order_product_snapshot UNIQUE (order_id),
    FOREIGN KEY(product_id) REFERENCES product_skus (id),
    FOREIGN KEY(order_id) REFERENCES orders (id)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='订单创建时固化的商品价格和权益快照，发布新价不改历史订单' COLLATE utf8mb4_0900_bin;

CREATE INDEX ix_order_product_snapshots_created_at ON order_product_snapshots (created_at);

CREATE TABLE reconciliation_items (
    id VARCHAR(36) NOT NULL COMMENT '记录唯一标识（UUID）',
    created_at DATETIME(6) NOT NULL COMMENT '创建时间（UTC）',
    updated_at DATETIME(6) NOT NULL COMMENT '更新时间（UTC）',
    batch_id VARCHAR(36) NOT NULL COMMENT '关联对账批次 ID',
    order_id VARCHAR(36) NOT NULL COMMENT '账单提供的订单 ID；可为不存在的订单',
    difference_type VARCHAR(40) NOT NULL COMMENT '差异：MISSING_PLATFORM/MISSING_CHANNEL/AMOUNT/STATUS/DUPLICATE',
    amount_minor INTEGER NOT NULL COMMENT '渠道账单金额（人民币分）',
    status VARCHAR(24) NOT NULL COMMENT '工单状态：OPEN/RESOLVED',
    resolution VARCHAR(500) COMMENT '处理结论',
    PRIMARY KEY (id),
    FOREIGN KEY(batch_id) REFERENCES reconciliation_batches (id)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='对账差异工单；不直接修改订单金额' COLLATE utf8mb4_0900_bin;

CREATE INDEX ix_reconciliation_items_created_at ON reconciliation_items (created_at);

CREATE TABLE refund_requests (
    id VARCHAR(36) NOT NULL COMMENT '记录唯一标识（UUID）',
    created_at DATETIME(6) NOT NULL COMMENT '创建时间（UTC）',
    updated_at DATETIME(6) NOT NULL COMMENT '更新时间（UTC）',
    order_id VARCHAR(36) NOT NULL COMMENT '目标订单 ID',
    applicant_id VARCHAR(36) NOT NULL COMMENT '申请管理员 ID',
    approval_id VARCHAR(36) COMMENT '关联审批 ID',
    amount_minor INTEGER NOT NULL COMMENT '退款金额（人民币分）',
    reason VARCHAR(500) NOT NULL COMMENT '退款原因',
    status VARCHAR(24) NOT NULL COMMENT '状态：PENDING/APPROVED/REJECTED/SUCCEEDED',
    provider_refund_no VARCHAR(120) COMMENT '渠道退款流水；dev 前缀仅为开发模拟',
    PRIMARY KEY (id),
    FOREIGN KEY(approval_id) REFERENCES admin_approval_requests (id),
    FOREIGN KEY(applicant_id) REFERENCES admin_users (id),
    FOREIGN KEY(order_id) REFERENCES orders (id)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='全额退款申请及双人审批；到账结果必须由渠道确认' COLLATE utf8mb4_0900_bin;

CREATE INDEX ix_refund_requests_created_at ON refund_requests (created_at);

ALTER TABLE `users` MODIFY COLUMN status VARCHAR(24) NOT NULL COMMENT '账号状态：ACTIVE=正常，FROZEN=冻结，DELETING=删除中，DELETED=已删除';

ALTER TABLE `user_consents` MODIFY COLUMN consent_type VARCHAR(30) NOT NULL COMMENT '授权类型：PRIVACY/TERMS/AI_PROCESSING/MICROPHONE/CAMERA/SCREEN_CAPTURE；SUPPORT_REVIEW=后台人工审阅授权';

UPDATE alembic_version SET version_num='a61d8c4e9201' WHERE alembic_version.version_num = '3b9e7c2a4106';

