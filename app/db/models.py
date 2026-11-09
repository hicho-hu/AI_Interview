from sqlalchemy import (
    JSON,
    BigInteger,
    Column,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import declarative_base

from app.core.security import new_id, utcnow
from app.db.schema_docs_v1 import attach_schema_documentation
from app.db.types import MYSQL_TABLE_OPTIONS, LongText, UTCDateTime

Base = declarative_base()
Json = JSON().with_variant(JSONB(), "postgresql")


class Entity:
    __table_args__ = MYSQL_TABLE_OPTIONS
    id = Column(String(36), primary_key=True, default=new_id)
    created_at = Column(UTCDateTime(), default=utcnow, nullable=False)
    updated_at = Column(UTCDateTime(), default=utcnow, onupdate=utcnow, nullable=False)


class User(Entity, Base):
    __tablename__ = "users"
    openid_hash = Column(String(64), unique=True, nullable=False)
    nickname = Column(String(80), default="求职者", nullable=False)
    status = Column(String(24), default="ACTIVE", nullable=False)
    credits = Column(Integer, default=0, nullable=False)


class Consent(Entity, Base):
    __tablename__ = "user_consents"
    user_id = Column(String(36), ForeignKey("users.id"), index=True, nullable=False)
    consent_type = Column(String(30), nullable=False)
    policy_version = Column(String(30), nullable=False)
    revoked_at = Column(UTCDateTime())


class RevokedToken(Entity, Base):
    __tablename__ = "revoked_tokens"
    jti = Column(String(36), unique=True, nullable=False)
    expires_at = Column(UTCDateTime(), index=True, nullable=False)


class JobProfile(Entity, Base):
    __tablename__ = "job_profiles"
    user_id = Column(String(36), ForeignKey("users.id"), index=True, nullable=False)
    job_title = Column(String(120), nullable=False)
    seniority = Column(String(30), nullable=False)
    jd_encrypted = Column(LongText, nullable=False)
    profile_encrypted = Column(LongText)
    status = Column(String(24), default="PREPARING", nullable=False)
    task_id = Column(String(36))


class MediaAsset(Entity, Base):
    __tablename__ = "media_assets"
    user_id = Column(String(36), ForeignKey("users.id"), index=True, nullable=False)
    interview_id = Column(String(36), ForeignKey("interviews.id"), index=True)
    turn_id = Column(String(36), ForeignKey("interview_turns.id", use_alter=True, name="fk_media_turn"))
    media_type = Column(String(24), nullable=False)
    object_key = Column(String(500), unique=True, nullable=False)
    filename = Column(String(200), nullable=False)
    mime_type = Column(String(120), nullable=False)
    size_bytes = Column(BigInteger, nullable=False)
    duration_ms = Column(Integer)
    checksum = Column(String(64))
    etag = Column(String(128))
    status = Column(String(24), default="PENDING_UPLOAD", nullable=False)
    expires_at = Column(UTCDateTime(), index=True, nullable=False)
    deleted_at = Column(UTCDateTime())


class ResumeAsset(Entity, Base):
    __tablename__ = "resume_assets"
    user_id = Column(String(36), ForeignKey("users.id"), index=True, nullable=False)
    asset_id = Column(String(36), ForeignKey("media_assets.id"), unique=True, nullable=False)
    parse_status = Column(String(24), default="PENDING_UPLOAD", nullable=False)
    text_encrypted = Column(LongText)
    summary_encrypted = Column(LongText)
    task_id = Column(String(36))


class Interview(Entity, Base):
    __tablename__ = "interviews"
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    job_profile_id = Column(String(36), ForeignKey("job_profiles.id"), nullable=False)
    resume_asset_id = Column(
        String(36), ForeignKey("resume_assets.id", use_alter=True, name="fk_interview_resume")
    )
    status = Column(String(24), default="PREPARING", nullable=False, index=True)
    mode = Column(String(16), nullable=False)
    config_json = Column(Json, nullable=False)
    current_turn_no = Column(Integer, default=0, nullable=False)
    version = Column(Integer, default=1, nullable=False)
    started_at = Column(UTCDateTime())
    ended_at = Column(UTCDateTime())
    paused_at = Column(UTCDateTime())
    paused_seconds = Column(Integer, default=0, nullable=False)
    prepare_task_id = Column(String(36))
    report_task_id = Column(String(36))
    failure_code = Column(String(80))
    deleted_at = Column(UTCDateTime())
    __mapper_args__ = {"version_id_col": version}
    __table_args__ = (Index("ix_interviews_user_created", "user_id", "created_at"), MYSQL_TABLE_OPTIONS)


class InterviewPlan(Entity, Base):
    __tablename__ = "interview_plans"
    interview_id = Column(String(36), ForeignKey("interviews.id"), unique=True, nullable=False)
    plan_encrypted = Column(LongText, nullable=False)
    prompt_version = Column(String(30), default="1.0", nullable=False)
    model_name = Column(String(100), nullable=False)


class Turn(Entity, Base):
    __tablename__ = "interview_turns"
    interview_id = Column(String(36), ForeignKey("interviews.id"), index=True, nullable=False)
    turn_no = Column(Integer, nullable=False)
    parent_turn_id = Column(String(36), ForeignKey("interview_turns.id"))
    turn_type = Column(String(24), nullable=False)
    main_index = Column(Integer, nullable=False)
    competency = Column(String(120), nullable=False)
    question_encrypted = Column(LongText, nullable=False)
    question_asset_id = Column(String(36), ForeignKey("media_assets.id", use_alter=True, name="fk_turn_tts"))
    answer_asset_id = Column(String(36), ForeignKey("media_assets.id", use_alter=True, name="fk_turn_answer"))
    transcript_encrypted = Column(LongText)
    confidence = Column(JSON)
    decision_encrypted = Column(LongText)
    status = Column(String(24), default="WAITING_ANSWER", nullable=False)
    task_id = Column(String(36))
    __table_args__ = (UniqueConstraint("interview_id", "turn_no"), MYSQL_TABLE_OPTIONS)


class Report(Entity, Base):
    __tablename__ = "interview_reports"
    interview_id = Column(String(36), ForeignKey("interviews.id"), index=True, nullable=False)
    report_version = Column(Integer, nullable=False)
    overall_score = Column(JSON)
    content_encrypted = Column(LongText, nullable=False)
    prompt_version = Column(String(30), default="1.0", nullable=False)
    model_name = Column(String(100), nullable=False)
    __table_args__ = (UniqueConstraint("interview_id", "report_version"), MYSQL_TABLE_OPTIONS)


class AICallLog(Entity, Base):
    __tablename__ = "ai_call_logs"
    user_id = Column(String(36), ForeignKey("users.id"), index=True, nullable=False)
    interview_id = Column(String(36), ForeignKey("interviews.id"), index=True)
    turn_id = Column(String(36), ForeignKey("interview_turns.id"))
    provider = Column(String(40), nullable=False)
    model = Column(String(100), nullable=False)
    call_type = Column(String(30), nullable=False)
    input_tokens = Column(Integer, default=0, nullable=False)
    output_tokens = Column(Integer, default=0, nullable=False)
    latency_ms = Column(Integer, nullable=False)
    cost_microunits = Column(BigInteger, default=0, nullable=False)
    units = Column(JSON)
    status = Column(String(24), nullable=False)
    request_id = Column(String(80))


class Usage(Entity, Base):
    __tablename__ = "usage_ledger"
    user_id = Column(String(36), ForeignKey("users.id"), index=True, nullable=False)
    interview_id = Column(String(36), ForeignKey("interviews.id"), index=True)
    usage_type = Column(String(30), nullable=False)
    quantity = Column(Integer, nullable=False)
    amount_minor = Column(BigInteger, default=0, nullable=False)
    status = Column(String(24), nullable=False)
    idempotency_key = Column(String(200), unique=True, nullable=False)


class Order(Entity, Base):
    __tablename__ = "orders"
    user_id = Column(String(36), ForeignKey("users.id"), index=True, nullable=False)
    product_code = Column(String(40), nullable=False)
    amount_minor = Column(Integer, nullable=False)
    credits = Column(Integer, nullable=False)
    currency = Column(String(3), default="CNY", nullable=False)
    status = Column(String(24), default="PENDING", nullable=False)
    provider_trade_no = Column(String(120), unique=True)
    paid_at = Column(UTCDateTime())
    refunded_at = Column(UTCDateTime())


class Task(Entity, Base):
    __tablename__ = "outbox_events"
    user_id = Column(String(36), ForeignKey("users.id"), index=True, nullable=False)
    event_type = Column(String(40), nullable=False)
    aggregate_id = Column(String(36), nullable=False)
    payload_encrypted = Column(LongText)
    result_encrypted = Column(LongText)
    status = Column(String(24), default="PENDING", nullable=False)
    retry_count = Column(Integer, default=0, nullable=False)
    available_at = Column(UTCDateTime(), default=utcnow, nullable=False)
    lease_until = Column(UTCDateTime())
    lease_token = Column(String(36))
    error_code = Column(String(80))
    request_id = Column(String(80))
    __table_args__ = (Index("ix_outbox_dispatch", "status", "available_at"), MYSQL_TABLE_OPTIONS)


class Idempotency(Entity, Base):
    __tablename__ = "idempotency_records"
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    scope = Column(String(200), nullable=False)
    key = Column(String(120), nullable=False)
    request_hash = Column(String(64), nullable=False)
    response_encrypted = Column(LongText, nullable=False)
    __table_args__ = (UniqueConstraint("user_id", "scope", "key"), MYSQL_TABLE_OPTIONS)


class AuditLog(Entity, Base):
    __tablename__ = "audit_logs"
    user_id = Column(String(36), ForeignKey("users.id"), index=True, nullable=False)
    action = Column(String(60), nullable=False)
    resource_id = Column(String(120), nullable=False)
    request_id = Column(String(80))


class Feedback(Entity, Base):
    __tablename__ = "feedback"
    user_id = Column(String(36), ForeignKey("users.id"), index=True, nullable=False)
    interview_id = Column(String(36), ForeignKey("interviews.id"))
    category = Column(String(30), nullable=False)
    content_encrypted = Column(LongText, nullable=False)
    status = Column(String(30), default="OPEN", nullable=False)
    reply_encrypted = Column(LongText)


attach_schema_documentation(Base.metadata)
Base.metadata.tables[
    "users"
].c.status.comment = "账号状态：ACTIVE=正常，FROZEN=冻结，DELETING=删除中，DELETED=已删除"
Base.metadata.tables[
    "user_consents"
].c.consent_type.comment = (
    "授权类型：PRIVACY/TERMS/AI_PROCESSING/MICROPHONE/CAMERA/SCREEN_CAPTURE；SUPPORT_REVIEW=后台人工审阅授权"
)

# Register the new schema after the historical business COMMENT snapshot was applied.
from app.db import (  # noqa: E402, F401
    admin_models,
    entitlement_models,
    growth_models,
)
