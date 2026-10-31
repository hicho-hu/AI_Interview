from pathlib import Path
from typing import Literal, Optional

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    environment: Literal["development", "test", "production"] = "development"
    database_url: str = "sqlite:///./var/interview.db"
    redis_url: Optional[str] = None
    task_mode: Literal["eager", "worker"] = "eager"
    storage_backend: Literal["local", "s3"] = "local"
    storage_path: Path = Path("var/objects")
    public_base_url: str = "http://localhost:8000"
    secret_key: str = "development-only-change-this-secret-32-chars"
    encryption_key: Optional[str] = None
    access_token_minutes: int = Field(120, ge=1)
    upload_url_seconds: int = Field(600, ge=30, le=3600)
    media_retention_days: int = Field(7, ge=1)
    initial_credits: int = Field(3, ge=0)
    policy_version: str = "1.0"
    provider_mode: Literal["mock", "http", "compatible"] = "mock"
    provider_url: str = ""
    provider_api_key: str = ""
    provider_model: str = "configured-model"
    asr_model: str = "whisper-1"
    tts_model: str = "tts-1"
    tts_voice: str = "alloy"
    compatible_json_schema: bool = True
    llm_input_cost_per_million: int = Field(0, ge=0)
    llm_output_cost_per_million: int = Field(0, ge=0)
    asr_cost_per_minute: int = Field(0, ge=0)
    tts_cost_per_thousand_chars: int = Field(0, ge=0)
    provider_timeout_seconds: float = 30
    provider_retry_count: int = Field(2, ge=0, le=3)
    provider_cost_limit_microunits: int = Field(2_000_000, ge=1)
    global_daily_cost_limit_microunits: int = Field(100_000_000, ge=1)
    wechat_app_id: str = ""
    wechat_app_secret: str = ""
    s3_endpoint_url: Optional[str] = None
    s3_public_endpoint_url: Optional[str] = None
    s3_bucket: str = "ai-interview"
    s3_region: str = "us-east-1"
    s3_access_key: str = ""
    s3_secret_key: str = ""
    admin_user_ids: str = ""
    rate_limit_per_minute: int = Field(120, ge=1)
    cors_origins: str = "http://localhost:3000,http://localhost:5173"
    max_upload_bytes: int = 32 * 1024 * 1024
    task_lease_seconds: int = 900

    @model_validator(mode="after")
    def production_guards(self):
        if self.environment == "production":
            required = (
                self.database_url.startswith("postgresql"),
                self.redis_url,
                self.storage_backend == "s3",
                self.task_mode == "worker",
                self.provider_mode in ("http", "compatible"),
                self.provider_url.startswith("https://"),
                self.provider_api_key,
                self.encryption_key,
                len(self.secret_key) >= 32 and not self.secret_key.startswith("development"),
                self.wechat_app_id,
                self.wechat_app_secret,
                self.public_base_url.startswith("https://"),
            )
            if not all(required):
                raise ValueError(
                    "生产环境必须配置 PostgreSQL/Redis/S3/Worker/真实 Provider/微信/HTTPS/独立密钥"
                )
        return self
