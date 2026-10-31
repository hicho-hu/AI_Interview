-- AI 模拟面试服务：MySQL 8.4 初始化结构（17 张业务表 + Alembic 版本表）
-- 来源：Alembic base -> f4c8a1d2e903，与当前应用迁移一致。
-- 在已创建的空数据库中执行；不包含任何业务数据、密码、Redis 数据或媒体文件。
-- 示例：mysql --default-character-set=utf8mb4 -h 127.0.0.1 -u interview -p interview < docs/sql/ai_interview_mysql.sql
-- 不要在已经执行 Alembic 迁移的数据库中重复导入。
-- MySQL DDL 会隐式提交，执行失败时应检查错误，不应当作可整体回滚的事务脚本。
-- DATETIME(6) 按 UTC 存储；ID/时间/状态等客户端默认值由应用写入。

CREATE TABLE alembic_version (
    version_num VARCHAR(32) NOT NULL,
    CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
);

-- Running upgrade  -> 867baba5ca32

CREATE TABLE revoked_tokens (
    jti VARCHAR(36) NOT NULL,
    expires_at DATETIME(6) NOT NULL,
    id VARCHAR(36) NOT NULL,
    created_at DATETIME(6) NOT NULL,
    updated_at DATETIME(6) NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (jti)
)ENGINE=InnoDB CHARSET=utf8mb4 COLLATE utf8mb4_0900_bin;

CREATE INDEX ix_revoked_tokens_expires_at ON revoked_tokens (expires_at);

CREATE TABLE users (
    openid_hash VARCHAR(64) NOT NULL,
    nickname VARCHAR(80) NOT NULL,
    status VARCHAR(24) NOT NULL,
    credits INTEGER NOT NULL,
    id VARCHAR(36) NOT NULL,
    created_at DATETIME(6) NOT NULL,
    updated_at DATETIME(6) NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (openid_hash)
)ENGINE=InnoDB CHARSET=utf8mb4 COLLATE utf8mb4_0900_bin;

CREATE TABLE audit_logs (
    user_id VARCHAR(36) NOT NULL,
    action VARCHAR(60) NOT NULL,
    resource_id VARCHAR(120) NOT NULL,
    request_id VARCHAR(80),
    id VARCHAR(36) NOT NULL,
    created_at DATETIME(6) NOT NULL,
    updated_at DATETIME(6) NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(user_id) REFERENCES users (id)
)ENGINE=InnoDB CHARSET=utf8mb4 COLLATE utf8mb4_0900_bin;

CREATE INDEX ix_audit_logs_user_id ON audit_logs (user_id);

CREATE TABLE idempotency_records (
    user_id VARCHAR(36) NOT NULL,
    scope VARCHAR(200) NOT NULL,
    `key` VARCHAR(120) NOT NULL,
    request_hash VARCHAR(64) NOT NULL,
    response_encrypted LONGTEXT NOT NULL,
    id VARCHAR(36) NOT NULL,
    created_at DATETIME(6) NOT NULL,
    updated_at DATETIME(6) NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(user_id) REFERENCES users (id),
    UNIQUE (user_id, scope, `key`)
)ENGINE=InnoDB CHARSET=utf8mb4 COLLATE utf8mb4_0900_bin;

CREATE TABLE job_profiles (
    user_id VARCHAR(36) NOT NULL,
    job_title VARCHAR(120) NOT NULL,
    seniority VARCHAR(30) NOT NULL,
    jd_encrypted LONGTEXT NOT NULL,
    profile_encrypted LONGTEXT,
    status VARCHAR(24) NOT NULL,
    task_id VARCHAR(36),
    id VARCHAR(36) NOT NULL,
    created_at DATETIME(6) NOT NULL,
    updated_at DATETIME(6) NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(user_id) REFERENCES users (id)
)ENGINE=InnoDB CHARSET=utf8mb4 COLLATE utf8mb4_0900_bin;

CREATE INDEX ix_job_profiles_user_id ON job_profiles (user_id);

