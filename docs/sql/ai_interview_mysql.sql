-- AI 模拟面试服务：MySQL 8.4 全新建库 SQL（含中文表注释和全部字段注释）
-- 当前结构对应 Alembic 版本 e913c5ab7d20，包含 40 张业务表和 1 张迁移版本表。
-- 仅在空数据库中执行；已有数据库请使用 alembic upgrade head；不要直接执行本建库 SQL。
-- 不包含业务数据或凭证；ID、状态及时间等 ORM 客户端默认值由应用写入。
-- 示例：mysql --default-character-set=utf8mb4 -h 127.0.0.1 -u root AI_Interview < docs/sql/ai_interview_mysql.sql
-- 本文件由 scripts/export_schema_sql.py 生成；MySQL DDL 会隐式提交。

SET NAMES utf8mb4;

CREATE TABLE alembic_version (
    version_num VARCHAR(32) NOT NULL COMMENT '当前已应用的 Alembic 迁移版本号，由迁移工具维护，不是业务版本',
    CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='Alembic 数据库迁移版本标记，由迁移工具维护' COLLATE utf8mb4_0900_ai_ci;

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

CREATE TABLE revoked_tokens (
    jti VARCHAR(36) NOT NULL COMMENT '被撤销 JWT 的唯一标识，用于拦截该令牌',
    expires_at DATETIME(6) NOT NULL COMMENT '原登录令牌的到期时间（UTC），到期后可清理撤销记录',
    id VARCHAR(36) NOT NULL COMMENT '记录唯一标识（UUID，由应用生成）',
    created_at DATETIME(6) NOT NULL COMMENT '记录创建时间（UTC，精确到微秒，由应用写入）',
    updated_at DATETIME(6) NOT NULL COMMENT '记录最近更新时间（UTC，由应用在修改记录时刷新）',
    PRIMARY KEY (id),
    UNIQUE (jti)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='已撤销的登录令牌，用于退出后禁止旧令牌继续访问' COLLATE utf8mb4_0900_bin;

CREATE INDEX ix_revoked_tokens_expires_at ON revoked_tokens (expires_at);

CREATE TABLE users (
    openid_hash VARCHAR(64) NOT NULL COMMENT '登录身份的 HMAC-SHA256 哈希，不保存明文微信 OpenID',
    nickname VARCHAR(80) NOT NULL COMMENT '用户显示昵称',
    status VARCHAR(24) NOT NULL COMMENT '账号状态：ACTIVE=正常，FROZEN=冻结，DELETING=删除中，DELETED=已删除',
    credits INTEGER NOT NULL COMMENT '当前可用面试次数；创建面试时扣减预占，未消费释放时归还',
    id VARCHAR(36) NOT NULL COMMENT '记录唯一标识（UUID，由应用生成）',
    created_at DATETIME(6) NOT NULL COMMENT '记录创建时间（UTC，精确到微秒，由应用写入）',
    updated_at DATETIME(6) NOT NULL COMMENT '记录最近更新时间（UTC，由应用在修改记录时刷新）',
    PRIMARY KEY (id),
    UNIQUE (openid_hash)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='用户账号、昵称、账号状态及剩余面试额度' COLLATE utf8mb4_0900_bin;

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
    FOREIGN KEY(applicant_id) REFERENCES admin_users (id),
    FOREIGN KEY(reviewer_id) REFERENCES admin_users (id)
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

CREATE TABLE audit_logs (
    user_id VARCHAR(36) NOT NULL COMMENT '所属用户 ID，关联 users.id，用于数据归属校验',
    action VARCHAR(60) NOT NULL COMMENT '操作标识，例如授权、创建面试、提交回答、删除数据等审计事件',
    resource_id VARCHAR(120) NOT NULL COMMENT '操作涉及的资源标识；通常为业务记录 ID，也可为授权类型',
    request_id VARCHAR(80) COMMENT '本次操作的 HTTP 请求追踪 ID',
    id VARCHAR(36) NOT NULL COMMENT '记录唯一标识（UUID，由应用生成）',
    created_at DATETIME(6) NOT NULL COMMENT '记录创建时间（UTC，精确到微秒，由应用写入）',
    updated_at DATETIME(6) NOT NULL COMMENT '记录最近更新时间（UTC，由应用在修改记录时刷新）',
    PRIMARY KEY (id),
    FOREIGN KEY(user_id) REFERENCES users (id)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='用户或管理员关键操作的审计记录，不保存敏感业务原文' COLLATE utf8mb4_0900_bin;

CREATE INDEX ix_audit_logs_user_id ON audit_logs (user_id);

CREATE TABLE email_challenges (
    id VARCHAR(36) NOT NULL COMMENT '记录唯一标识（UUID）',
    created_at DATETIME(6) NOT NULL COMMENT '创建时间（UTC）',
    updated_at DATETIME(6) NOT NULL COMMENT '最后变更时间（UTC）',
    user_id VARCHAR(36) COMMENT '绑定邮箱的操作用户 ID；注册或找回密码时为空',
    email_hash VARCHAR(64) NOT NULL COMMENT '验证码目标邮箱 HMAC',
    purpose VARCHAR(24) NOT NULL COMMENT '用途：REGISTER/RESET_PASSWORD/BIND_EMAIL',
    code_hash VARCHAR(64) NOT NULL COMMENT '验证码 HMAC，不保存或记录明文验证码',
    expires_at DATETIME(6) NOT NULL COMMENT '验证码过期时间（UTC）',
    used_at DATETIME(6) COMMENT '成功消费时间（UTC）；空表示未使用',
    PRIMARY KEY (id),
    FOREIGN KEY(user_id) REFERENCES users (id)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='邮箱验证码挑战，限定用途、收件人、有效期和一次性消费' COLLATE utf8mb4_0900_bin;

CREATE TABLE idempotency_records (
    user_id VARCHAR(36) NOT NULL COMMENT '所属用户 ID，关联 users.id，用于数据归属校验',
    scope VARCHAR(200) NOT NULL COMMENT '幂等操作范围，区分创建面试、提交回答、结束、订单等操作',
    `key` VARCHAR(120) NOT NULL COMMENT '客户端 Idempotency-Key；与 user_id、scope 组合唯一，区分大小写',
    request_hash VARCHAR(64) NOT NULL COMMENT '规范化请求内容的 SHA-256 摘要，识别同键不同请求',
    response_encrypted LONGTEXT NOT NULL COMMENT '加密原始响应 JSON；重复请求直接返回该结果',
    id VARCHAR(36) NOT NULL COMMENT '记录唯一标识（UUID，由应用生成）',
    created_at DATETIME(6) NOT NULL COMMENT '记录创建时间（UTC，精确到微秒，由应用写入）',
    updated_at DATETIME(6) NOT NULL COMMENT '记录最近更新时间（UTC，由应用在修改记录时刷新）',
    PRIMARY KEY (id),
    UNIQUE (user_id, scope, `key`),
    FOREIGN KEY(user_id) REFERENCES users (id)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='关键写操作的幂等键、请求摘要及原始响应快照' COLLATE utf8mb4_0900_bin;

CREATE TABLE interview_credit_balances (
    id VARCHAR(36) NOT NULL COMMENT '分类权益余额记录 ID（UUID）',
    user_id VARCHAR(36) NOT NULL COMMENT '权益所属用户 ID，关联 users.id',
    mode VARCHAR(16) NOT NULL COMMENT '面试类型：TEXT=文字、VOICE=语音、VIDEO=视频',
    remaining INTEGER NOT NULL COMMENT '该类型可用次数，单位次；不含已预占次数和历史通用次数' DEFAULT '0',
    created_at DATETIME(6) NOT NULL COMMENT '创建时间（UTC）',
    updated_at DATETIME(6) NOT NULL COMMENT '最后变更时间（UTC）',
    PRIMARY KEY (id),
    CONSTRAINT uq_credit_balance_user_mode UNIQUE (user_id, mode),
    CONSTRAINT ck_credit_balance_nonnegative CHECK (remaining >= 0),
    FOREIGN KEY(user_id) REFERENCES users (id)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='按文字、语音、视频分别管理可用面试次数；旧通用次数继续存储于 users.credits' COLLATE utf8mb4_0900_bin;

CREATE TABLE invitation_codes (
    id VARCHAR(36) NOT NULL COMMENT '记录唯一标识（UUID）',
    created_at DATETIME(6) NOT NULL COMMENT '创建时间（UTC）',
    updated_at DATETIME(6) NOT NULL COMMENT '最后变更时间（UTC）',
    user_id VARCHAR(36) NOT NULL COMMENT '邀请人用户 ID',
    code VARCHAR(24) NOT NULL COMMENT '公开邀请码，大小写规范化',
    PRIMARY KEY (id),
    UNIQUE (user_id),
    FOREIGN KEY(user_id) REFERENCES users (id),
    UNIQUE (code)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='每个用户唯一的邀请码；注册归因后不可重绑邀请人' COLLATE utf8mb4_0900_bin;

CREATE TABLE invitation_configs (
    id VARCHAR(36) NOT NULL COMMENT '记录唯一标识（UUID）',
    created_at DATETIME(6) NOT NULL COMMENT '创建时间（UTC）',
    updated_at DATETIME(6) NOT NULL COMMENT '最后变更时间（UTC）',
    version INTEGER NOT NULL COMMENT '配置版本号，递增且唯一',
    enabled BOOL NOT NULL COMMENT '是否启用邀请奖励',
    threshold INTEGER NOT NULL COMMENT '邀请人每累计多少名有效新注册用户获得一次奖励，单位人',
    inviter_rewards JSON NOT NULL COMMENT '邀请人每达门槛发放的 TEXT/VOICE/VIDEO 次数组合',
    invitee_rewards JSON NOT NULL COMMENT '受邀人成功注册额外发放的 TEXT/VOICE/VIDEO 次数组合',
    created_by VARCHAR(36) NOT NULL COMMENT '发布配置的管理员 ID',
    PRIMARY KEY (id),
    UNIQUE (version),
    FOREIGN KEY(created_by) REFERENCES admin_users (id)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='邀请活动配置版本；新版本仅影响之后验证邮箱并注册的用户' COLLATE utf8mb4_0900_bin;

CREATE TABLE job_profiles (
    user_id VARCHAR(36) NOT NULL COMMENT '所属用户 ID，关联 users.id，用于数据归属校验',
    job_title VARCHAR(120) NOT NULL COMMENT '目标招聘岗位名称，例如 Python 后端工程师',
    seniority VARCHAR(30) NOT NULL COMMENT '岗位级别：JUNIOR=初级，MIDDLE=中级，SENIOR=高级，EXPERT=专家',
    jd_encrypted LONGTEXT NOT NULL COMMENT '加密保存的招聘岗位描述 JD 原文',
    profile_encrypted LONGTEXT COMMENT '加密岗位画像 JSON，包含职责、技能、经验要求及能力维度',
    status VARCHAR(24) NOT NULL COMMENT '岗位解析状态：PREPARING=处理中，READY=就绪，FAILED=失败，DELETED=已删除',
    task_id VARCHAR(36) COMMENT '岗位解析异步任务 ID，对应 outbox_events.id；任务创建前可为空',
    id VARCHAR(36) NOT NULL COMMENT '记录唯一标识（UUID，由应用生成）',
    created_at DATETIME(6) NOT NULL COMMENT '记录创建时间（UTC，精确到微秒，由应用写入）',
    updated_at DATETIME(6) NOT NULL COMMENT '记录最近更新时间（UTC，由应用在修改记录时刷新）',
    PRIMARY KEY (id),
    FOREIGN KEY(user_id) REFERENCES users (id)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='目标岗位 JD、岗位级别及 AI 解析出的结构化岗位画像' COLLATE utf8mb4_0900_bin;

CREATE INDEX ix_job_profiles_user_id ON job_profiles (user_id);

CREATE TABLE orders (
    user_id VARCHAR(36) NOT NULL COMMENT '所属用户 ID，关联 users.id，用于数据归属校验',
    product_code VARCHAR(40) NOT NULL COMMENT '产品编码：SINGLE=单次，PACK_10=10 次套餐，MONTHLY=20 次月度额度包',
    amount_minor INTEGER NOT NULL COMMENT '订单应付金额，单位人民币分',
    credits INTEGER NOT NULL COMMENT '订单支付成功后发放的面试次数',
    currency VARCHAR(3) NOT NULL COMMENT '订单币种代码，当前为 CNY（人民币）',
    status VARCHAR(24) NOT NULL COMMENT '订单状态：PENDING=待支付，PAID=已支付，REFUNDED=已全额退款',
    provider_trade_no VARCHAR(120) COMMENT '支付供应商唯一交易流水号；未支付时为空，开发支付使用 dev 前缀',
    paid_at DATETIME(6) COMMENT '支付成功时间（UTC）；未支付时为空',
    refunded_at DATETIME(6) COMMENT '全额退款完成时间（UTC）；未退款时为空',
    id VARCHAR(36) NOT NULL COMMENT '记录唯一标识（UUID，由应用生成）',
    created_at DATETIME(6) NOT NULL COMMENT '记录创建时间（UTC，精确到微秒，由应用写入）',
    updated_at DATETIME(6) NOT NULL COMMENT '记录最近更新时间（UTC，由应用在修改记录时刷新）',
    PRIMARY KEY (id),
    FOREIGN KEY(user_id) REFERENCES users (id),
    UNIQUE (provider_trade_no)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='训练额度套餐订单、支付状态及退款信息' COLLATE utf8mb4_0900_bin;

CREATE INDEX ix_orders_user_id ON orders (user_id);

CREATE TABLE outbox_events (
    user_id VARCHAR(36) NOT NULL COMMENT '所属用户 ID，关联 users.id，用于数据归属校验',
    event_type VARCHAR(40) NOT NULL COMMENT '任务类型，例如 PARSE_JOB、PARSE_RESUME、PREPARE_INTERVIEW、PROCESS_ANSWER、GENERATE_REPORT、DELETE_MEDIA、DELETE_USER',
    aggregate_id VARCHAR(36) NOT NULL COMMENT '本任务操作的业务对象 ID，具体含义由 event_type 决定',
    payload_encrypted LONGTEXT COMMENT '加密任务输入 JSON，例如回答分段资产列表或报告冻结快照',
    result_encrypted LONGTEXT COMMENT '加密任务结果 JSON；任务成功前可为空',
    status VARCHAR(24) NOT NULL COMMENT '任务状态：PENDING=待处理，QUEUED=已入队，RUNNING=执行中，RETRYING=等待重试，SUCCEEDED=成功，FAILED=失败，CANCELLED=取消',
    retry_count INTEGER NOT NULL COMMENT '当前处理周期内已失败次数，用于退避和判断重试上限',
    available_at DATETIME(6) NOT NULL COMMENT '任务最早可执行时间（UTC），延迟重试时向后推迟',
    lease_until DATETIME(6) COMMENT '派发或执行租约到期时间（UTC），过期后可重新领取',
    lease_token VARCHAR(36) COMMENT '执行租约唯一令牌，用于防止并发重复领取和过期执行者写回',
    error_code VARCHAR(80) COMMENT '最近一次任务失败的业务错误码',
    request_id VARCHAR(80) COMMENT '创建任务的 HTTP 请求追踪 ID；定时任务可为空',
    id VARCHAR(36) NOT NULL COMMENT '记录唯一标识（UUID，由应用生成）',
    created_at DATETIME(6) NOT NULL COMMENT '记录创建时间（UTC，精确到微秒，由应用写入）',
    updated_at DATETIME(6) NOT NULL COMMENT '记录最近更新时间（UTC，由应用在修改记录时刷新）',
    PRIMARY KEY (id),
    FOREIGN KEY(user_id) REFERENCES users (id)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='事务 Outbox 与异步任务，支持可靠派发、租约和失败重试' COLLATE utf8mb4_0900_bin;

CREATE INDEX ix_outbox_dispatch ON outbox_events (status, available_at);

CREATE INDEX ix_outbox_events_user_id ON outbox_events (user_id);

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
    interview_credits JSON COMMENT '按面试类型发放的次数：TEXT=文字、VOICE=语音、VIDEO=视频；NULL=历史通用次数套餐',
    PRIMARY KEY (id),
    CONSTRAINT uq_product_sku_version UNIQUE (code, version),
    FOREIGN KEY(created_by) REFERENCES admin_users (id)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='按 SKU 和版本保存商品配置，已发布版本只影响新订单' COLLATE utf8mb4_0900_bin;

CREATE INDEX ix_product_skus_created_at ON product_skus (created_at);

CREATE TABLE promotion_links (
    id VARCHAR(36) NOT NULL COMMENT '记录唯一标识（UUID）',
    created_at DATETIME(6) NOT NULL COMMENT '创建时间（UTC）',
    updated_at DATETIME(6) NOT NULL COMMENT '最后变更时间（UTC）',
    code VARCHAR(24) NOT NULL COMMENT '公开推广码，唯一',
    name VARCHAR(100) NOT NULL COMMENT '推广活动名称',
    promoter_name VARCHAR(100) NOT NULL COMMENT '推广合作方或推广人名称',
    alipay_account_encrypted LONGTEXT COMMENT '线下打款用支付宝账号（加密存储）',
    rate_bps INTEGER NOT NULL COMMENT '佣金比例，单位基点；100表示百分之一，范围0至10000',
    status VARCHAR(16) NOT NULL COMMENT '链接状态：ACTIVE=可用于新注册，DISABLED=停止新注册归因',
    created_by VARCHAR(36) NOT NULL COMMENT '创建推广链接的管理员 ID',
    PRIMARY KEY (id),
    UNIQUE (code),
    FOREIGN KEY(created_by) REFERENCES admin_users (id)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='管理后台创建的分佣推广链接及佣金配置' COLLATE utf8mb4_0900_bin;

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

CREATE TABLE referral_registrations (
    id VARCHAR(36) NOT NULL COMMENT '记录唯一标识（UUID）',
    created_at DATETIME(6) NOT NULL COMMENT '创建时间（UTC）',
    updated_at DATETIME(6) NOT NULL COMMENT '最后变更时间（UTC）',
    invitee_id VARCHAR(36) NOT NULL COMMENT '验证邮箱并成功注册的受邀用户 ID',
    inviter_id VARCHAR(36) NOT NULL COMMENT '邀请人用户 ID',
    config_version INTEGER NOT NULL COMMENT '注册时邀请规则版本，0表示系统默认版本',
    threshold INTEGER NOT NULL COMMENT '注册时门槛快照，单位人',
    inviter_rewards JSON NOT NULL COMMENT '注册时邀请人奖励次数组合快照',
    invitee_rewards JSON NOT NULL COMMENT '注册时受邀人奖励次数组合快照',
    PRIMARY KEY (id),
    UNIQUE (invitee_id),
    FOREIGN KEY(invitee_id) REFERENCES users (id),
    FOREIGN KEY(inviter_id) REFERENCES users (id)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='有效邀请注册记录；一名新用户仅归因一次，规则取注册时配置快照' COLLATE utf8mb4_0900_bin;

CREATE INDEX ix_referral_inviter_version ON referral_registrations (inviter_id, config_version);

CREATE TABLE user_consents (
    user_id VARCHAR(36) NOT NULL COMMENT '所属用户 ID，关联 users.id，用于数据归属校验',
    consent_type VARCHAR(30) NOT NULL COMMENT '授权类型：PRIVACY/TERMS/AI_PROCESSING/MICROPHONE/CAMERA/SCREEN_CAPTURE；SUPPORT_REVIEW=后台人工审阅授权',
    policy_version VARCHAR(30) NOT NULL COMMENT '用户同意的隐私政策或协议版本号',
    revoked_at DATETIME(6) COMMENT '撤销授权时间（UTC）；为空表示尚未撤销',
    id VARCHAR(36) NOT NULL COMMENT '记录唯一标识（UUID，由应用生成）',
    created_at DATETIME(6) NOT NULL COMMENT '记录创建时间（UTC，精确到微秒，由应用写入）',
    updated_at DATETIME(6) NOT NULL COMMENT '记录最近更新时间（UTC，由应用在修改记录时刷新）',
    PRIMARY KEY (id),
    FOREIGN KEY(user_id) REFERENCES users (id)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='用户隐私、协议、AI 处理及录音录像授权记录' COLLATE utf8mb4_0900_bin;

CREATE INDEX ix_user_consents_user_id ON user_consents (user_id);

CREATE TABLE interviews (
    user_id VARCHAR(36) NOT NULL COMMENT '所属用户 ID，关联 users.id，用于数据归属校验',
    job_profile_id VARCHAR(36) NOT NULL COMMENT '目标岗位档案 ID，关联 job_profiles.id',
    resume_asset_id VARCHAR(36) COMMENT '可选简历解析记录 ID，关联 resume_assets.id',
    status VARCHAR(24) NOT NULL COMMENT '面试状态：DRAFT=草稿，PREPARING=准备中，READY=待开始，IN_PROGRESS=进行中，PAUSED=暂停，FINISHING=报告生成中，COMPLETED=完成，FAILED=准备失败，REPORT_FAILED=报告失败，CANCELLED=取消',
    mode VARCHAR(16) NOT NULL COMMENT '面试模式：TEXT=文字，VOICE=语音，VIDEO=视频',
    config_json JSON NOT NULL COMMENT '面试配置 JSON，含时长、难度、题数、语言、面试类型和追问/澄清上限',
    current_turn_no INTEGER NOT NULL COMMENT '当前轮次序号，从 1 开始；首题生成前为 0',
    version INTEGER NOT NULL COMMENT '乐观锁版本号；状态或轮次更新时递增，防止并发覆盖',
    started_at DATETIME(6) COMMENT '面试正式开始时间（UTC）；未开始时为空',
    ended_at DATETIME(6) COMMENT '面试结束或取消时间（UTC）；未结束时为空',
    paused_at DATETIME(6) COMMENT '本次暂停开始时间（UTC）；未暂停或恢复后为空',
    paused_seconds INTEGER NOT NULL COMMENT '累计暂停时长，单位秒；计算有效面试时间时扣除',
    prepare_task_id VARCHAR(36) COMMENT '面试计划与首题准备任务 ID，关联 outbox_events.id',
    report_task_id VARCHAR(36) COMMENT '最近一次报告生成任务 ID，关联 outbox_events.id',
    failure_code VARCHAR(80) COMMENT '最近一次准备或报告失败的业务错误码；成功后清空',
    deleted_at DATETIME(6) COMMENT '面试记录软删除时间（UTC）；未删除时为空',
    id VARCHAR(36) NOT NULL COMMENT '记录唯一标识（UUID，由应用生成）',
    created_at DATETIME(6) NOT NULL COMMENT '记录创建时间（UTC，精确到微秒，由应用写入）',
    updated_at DATETIME(6) NOT NULL COMMENT '记录最近更新时间（UTC，由应用在修改记录时刷新）',
    PRIMARY KEY (id),
    FOREIGN KEY(user_id) REFERENCES users (id),
    FOREIGN KEY(job_profile_id) REFERENCES job_profiles (id)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='一次模拟面试的配置、状态、当前轮次及起止时间' COLLATE utf8mb4_0900_bin;

CREATE INDEX ix_interviews_status ON interviews (status);

CREATE INDEX ix_interviews_user_created ON interviews (user_id, created_at);

CREATE TABLE order_product_snapshots (
    id VARCHAR(36) NOT NULL COMMENT '记录唯一标识（UUID）',
    created_at DATETIME(6) NOT NULL COMMENT '创建时间（UTC）',
    updated_at DATETIME(6) NOT NULL COMMENT '更新时间（UTC）',
    order_id VARCHAR(36) NOT NULL COMMENT '订单 ID，关联 orders.id',
    product_id VARCHAR(36) COMMENT '商品版本 ID；历史常量商品可空',
    snapshot_json JSON NOT NULL COMMENT 'SKU、版本、金额、币种和次数快照',
    PRIMARY KEY (id),
    CONSTRAINT uq_order_product_snapshot UNIQUE (order_id),
    FOREIGN KEY(order_id) REFERENCES orders (id),
    FOREIGN KEY(product_id) REFERENCES product_skus (id)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='订单创建时固化的商品价格和权益快照，发布新价不改历史订单' COLLATE utf8mb4_0900_bin;

CREATE INDEX ix_order_product_snapshots_created_at ON order_product_snapshots (created_at);

CREATE TABLE promotion_attributions (
    id VARCHAR(36) NOT NULL COMMENT '记录唯一标识（UUID）',
    created_at DATETIME(6) NOT NULL COMMENT '创建时间（UTC）',
    updated_at DATETIME(6) NOT NULL COMMENT '最后变更时间（UTC）',
    user_id VARCHAR(36) NOT NULL COMMENT '被推广注册的用户 ID',
    link_id VARCHAR(36) NOT NULL COMMENT '推广链接 ID',
    PRIMARY KEY (id),
    UNIQUE (user_id),
    FOREIGN KEY(user_id) REFERENCES users (id),
    FOREIGN KEY(link_id) REFERENCES promotion_links (id)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='新注册用户的推广归因，账号唯一且不能在购买时改写' COLLATE utf8mb4_0900_bin;

CREATE TABLE promotion_commissions (
    id VARCHAR(36) NOT NULL COMMENT '记录唯一标识（UUID）',
    created_at DATETIME(6) NOT NULL COMMENT '创建时间（UTC）',
    updated_at DATETIME(6) NOT NULL COMMENT '最后变更时间（UTC）',
    order_id VARCHAR(36) NOT NULL COMMENT '产生佣金的订单 ID',
    link_id VARCHAR(36) NOT NULL COMMENT '归属推广链接 ID',
    rate_bps INTEGER NOT NULL COMMENT '订单创建时固化的佣金比例（基点）',
    amount_minor INTEGER NOT NULL COMMENT '佣金金额（人民币分），按订单实付乘比例向下取整',
    status VARCHAR(16) NOT NULL COMMENT '应计状态：ACCRUED=已计提，REVERSED=退款已冲销，SETTLED=已线下打款',
    reversed_at DATETIME(6) COMMENT '退款冲销时间（UTC）',
    settled_at DATETIME(6) COMMENT '线下打款结算时间（UTC）',
    PRIMARY KEY (id),
    UNIQUE (order_id),
    FOREIGN KEY(order_id) REFERENCES orders (id),
    FOREIGN KEY(link_id) REFERENCES promotion_links (id)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='订单分佣应计台账；订单唯一，退款冲销，不执行真实转账' COLLATE utf8mb4_0900_bin;

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
    FOREIGN KEY(order_id) REFERENCES orders (id),
    FOREIGN KEY(applicant_id) REFERENCES admin_users (id),
    FOREIGN KEY(approval_id) REFERENCES admin_approval_requests (id)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='全额退款申请及双人审批；到账结果必须由渠道确认' COLLATE utf8mb4_0900_bin;

CREATE INDEX ix_refund_requests_created_at ON refund_requests (created_at);

CREATE TABLE feedback (
    user_id VARCHAR(36) NOT NULL COMMENT '所属用户 ID，关联 users.id，用于数据归属校验',
    interview_id VARCHAR(36) COMMENT '反馈涉及的面试 ID；通用反馈可为空',
    category VARCHAR(30) NOT NULL COMMENT '反馈类型：CONTENT=内容问题，REPORT=报告纠错，PRIVACY=隐私，OTHER=其他',
    content_encrypted LONGTEXT NOT NULL COMMENT '加密保存的用户反馈、纠错或申诉内容',
    status VARCHAR(30) NOT NULL COMMENT '处理状态：OPEN=待处理，IN_REVIEW=审核中，RESOLVED=已解决，REJECTED=已驳回',
    reply_encrypted LONGTEXT COMMENT '加密保存的管理员处理回复；尚未回复时为空',
    id VARCHAR(36) NOT NULL COMMENT '记录唯一标识（UUID，由应用生成）',
    created_at DATETIME(6) NOT NULL COMMENT '记录创建时间（UTC，精确到微秒，由应用写入）',
    updated_at DATETIME(6) NOT NULL COMMENT '记录最近更新时间（UTC，由应用在修改记录时刷新）',
    PRIMARY KEY (id),
    FOREIGN KEY(user_id) REFERENCES users (id),
    FOREIGN KEY(interview_id) REFERENCES interviews (id)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='用户反馈、报告纠错、申诉及管理员处理回复' COLLATE utf8mb4_0900_bin;

CREATE INDEX ix_feedback_user_id ON feedback (user_id);

CREATE TABLE interview_plans (
    interview_id VARCHAR(36) NOT NULL COMMENT '所属面试 ID，关联 interviews.id；每场面试一份计划',
    plan_encrypted LONGTEXT NOT NULL COMMENT '加密面试计划 JSON，含主问题、考察维度、JD 证据及简历关联',
    prompt_version VARCHAR(30) NOT NULL COMMENT '生成计划使用的提示词版本号',
    model_name VARCHAR(100) NOT NULL COMMENT '生成计划使用的模型名称；mock 表示开发模拟供应商',
    id VARCHAR(36) NOT NULL COMMENT '记录唯一标识（UUID，由应用生成）',
    created_at DATETIME(6) NOT NULL COMMENT '记录创建时间（UTC，精确到微秒，由应用写入）',
    updated_at DATETIME(6) NOT NULL COMMENT '记录最近更新时间（UTC，由应用在修改记录时刷新）',
    PRIMARY KEY (id),
    UNIQUE (interview_id),
    FOREIGN KEY(interview_id) REFERENCES interviews (id)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='结合岗位、简历和配置生成的个性化面试计划' COLLATE utf8mb4_0900_bin;

CREATE TABLE interview_reports (
    interview_id VARCHAR(36) NOT NULL COMMENT '所属面试 ID，关联 interviews.id',
    report_version INTEGER NOT NULL COMMENT '报告版本号，从 1 递增；重新生成保留旧版本',
    overall_score JSON COMMENT '综合得分（0～100）；证据不足或模拟环境时为 JSON null',
    content_encrypted LONGTEXT NOT NULL COMMENT '加密完整报告 JSON，含维度评分、逐题证据、建议和原始轮次快照',
    prompt_version VARCHAR(30) NOT NULL COMMENT '生成报告使用的提示词与评分规则版本号',
    model_name VARCHAR(100) NOT NULL COMMENT '生成报告使用的模型名称；mock 表示开发模拟供应商',
    id VARCHAR(36) NOT NULL COMMENT '记录唯一标识（UUID，由应用生成）',
    created_at DATETIME(6) NOT NULL COMMENT '记录创建时间（UTC，精确到微秒，由应用写入）',
    updated_at DATETIME(6) NOT NULL COMMENT '记录最近更新时间（UTC，由应用在修改记录时刷新）',
    PRIMARY KEY (id),
    UNIQUE (interview_id, report_version),
    FOREIGN KEY(interview_id) REFERENCES interviews (id)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='按版本保存的面试评估报告及综合评分' COLLATE utf8mb4_0900_bin;

CREATE INDEX ix_interview_reports_interview_id ON interview_reports (interview_id);

CREATE TABLE interview_turns (
    interview_id VARCHAR(36) NOT NULL COMMENT '所属面试 ID，关联 interviews.id',
    turn_no INTEGER NOT NULL COMMENT '场内轮次序号，从 1 开始；与 interview_id 组合唯一',
    parent_turn_id VARCHAR(36) COMMENT '被追问或澄清的上一轮 ID；主问题为空',
    turn_type VARCHAR(24) NOT NULL COMMENT '题目类型：QUESTION=主问题，FOLLOW_UP=追问，CLARIFICATION=澄清',
    main_index INTEGER NOT NULL COMMENT '对应主问题在计划中的下标，从 0 开始；追问和澄清沿用原主问题下标',
    competency VARCHAR(120) NOT NULL COMMENT '本轮问题考察的岗位能力维度',
    question_encrypted LONGTEXT NOT NULL COMMENT '加密保存的 AI 提问文本',
    question_asset_id VARCHAR(36) COMMENT 'AI 提问 TTS 语音资产 ID，关联 media_assets.id；文字模式可为空',
    answer_asset_id VARCHAR(36) COMMENT '回答的首段音视频资产 ID；文字回答为空，多段完整列表记录在任务载荷中',
    transcript_encrypted LONGTEXT COMMENT '加密回答文本：用户文字输入或 ASR 语音转写，多段回答按顺序合并',
    confidence JSON COMMENT 'ASR 置信度（0～1），文字回答为 1；供应商未提供置信度时为 null',
    decision_encrypted LONGTEXT COMMENT '加密 AI 决策 JSON，含追问/下一题/澄清/结束、原因和回答摘要',
    status VARCHAR(24) NOT NULL COMMENT '轮次状态：WAITING_ANSWER=待回答，SUBMITTED=已提交待处理，COMPLETED=处理完成，FAILED=处理失败，DELETED=已删除',
    task_id VARCHAR(36) COMMENT '回答转写与分析任务 ID，关联 outbox_events.id',
    id VARCHAR(36) NOT NULL COMMENT '记录唯一标识（UUID，由应用生成）',
    created_at DATETIME(6) NOT NULL COMMENT '记录创建时间（UTC，精确到微秒，由应用写入）',
    updated_at DATETIME(6) NOT NULL COMMENT '记录最近更新时间（UTC，由应用在修改记录时刷新）',
    PRIMARY KEY (id),
    UNIQUE (interview_id, turn_no),
    FOREIGN KEY(interview_id) REFERENCES interviews (id),
    FOREIGN KEY(parent_turn_id) REFERENCES interview_turns (id)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='面试主问题、追问、澄清及对应回答和 AI 决策' COLLATE utf8mb4_0900_bin;

CREATE INDEX ix_interview_turns_interview_id ON interview_turns (interview_id);

CREATE TABLE media_assets (
    user_id VARCHAR(36) NOT NULL COMMENT '所属用户 ID，关联 users.id，用于数据归属校验',
    interview_id VARCHAR(36) COMMENT '关联面试 ID；独立简历或 JD 图片可为空',
    turn_id VARCHAR(36) COMMENT '关联面试轮次 ID；独立文件可为空，分段录像共用同一轮次',
    media_type VARCHAR(24) NOT NULL COMMENT '媒体用途：AUDIO=回答录音，VIDEO=回答录像，RESUME=简历，JD_IMAGE=岗位图片，TTS=提问语音',
    object_key VARCHAR(500) NOT NULL COMMENT '私有对象存储中的唯一文件键，不是永久公开下载地址',
    filename VARCHAR(200) NOT NULL COMMENT '上传或生成文件的名称，包含文件扩展名',
    mime_type VARCHAR(120) NOT NULL COMMENT '文件 MIME 类型，例如 audio/wav、video/mp4、application/pdf',
    size_bytes BIGINT NOT NULL COMMENT '文件大小，单位字节；完成上传时与实际对象大小核对',
    duration_ms INTEGER COMMENT '音视频实际时长，单位毫秒；非音视频文件可为空',
    checksum VARCHAR(64) COMMENT '文件 SHA-256 校验值，用于内容完整性核验',
    etag VARCHAR(128) COMMENT '对象存储返回的 ETag，用于上传校验和条件复制',
    status VARCHAR(24) NOT NULL COMMENT '媒体状态：PENDING_UPLOAD=待上传，READY=可用，DELETING=删除中，DELETED=已删除；过期可由 expires_at 判断',
    expires_at DATETIME(6) NOT NULL COMMENT '文件保留截止时间（UTC），到期禁止访问并安排清理',
    deleted_at DATETIME(6) COMMENT '文件删除完成时间（UTC）；未删除时为空',
    id VARCHAR(36) NOT NULL COMMENT '记录唯一标识（UUID，由应用生成）',
    created_at DATETIME(6) NOT NULL COMMENT '记录创建时间（UTC，精确到微秒，由应用写入）',
    updated_at DATETIME(6) NOT NULL COMMENT '记录最近更新时间（UTC，由应用在修改记录时刷新）',
    PRIMARY KEY (id),
    FOREIGN KEY(user_id) REFERENCES users (id),
    FOREIGN KEY(interview_id) REFERENCES interviews (id),
    UNIQUE (object_key)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='简历、录音、录像及提问语音的对象存储元数据，不保存文件二进制' COLLATE utf8mb4_0900_bin;

CREATE INDEX ix_media_assets_expires_at ON media_assets (expires_at);

CREATE INDEX ix_media_assets_interview_id ON media_assets (interview_id);

CREATE INDEX ix_media_assets_user_id ON media_assets (user_id);

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
    FOREIGN KEY(interview_id) REFERENCES interviews (id),
    FOREIGN KEY(assignee_id) REFERENCES admin_users (id),
    FOREIGN KEY(created_by) REFERENCES admin_users (id)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='授权质检样本与问题处理任务，限定审阅人资源范围' COLLATE utf8mb4_0900_bin;

CREATE INDEX ix_quality_review_tasks_created_at ON quality_review_tasks (created_at);

CREATE TABLE usage_ledger (
    user_id VARCHAR(36) NOT NULL COMMENT '所属用户 ID，关联 users.id，用于数据归属校验',
    interview_id VARCHAR(36) COMMENT '关联面试 ID；注册赠送、购买或退款可为空',
    usage_type VARCHAR(30) NOT NULL COMMENT '账本类型：WELCOME=注册赠送，INTERVIEW=面试额度，PURCHASE=购买，REFUND=退款',
    quantity INTEGER NOT NULL COMMENT '额度变动次数；增加为正、扣减为负，预占释放由 status 表示',
    amount_minor BIGINT NOT NULL COMMENT '对应金额，单位人民币分；购买为正、退款为负，无金额变动为 0',
    status VARCHAR(24) NOT NULL COMMENT '额度状态：GRANTED=已发放，RESERVED=已预占，CONSUMED=已消费，RELEASED=已释放，REFUNDED=已退款',
    idempotency_key VARCHAR(200) NOT NULL COMMENT '全局唯一账本幂等键，用于防止重复发放、扣费或退款',
    id VARCHAR(36) NOT NULL COMMENT '记录唯一标识（UUID，由应用生成）',
    created_at DATETIME(6) NOT NULL COMMENT '记录创建时间（UTC，精确到微秒，由应用写入）',
    updated_at DATETIME(6) NOT NULL COMMENT '记录最近更新时间（UTC，由应用在修改记录时刷新）',
    PRIMARY KEY (id),
    FOREIGN KEY(user_id) REFERENCES users (id),
    FOREIGN KEY(interview_id) REFERENCES interviews (id),
    UNIQUE (idempotency_key)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='面试额度预占、消费、释放、购买及退款账本' COLLATE utf8mb4_0900_bin;

CREATE INDEX ix_usage_ledger_interview_id ON usage_ledger (interview_id);

CREATE INDEX ix_usage_ledger_user_id ON usage_ledger (user_id);

CREATE TABLE ai_call_logs (
    user_id VARCHAR(36) NOT NULL COMMENT '所属用户 ID，关联 users.id，用于数据归属校验',
    interview_id VARCHAR(36) COMMENT '关联面试 ID；独立岗位或简历解析调用可为空',
    turn_id VARCHAR(36) COMMENT '关联轮次 ID；非逐题调用或新轮次回滚时可为空',
    provider VARCHAR(40) NOT NULL COMMENT 'AI 适配器类型，例如 mock、http、compatible',
    model VARCHAR(100) NOT NULL COMMENT '本次调用使用的实际模型名称',
    call_type VARCHAR(30) NOT NULL COMMENT '调用用途：job=岗位解析，resume=简历解析，plan=计划，decision=追问决策，report=报告，asr=转写，tts=语音合成',
    input_tokens INTEGER NOT NULL COMMENT '本次调用的输入 Token 数；供应商未计量时为 0',
    output_tokens INTEGER NOT NULL COMMENT '本次调用的输出 Token 数；供应商未计量时为 0',
    latency_ms INTEGER NOT NULL COMMENT '本次供应商调用耗时，单位毫秒',
    cost_microunits BIGINT NOT NULL COMMENT 'AI 调用成本，单位人民币微元（1 元=1000000 微元）；按网关账单或配置费率计价',
    units JSON COMMENT '额外用量 JSON，可含 ASR 毫秒数、TTS 字符数及成本来源',
    status VARCHAR(24) NOT NULL COMMENT '调用结果：SUCCEEDED=成功，FAILED=失败；重试逐次记录',
    request_id VARCHAR(80) COMMENT '触发此次调用的 HTTP 请求追踪 ID',
    id VARCHAR(36) NOT NULL COMMENT '记录唯一标识（UUID，由应用生成）',
    created_at DATETIME(6) NOT NULL COMMENT '记录创建时间（UTC，精确到微秒，由应用写入）',
    updated_at DATETIME(6) NOT NULL COMMENT '记录最近更新时间（UTC，由应用在修改记录时刷新）',
    PRIMARY KEY (id),
    FOREIGN KEY(user_id) REFERENCES users (id),
    FOREIGN KEY(interview_id) REFERENCES interviews (id),
    FOREIGN KEY(turn_id) REFERENCES interview_turns (id)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='AI 调用的供应商、模型、用量、延迟、结果和成本记录' COLLATE utf8mb4_0900_bin;

CREATE INDEX ix_ai_call_logs_interview_id ON ai_call_logs (interview_id);

CREATE INDEX ix_ai_call_logs_user_id ON ai_call_logs (user_id);

CREATE TABLE invitation_rewards (
    id VARCHAR(36) NOT NULL COMMENT '记录唯一标识（UUID）',
    created_at DATETIME(6) NOT NULL COMMENT '创建时间（UTC）',
    updated_at DATETIME(6) NOT NULL COMMENT '最后变更时间（UTC）',
    inviter_id VARCHAR(36) NOT NULL COMMENT '获得奖励的邀请人 ID',
    config_version INTEGER NOT NULL COMMENT '活动配置版本',
    milestone INTEGER NOT NULL COMMENT '已达到的第几个门槛，从 1 递增',
    usage_id VARCHAR(36) NOT NULL COMMENT '实际奖励权益流水 ID',
    PRIMARY KEY (id),
    CONSTRAINT uq_invitation_reward_milestone UNIQUE (inviter_id, config_version, milestone),
    FOREIGN KEY(inviter_id) REFERENCES users (id),
    FOREIGN KEY(usage_id) REFERENCES usage_ledger (id)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='邀请人每个配置版本的里程碑奖励，唯一键防止重复发放' COLLATE utf8mb4_0900_bin;

CREATE TABLE resume_assets (
    user_id VARCHAR(36) NOT NULL COMMENT '所属用户 ID，关联 users.id，用于数据归属校验',
    asset_id VARCHAR(36) NOT NULL COMMENT '简历原始文件资产 ID，关联 media_assets.id，一份文件对应一份解析记录',
    parse_status VARCHAR(24) NOT NULL COMMENT '简历解析状态：PENDING_UPLOAD=待上传，PROCESSING=处理中，READY=就绪，FAILED=失败，DELETED=已删除',
    text_encrypted LONGTEXT COMMENT '从 PDF/DOCX 提取的简历文字原文，采用加密存储',
    summary_encrypted LONGTEXT COMMENT '加密结构化简历 JSON，含教育、工作、项目、技能及原文证据',
    task_id VARCHAR(36) COMMENT '简历解析异步任务 ID，关联 outbox_events.id',
    id VARCHAR(36) NOT NULL COMMENT '记录唯一标识（UUID，由应用生成）',
    created_at DATETIME(6) NOT NULL COMMENT '记录创建时间（UTC，精确到微秒，由应用写入）',
    updated_at DATETIME(6) NOT NULL COMMENT '记录最近更新时间（UTC，由应用在修改记录时刷新）',
    PRIMARY KEY (id),
    FOREIGN KEY(user_id) REFERENCES users (id),
    UNIQUE (asset_id),
    FOREIGN KEY(asset_id) REFERENCES media_assets (id)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='简历文件关联、解析状态、加密原文和结构化简历摘要' COLLATE utf8mb4_0900_bin;

CREATE INDEX ix_resume_assets_user_id ON resume_assets (user_id);

CREATE TABLE usage_credit_allocations (
    id VARCHAR(36) NOT NULL COMMENT '权益流水分类明细 ID（UUID）',
    usage_id VARCHAR(36) NOT NULL COMMENT '权益流水 ID，关联 usage_ledger.id',
    mode VARCHAR(16) NOT NULL COMMENT '次数来源：TEXT/VOICE/VIDEO=对应类型；UNIVERSAL=历史通用次数',
    quantity INTEGER NOT NULL COMMENT '该类型次数变化，单位次；发放为正、预占或退款为负；释放按原来源返还',
    created_at DATETIME(6) NOT NULL COMMENT '创建时间（UTC）',
    updated_at DATETIME(6) NOT NULL COMMENT '最后变更时间（UTC）',
    PRIMARY KEY (id),
    CONSTRAINT uq_usage_credit_mode UNIQUE (usage_id, mode),
    FOREIGN KEY(usage_id) REFERENCES usage_ledger (id)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='权益流水的类型分配明细，保证取消、重试及退款使用原始类型；历史无明细流水视为通用次数' COLLATE utf8mb4_0900_bin;

CREATE TABLE user_accounts (
    id VARCHAR(36) NOT NULL COMMENT '记录唯一标识（UUID）',
    created_at DATETIME(6) NOT NULL COMMENT '创建时间（UTC）',
    updated_at DATETIME(6) NOT NULL COMMENT '最后变更时间（UTC）',
    user_id VARCHAR(36) NOT NULL COMMENT '用户 ID，关联 users.id',
    email_hash VARCHAR(64) COMMENT '规范化邮箱的 HMAC，唯一查找键；不保存明文邮箱',
    email_encrypted LONGTEXT COMMENT '加密的已验证邮箱地址',
    password_hash VARCHAR(256) COMMENT 'scrypt 密码散列，含随机盐；不保存明文密码',
    auth_version INTEGER NOT NULL COMMENT '用户会话版本；改密和换绑后递增，使旧令牌失效' DEFAULT '0',
    avatar_asset_id VARCHAR(36) COMMENT '已验证的头像媒体资产 ID',
    system_avatar_key VARCHAR(16) COMMENT '用户选择的系统头像目录标识',
    phone_encrypted LONGTEXT COMMENT '可选联系手机号（加密）；仅联系信息，未认证且不用于登录',
    PRIMARY KEY (id),
    UNIQUE (user_id),
    FOREIGN KEY(user_id) REFERENCES users (id),
    UNIQUE (email_hash),
    FOREIGN KEY(avatar_asset_id) REFERENCES media_assets (id)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='用户邮箱账号、安全凭证与资料扩展；关联既有业务用户' COLLATE utf8mb4_0900_bin;

ALTER TABLE interviews ADD CONSTRAINT fk_interview_resume FOREIGN KEY(resume_asset_id) REFERENCES resume_assets (id);

ALTER TABLE media_assets ADD CONSTRAINT fk_media_turn FOREIGN KEY(turn_id) REFERENCES interview_turns (id);

ALTER TABLE interview_turns ADD CONSTRAINT fk_turn_answer FOREIGN KEY(answer_asset_id) REFERENCES media_assets (id);

ALTER TABLE interview_turns ADD CONSTRAINT fk_turn_tts FOREIGN KEY(question_asset_id) REFERENCES media_assets (id);

INSERT INTO alembic_version (version_num) VALUES ('e913c5ab7d20');
