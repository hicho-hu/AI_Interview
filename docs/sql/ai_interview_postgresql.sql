-- 历史版本存档：当前服务已切换 MySQL，请使用 ai_interview_mysql.sql。
-- AI 模拟面试服务：PostgreSQL 初始化结构（仅适用于旧版本）
-- 来源：Alembic revision 867baba5ca32（base -> head），与当前代码迁移保持一致。
-- 包含：17 张业务表、1 张迁移版本表、索引、唯一约束及外键。
-- 不包含：业务数据、账号密码、Redis 数据或对象存储文件。
-- 请连接已经创建的空 PostgreSQL 数据库后执行；不创建数据库或数据库用户。
-- 导入示例（在项目根目录运行）：
-- psql -U interview -d interview -v ON_ERROR_STOP=1 -f docs/sql/ai_interview_postgresql.sql
-- 本脚本用于首次初始化，不可在已执行 Alembic 迁移的数据库中重复导入。
-- ID、时间和状态等 ORM 客户端默认值由应用写入，与迁移原样保持一致。

BEGIN;

CREATE TABLE alembic_version (
    version_num VARCHAR(32) NOT NULL,
    CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
);

-- Running upgrade  -> 867baba5ca32

CREATE TABLE revoked_tokens (
    jti VARCHAR(36) NOT NULL,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    id VARCHAR(36) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (jti)
);

CREATE INDEX ix_revoked_tokens_expires_at ON revoked_tokens (expires_at);

CREATE TABLE users (
    openid_hash VARCHAR(64) NOT NULL,
    nickname VARCHAR(80) NOT NULL,
    status VARCHAR(24) NOT NULL,
    credits INTEGER NOT NULL,
    id VARCHAR(36) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (openid_hash)
);

CREATE TABLE audit_logs (
    user_id VARCHAR(36) NOT NULL,
    action VARCHAR(60) NOT NULL,
    resource_id VARCHAR(120) NOT NULL,
    request_id VARCHAR(80),
    id VARCHAR(36) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(user_id) REFERENCES users (id)
);

CREATE INDEX ix_audit_logs_user_id ON audit_logs (user_id);

CREATE TABLE idempotency_records (
    user_id VARCHAR(36) NOT NULL,
    scope VARCHAR(200) NOT NULL,
    key VARCHAR(120) NOT NULL,
    request_hash VARCHAR(64) NOT NULL,
    response_encrypted TEXT NOT NULL,
    id VARCHAR(36) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(user_id) REFERENCES users (id),
    UNIQUE (user_id, scope, key)
);

CREATE TABLE job_profiles (
    user_id VARCHAR(36) NOT NULL,
    job_title VARCHAR(120) NOT NULL,
    seniority VARCHAR(30) NOT NULL,
    jd_encrypted TEXT NOT NULL,
    profile_encrypted TEXT,
    status VARCHAR(24) NOT NULL,
    task_id VARCHAR(36),
    id VARCHAR(36) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(user_id) REFERENCES users (id)
);

CREATE INDEX ix_job_profiles_user_id ON job_profiles (user_id);

CREATE TABLE orders (
    user_id VARCHAR(36) NOT NULL,
    product_code VARCHAR(40) NOT NULL,
    amount_minor INTEGER NOT NULL,
    credits INTEGER NOT NULL,
    currency VARCHAR(3) NOT NULL,
    status VARCHAR(24) NOT NULL,
    provider_trade_no VARCHAR(120),
    paid_at TIMESTAMP WITH TIME ZONE,
    refunded_at TIMESTAMP WITH TIME ZONE,
    id VARCHAR(36) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(user_id) REFERENCES users (id),
    UNIQUE (provider_trade_no)
);

CREATE INDEX ix_orders_user_id ON orders (user_id);

CREATE TABLE outbox_events (
    user_id VARCHAR(36) NOT NULL,
    event_type VARCHAR(40) NOT NULL,
    aggregate_id VARCHAR(36) NOT NULL,
    payload_encrypted TEXT,
    result_encrypted TEXT,
    status VARCHAR(24) NOT NULL,
    retry_count INTEGER NOT NULL,
    available_at TIMESTAMP WITH TIME ZONE NOT NULL,
    lease_until TIMESTAMP WITH TIME ZONE,
    lease_token VARCHAR(36),
    error_code VARCHAR(80),
    request_id VARCHAR(80),
    id VARCHAR(36) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(user_id) REFERENCES users (id)
);

CREATE INDEX ix_outbox_dispatch ON outbox_events (status, available_at);

CREATE INDEX ix_outbox_events_user_id ON outbox_events (user_id);

CREATE TABLE user_consents (
    user_id VARCHAR(36) NOT NULL,
    consent_type VARCHAR(30) NOT NULL,
    policy_version VARCHAR(30) NOT NULL,
    revoked_at TIMESTAMP WITH TIME ZONE,
    id VARCHAR(36) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(user_id) REFERENCES users (id)
);

CREATE INDEX ix_user_consents_user_id ON user_consents (user_id);