CREATE TABLE orders (
    user_id VARCHAR(36) NOT NULL,
    product_code VARCHAR(40) NOT NULL,
    amount_minor INTEGER NOT NULL,
    credits INTEGER NOT NULL,
    currency VARCHAR(3) NOT NULL,
    status VARCHAR(24) NOT NULL,
    provider_trade_no VARCHAR(120),
    paid_at DATETIME(6),
    refunded_at DATETIME(6),
    id VARCHAR(36) NOT NULL,
    created_at DATETIME(6) NOT NULL,
    updated_at DATETIME(6) NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(user_id) REFERENCES users (id),
    UNIQUE (provider_trade_no)
)ENGINE=InnoDB CHARSET=utf8mb4 COLLATE utf8mb4_0900_bin;

CREATE INDEX ix_orders_user_id ON orders (user_id);

CREATE TABLE outbox_events (
    user_id VARCHAR(36) NOT NULL,
    event_type VARCHAR(40) NOT NULL,
    aggregate_id VARCHAR(36) NOT NULL,
    payload_encrypted LONGTEXT,
    result_encrypted LONGTEXT,
    status VARCHAR(24) NOT NULL,
    retry_count INTEGER NOT NULL,
    available_at DATETIME(6) NOT NULL,
    lease_until DATETIME(6),
    lease_token VARCHAR(36),
    error_code VARCHAR(80),
    request_id VARCHAR(80),
    id VARCHAR(36) NOT NULL,
    created_at DATETIME(6) NOT NULL,
    updated_at DATETIME(6) NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(user_id) REFERENCES users (id)
)ENGINE=InnoDB CHARSET=utf8mb4 COLLATE utf8mb4_0900_bin;

CREATE INDEX ix_outbox_dispatch ON outbox_events (status, available_at);

CREATE INDEX ix_outbox_events_user_id ON outbox_events (user_id);

CREATE TABLE user_consents (
    user_id VARCHAR(36) NOT NULL,
    consent_type VARCHAR(30) NOT NULL,
    policy_version VARCHAR(30) NOT NULL,
    revoked_at DATETIME(6),
    id VARCHAR(36) NOT NULL,
    created_at DATETIME(6) NOT NULL,
    updated_at DATETIME(6) NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(user_id) REFERENCES users (id)
)ENGINE=InnoDB CHARSET=utf8mb4 COLLATE utf8mb4_0900_bin;

CREATE INDEX ix_user_consents_user_id ON user_consents (user_id);

CREATE TABLE interviews (
    user_id VARCHAR(36) NOT NULL,
    job_profile_id VARCHAR(36) NOT NULL,
    resume_asset_id VARCHAR(36),
    status VARCHAR(24) NOT NULL,
    mode VARCHAR(16) NOT NULL,
    config_json JSON NOT NULL,
    current_turn_no INTEGER NOT NULL,
    version INTEGER NOT NULL,
    started_at DATETIME(6),
    ended_at DATETIME(6),
    paused_at DATETIME(6),
    paused_seconds INTEGER NOT NULL,
    prepare_task_id VARCHAR(36),
    report_task_id VARCHAR(36),
    failure_code VARCHAR(80),
    deleted_at DATETIME(6),
    id VARCHAR(36) NOT NULL,
    created_at DATETIME(6) NOT NULL,
    updated_at DATETIME(6) NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(job_profile_id) REFERENCES job_profiles (id),
    FOREIGN KEY(user_id) REFERENCES users (id)
)ENGINE=InnoDB CHARSET=utf8mb4 COLLATE utf8mb4_0900_bin;

CREATE INDEX ix_interviews_status ON interviews (status);

CREATE INDEX ix_interviews_user_created ON interviews (user_id, created_at);

CREATE TABLE feedback (
    user_id VARCHAR(36) NOT NULL,
    interview_id VARCHAR(36),
    category VARCHAR(30) NOT NULL,
    content_encrypted LONGTEXT NOT NULL,
    status VARCHAR(30) NOT NULL,
    reply_encrypted LONGTEXT,
    id VARCHAR(36) NOT NULL,
    created_at DATETIME(6) NOT NULL,
    updated_at DATETIME(6) NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(interview_id) REFERENCES interviews (id),
    FOREIGN KEY(user_id) REFERENCES users (id)
)ENGINE=InnoDB CHARSET=utf8mb4 COLLATE utf8mb4_0900_bin;

