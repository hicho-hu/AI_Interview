-- 文字/语音/视频分类权益增量迁移；保留现有 users.credits 作为通用次数。
-- 适用于 a61d8c4e9201；与 alembic upgrade head 二选一。
-- Running upgrade a61d8c4e9201 -> b72e4f6d9102

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

ALTER TABLE product_skus ADD COLUMN interview_credits JSON COMMENT '按面试类型发放的次数：TEXT=文字、VOICE=语音、VIDEO=视频；NULL=历史通用次数套餐';

UPDATE alembic_version SET version_num='b72e4f6d9102' WHERE alembic_version.version_num = 'a61d8c4e9201';