CREATE TABLE interviews (
    user_id VARCHAR(36) NOT NULL,
    job_profile_id VARCHAR(36) NOT NULL,
    resume_asset_id VARCHAR(36),
    status VARCHAR(24) NOT NULL,
    mode VARCHAR(16) NOT NULL,
    config_json JSONB NOT NULL,
    current_turn_no INTEGER NOT NULL,
    version INTEGER NOT NULL,
    started_at TIMESTAMP WITH TIME ZONE,
    ended_at TIMESTAMP WITH TIME ZONE,
    paused_at TIMESTAMP WITH TIME ZONE,
    paused_seconds INTEGER NOT NULL,
    prepare_task_id VARCHAR(36),
    report_task_id VARCHAR(36),
    failure_code VARCHAR(80),
    deleted_at TIMESTAMP WITH TIME ZONE,
    id VARCHAR(36) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(job_profile_id) REFERENCES job_profiles (id),
    FOREIGN KEY(user_id) REFERENCES users (id)
);

CREATE INDEX ix_interviews_status ON interviews (status);

CREATE INDEX ix_interviews_user_created ON interviews (user_id, created_at);

CREATE TABLE feedback (
    user_id VARCHAR(36) NOT NULL,
    interview_id VARCHAR(36),
    category VARCHAR(30) NOT NULL,
    content_encrypted TEXT NOT NULL,
    status VARCHAR(30) NOT NULL,
    reply_encrypted TEXT,
    id VARCHAR(36) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(interview_id) REFERENCES interviews (id),
    FOREIGN KEY(user_id) REFERENCES users (id)
);

CREATE INDEX ix_feedback_user_id ON feedback (user_id);

CREATE TABLE interview_plans (
    interview_id VARCHAR(36) NOT NULL,
    plan_encrypted TEXT NOT NULL,
    prompt_version VARCHAR(30) NOT NULL,
    model_name VARCHAR(100) NOT NULL,
    id VARCHAR(36) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(interview_id) REFERENCES interviews (id),
    UNIQUE (interview_id)
);

CREATE TABLE interview_reports (
    interview_id VARCHAR(36) NOT NULL,
    report_version INTEGER NOT NULL,
    overall_score JSON,
    content_encrypted TEXT NOT NULL,
    prompt_version VARCHAR(30) NOT NULL,
    model_name VARCHAR(100) NOT NULL,
    id VARCHAR(36) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(interview_id) REFERENCES interviews (id),
    UNIQUE (interview_id, report_version)
);

CREATE INDEX ix_interview_reports_interview_id ON interview_reports (interview_id);

CREATE TABLE interview_turns (
    interview_id VARCHAR(36) NOT NULL,
    turn_no INTEGER NOT NULL,
    parent_turn_id VARCHAR(36),
    turn_type VARCHAR(24) NOT NULL,
    main_index INTEGER NOT NULL,
    competency VARCHAR(120) NOT NULL,
    question_encrypted TEXT NOT NULL,
    question_asset_id VARCHAR(36),
    answer_asset_id VARCHAR(36),
    transcript_encrypted TEXT,
    confidence JSON,
    decision_encrypted TEXT,
    status VARCHAR(24) NOT NULL,
    task_id VARCHAR(36),
    id VARCHAR(36) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(interview_id) REFERENCES interviews (id),
    FOREIGN KEY(parent_turn_id) REFERENCES interview_turns (id),
    UNIQUE (interview_id, turn_no)
);

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
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    deleted_at TIMESTAMP WITH TIME ZONE,
    id VARCHAR(36) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(interview_id) REFERENCES interviews (id),
    FOREIGN KEY(user_id) REFERENCES users (id),
    UNIQUE (object_key)
);

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
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(interview_id) REFERENCES interviews (id),
    FOREIGN KEY(user_id) REFERENCES users (id),
    UNIQUE (idempotency_key)
);

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
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(interview_id) REFERENCES interviews (id),
    FOREIGN KEY(turn_id) REFERENCES interview_turns (id),
    FOREIGN KEY(user_id) REFERENCES users (id)
);

CREATE INDEX ix_ai_call_logs_interview_id ON ai_call_logs (interview_id);

CREATE INDEX ix_ai_call_logs_user_id ON ai_call_logs (user_id);

CREATE TABLE resume_assets (
    user_id VARCHAR(36) NOT NULL,
    asset_id VARCHAR(36) NOT NULL,
    parse_status VARCHAR(24) NOT NULL,
    text_encrypted TEXT,
    summary_encrypted TEXT,
    task_id VARCHAR(36),
    id VARCHAR(36) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(asset_id) REFERENCES media_assets (id),
    FOREIGN KEY(user_id) REFERENCES users (id),
    UNIQUE (asset_id)
);

CREATE INDEX ix_resume_assets_user_id ON resume_assets (user_id);

ALTER TABLE interviews ADD CONSTRAINT fk_interview_resume FOREIGN KEY(resume_asset_id) REFERENCES resume_assets (id);

ALTER TABLE interview_turns ADD CONSTRAINT fk_turn_answer FOREIGN KEY(answer_asset_id) REFERENCES media_assets (id);

ALTER TABLE interview_turns ADD CONSTRAINT fk_turn_tts FOREIGN KEY(question_asset_id) REFERENCES media_assets (id);

ALTER TABLE media_assets ADD CONSTRAINT fk_media_turn FOREIGN KEY(turn_id) REFERENCES interview_turns (id);

INSERT INTO alembic_version (version_num) VALUES ('867baba5ca32') RETURNING alembic_version.version_num;

COMMIT;