CREATE INDEX ix_feedback_user_id ON feedback (user_id);

CREATE TABLE interview_plans (
    interview_id VARCHAR(36) NOT NULL,
    plan_encrypted LONGTEXT NOT NULL,
    prompt_version VARCHAR(30) NOT NULL,
    model_name VARCHAR(100) NOT NULL,
    id VARCHAR(36) NOT NULL,
    created_at DATETIME(6) NOT NULL,
    updated_at DATETIME(6) NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(interview_id) REFERENCES interviews (id),
    UNIQUE (interview_id)
)ENGINE=InnoDB CHARSET=utf8mb4 COLLATE utf8mb4_0900_bin;

CREATE TABLE interview_reports (
    interview_id VARCHAR(36) NOT NULL,
    report_version INTEGER NOT NULL,
    overall_score JSON,
    content_encrypted LONGTEXT NOT NULL,
    prompt_version VARCHAR(30) NOT NULL,
    model_name VARCHAR(100) NOT NULL,
    id VARCHAR(36) NOT NULL,
    created_at DATETIME(6) NOT NULL,
    updated_at DATETIME(6) NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(interview_id) REFERENCES interviews (id),
    UNIQUE (interview_id, report_version)
)ENGINE=InnoDB CHARSET=utf8mb4 COLLATE utf8mb4_0900_bin;

CREATE INDEX ix_interview_reports_interview_id ON interview_reports (interview_id);

CREATE TABLE interview_turns (
    interview_id VARCHAR(36) NOT NULL,
    turn_no INTEGER NOT NULL,
    parent_turn_id VARCHAR(36),
    turn_type VARCHAR(24) NOT NULL,
    main_index INTEGER NOT NULL,
    competency VARCHAR(120) NOT NULL,
    question_encrypted LONGTEXT NOT NULL,
    question_asset_id VARCHAR(36),
    answer_asset_id VARCHAR(36),
    transcript_encrypted LONGTEXT,
    confidence JSON,
    decision_encrypted LONGTEXT,
    status VARCHAR(24) NOT NULL,
    task_id VARCHAR(36),
    id VARCHAR(36) NOT NULL,
    created_at DATETIME(6) NOT NULL,
    updated_at DATETIME(6) NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(interview_id) REFERENCES interviews (id),
    FOREIGN KEY(parent_turn_id) REFERENCES interview_turns (id),
    UNIQUE (interview_id, turn_no)
)ENGINE=InnoDB CHARSET=utf8mb4 COLLATE utf8mb4_0900_bin;

CREATE INDEX ix_interview_turns_interview_id ON interview_turns (interview_id);

CREATE TABLE media_assets (
    user_id VARCHAR(36) NOT NULL,
    interview_id VARCHAR(36),
    turn_id VARCHAR(36),
    media_type VARCHAR(24) NOT NULL,
    object_key VARCHAR(500) NOT NULL,
    filename VARCHAR(200) NOT NULL,
    mime_type VARCHAR(120) NOT NULL,
    size_bytes BIGINT NOT NULL,
    duration_ms INTEGER,
    checksum VARCHAR(64),
    etag VARCHAR(128),
    status VARCHAR(24) NOT NULL,
    expires_at DATETIME(6) NOT NULL,
    deleted_at DATETIME(6),
    id VARCHAR(36) NOT NULL,
    created_at DATETIME(6) NOT NULL,
    updated_at DATETIME(6) NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(interview_id) REFERENCES interviews (id),
    FOREIGN KEY(user_id) REFERENCES users (id),
    UNIQUE (object_key)
)ENGINE=InnoDB CHARSET=utf8mb4 COLLATE utf8mb4_0900_bin;

