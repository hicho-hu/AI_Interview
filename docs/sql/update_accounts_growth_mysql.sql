-- 邮箱账号、邀请奖励、推广分佣增量迁移；适用于 b72e4f6d9102。与 alembic upgrade head 二选一。
-- Running upgrade b72e4f6d9102 -> c83f5a7e0203

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

CREATE TABLE invitation_codes (
    id VARCHAR(36) NOT NULL COMMENT '记录唯一标识（UUID）',
    created_at DATETIME(6) NOT NULL COMMENT '创建时间（UTC）',
    updated_at DATETIME(6) NOT NULL COMMENT '最后变更时间（UTC）',
    user_id VARCHAR(36) NOT NULL COMMENT '邀请人用户 ID',
    code VARCHAR(24) NOT NULL COMMENT '公开邀请码，大小写规范化',
    PRIMARY KEY (id),
    UNIQUE (code),
    UNIQUE (user_id),
    FOREIGN KEY(user_id) REFERENCES users (id),
    UNIQUE (user_id),
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
    FOREIGN KEY(created_by) REFERENCES admin_users (id),
    UNIQUE (version)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='邀请活动配置版本；新版本仅影响之后验证邮箱并注册的用户' COLLATE utf8mb4_0900_bin;

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
    FOREIGN KEY(usage_id) REFERENCES usage_ledger (id),
    FOREIGN KEY(inviter_id) REFERENCES users (id)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='邀请人每个配置版本的里程碑奖励，唯一键防止重复发放' COLLATE utf8mb4_0900_bin;

CREATE TABLE promotion_links (
    id VARCHAR(36) NOT NULL COMMENT '记录唯一标识（UUID）',
    created_at DATETIME(6) NOT NULL COMMENT '创建时间（UTC）',
    updated_at DATETIME(6) NOT NULL COMMENT '最后变更时间（UTC）',
    code VARCHAR(24) NOT NULL COMMENT '公开推广码，唯一',
    name VARCHAR(100) NOT NULL COMMENT '推广活动名称',
    promoter_name VARCHAR(100) NOT NULL COMMENT '推广合作方或推广人名称',
    rate_bps INTEGER NOT NULL COMMENT '佣金比例，单位基点；100表示百分之一，范围0至10000',
    status VARCHAR(16) NOT NULL COMMENT '链接状态：ACTIVE=可用于新注册，DISABLED=停止新注册归因',
    created_by VARCHAR(36) NOT NULL COMMENT '创建推广链接的管理员 ID',
    PRIMARY KEY (id),
    UNIQUE (code),
    FOREIGN KEY(created_by) REFERENCES admin_users (id),
    UNIQUE (code)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='管理后台创建的分佣推广链接及佣金配置' COLLATE utf8mb4_0900_bin;

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
    FOREIGN KEY(inviter_id) REFERENCES users (id),
    UNIQUE (invitee_id)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='有效邀请注册记录；一名新用户仅归因一次，规则取注册时配置快照' COLLATE utf8mb4_0900_bin;

CREATE INDEX ix_referral_inviter_version ON referral_registrations (inviter_id, config_version);

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
    phone_encrypted LONGTEXT COMMENT '可选联系手机号（加密）；仅联系信息，未认证且不用于登录',
    PRIMARY KEY (id),
    UNIQUE (email_hash),
    UNIQUE (user_id),
    FOREIGN KEY(user_id) REFERENCES users (id),
    FOREIGN KEY(avatar_asset_id) REFERENCES media_assets (id),
    UNIQUE (user_id),
    UNIQUE (email_hash)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='用户邮箱账号、安全凭证与资料扩展；关联既有业务用户' COLLATE utf8mb4_0900_bin;

CREATE TABLE promotion_attributions (
    id VARCHAR(36) NOT NULL COMMENT '记录唯一标识（UUID）',
    created_at DATETIME(6) NOT NULL COMMENT '创建时间（UTC）',
    updated_at DATETIME(6) NOT NULL COMMENT '最后变更时间（UTC）',
    user_id VARCHAR(36) NOT NULL COMMENT '被推广注册的用户 ID',
    link_id VARCHAR(36) NOT NULL COMMENT '推广链接 ID',
    PRIMARY KEY (id),
    UNIQUE (user_id),
    FOREIGN KEY(link_id) REFERENCES promotion_links (id),
    FOREIGN KEY(user_id) REFERENCES users (id),
    UNIQUE (user_id)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='新注册用户的推广归因，账号唯一且不能在购买时改写' COLLATE utf8mb4_0900_bin;

CREATE TABLE promotion_commissions (
    id VARCHAR(36) NOT NULL COMMENT '记录唯一标识（UUID）',
    created_at DATETIME(6) NOT NULL COMMENT '创建时间（UTC）',
    updated_at DATETIME(6) NOT NULL COMMENT '最后变更时间（UTC）',
    order_id VARCHAR(36) NOT NULL COMMENT '产生佣金的订单 ID',
    link_id VARCHAR(36) NOT NULL COMMENT '归属推广链接 ID',
    rate_bps INTEGER NOT NULL COMMENT '订单创建时固化的佣金比例（基点）',
    amount_minor INTEGER NOT NULL COMMENT '佣金金额（人民币分），按订单实付乘比例向下取整',
    status VARCHAR(16) NOT NULL COMMENT '应计状态：ACCRUED=已计提，REVERSED=退款已冲销',
    reversed_at DATETIME(6) COMMENT '退款冲销时间（UTC）',
    PRIMARY KEY (id),
    UNIQUE (order_id),
    FOREIGN KEY(link_id) REFERENCES promotion_links (id),
    FOREIGN KEY(order_id) REFERENCES orders (id),
    UNIQUE (order_id)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='订单分佣应计台账；订单唯一，退款冲销，不执行真实转账' COLLATE utf8mb4_0900_bin;

UPDATE alembic_version SET version_num='d4e6a8b10321' WHERE alembic_version.version_num = 'b72e4f6d9102';