CREATE INDEX ix_media_assets_expires_at ON media_assets (expires_at);

CREATE INDEX ix_media_assets_interview_id ON media_assets (interview_id);

CREATE INDEX ix_media_assets_user_id ON media_assets (user_id);

CREATE TABLE usage_ledger (
    user_id VARCHAR(36) NOT NULL,
    interview_id VARCHAR(36),
    usage_type VARCHAR(30) NOT NULL,
    quantity INTEGER NOT NULL,
    amount_minor BIGINT NOT NULL,
    status VARCHAR(24) NOT NULL,
    idempotency_key VARCHAR(200) NOT NULL,
    id VARCHAR(36) NOT NULL,
    created_at DATETIME(6) NOT NULL,
    updated_at DATETIME(6) NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(interview_id) REFERENCES interviews (id),
    FOREIGN KEY(user_id) REFERENCES users (id),
    UNIQUE (idempotency_key)
)ENGINE=InnoDB CHARSET=utf8mb4 COLLATE utf8mb4_0900_bin;

CREATE INDEX ix_usage_ledger_interview_id ON usage_ledger (interview_id);

CREATE INDEX ix_usage_ledger_user_id ON usage_ledger (user_id);

CREATE TABLE ai_call_logs (
    user_id VARCHAR(36) NOT NULL,
    interview_id VARCHAR(36),
    turn_id VARCHAR(36),
    provider VARCHAR(40) NOT NULL,
    model VARCHAR(100) NOT NULL,
    call_type VARCHAR(30) NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    latency_ms INTEGER NOT NULL,
    cost_microunits BIGINT NOT NULL,
    units JSON,
    status VARCHAR(24) NOT NULL,
    request_id VARCHAR(80),
    id VARCHAR(36) NOT NULL,
    created_at DATETIME(6) NOT NULL,
    updated_at DATETIME(6) NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(interview_id) REFERENCES interviews (id),
    FOREIGN KEY(turn_id) REFERENCES interview_turns (id),
    FOREIGN KEY(user_id) REFERENCES users (id)
)ENGINE=InnoDB CHARSET=utf8mb4 COLLATE utf8mb4_0900_bin;

CREATE INDEX ix_ai_call_logs_interview_id ON ai_call_logs (interview_id);

CREATE INDEX ix_ai_call_logs_user_id ON ai_call_logs (user_id);

CREATE TABLE resume_assets (
    user_id VARCHAR(36) NOT NULL,
    asset_id VARCHAR(36) NOT NULL,
    parse_status VARCHAR(24) NOT NULL,
    text_encrypted LONGTEXT,
    summary_encrypted LONGTEXT,
    task_id VARCHAR(36),
    id VARCHAR(36) NOT NULL,
    created_at DATETIME(6) NOT NULL,
    updated_at DATETIME(6) NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(asset_id) REFERENCES media_assets (id),
    FOREIGN KEY(user_id) REFERENCES users (id),
    UNIQUE (asset_id)
)ENGINE=InnoDB CHARSET=utf8mb4 COLLATE utf8mb4_0900_bin;

CREATE INDEX ix_resume_assets_user_id ON resume_assets (user_id);

INSERT INTO alembic_version (version_num) VALUES ('867baba5ca32');

-- Running upgrade 867baba5ca32 -> f4c8a1d2e903

ALTER TABLE interviews ADD CONSTRAINT fk_interview_resume FOREIGN KEY(resume_asset_id) REFERENCES resume_assets (id);

ALTER TABLE interview_turns ADD CONSTRAINT fk_turn_answer FOREIGN KEY(answer_asset_id) REFERENCES media_assets (id);

ALTER TABLE interview_turns ADD CONSTRAINT fk_turn_tts FOREIGN KEY(question_asset_id) REFERENCES media_assets (id);

ALTER TABLE media_assets ADD CONSTRAINT fk_media_turn FOREIGN KEY(turn_id) REFERENCES interview_turns (id);

UPDATE alembic_version SET version_num='f4c8a1d2e903' WHERE alembic_version.version_num = '867baba5ca32';
