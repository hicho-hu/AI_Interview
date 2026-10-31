from pathlib import Path
from typing import Literal, Optional

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    environment: Literal["development", "test", "production"] = "development"
    database_url: str = "mysql+pymysql://interview:interview-local@127.0.0.1:3306/interview?charset=utf8mb4"
    redis_url: Optional[str] = "redis://127.0.0.1:6379/0"
    task_mode: Literal["eager", "worker"] = "worker"
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
    provider_api_key: str = Field("", validation_alias=AliasChoices("provider_api_key", "dashscope_api_key"))
    provider_model: str = "configured-model"
    asr_model: str = "whisper-1"
    asr_provider: Literal["compatible", "qwen"] = "compatible"
    realtime_asr_enabled: bool = True
    realtime_asr_model: str = "qwen3-asr-flash-realtime"
    realtime_asr_url: str = ""
    tts_model: str = "tts-1"
    tts_voice: str = "alloy"
    tts_provider: Literal["compatible", "dashscope"] = "compatible"
    dashscope_websocket_url: str = ""
    compatible_json_schema: bool = True
    llm_input_cost_per_million: int = Field(0, ge=0)
    llm_output_cost_per_million: int = Field(0, ge=0)
    asr_cost_per_minute: int = Field(0, ge=0)
    tts_cost_per_thousand_chars: int = Field(0, ge=0)
    tts_input_cost_per_million: int = Field(0, ge=0)
    tts_output_cost_per_million: int = Field(0, ge=0)
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
    cors_origins: str = (
        "http://localhost:3000,http://localhost:5173,http://localhost:5174,http://127.0.0.1:5174"
    )
    max_upload_bytes: int = 32 * 1024 * 1024
    ffmpeg_bin: str = "ffmpeg"
    ffprobe_bin: str = "ffprobe"
    task_lease_seconds: int = 900

    @model_validator(mode="after")
    def production_guards(self):
        if self.realtime_asr_url and not self.realtime_asr_url.startswith("wss://"):
            raise ValueError("实时转写必须使用 WSS 地址")
        if self.asr_provider == "qwen" and (
            self.provider_mode != "compatible"
            or not self.provider_url.startswith("https://")
            or not self.provider_api_key
            or not self.asr_model.startswith("qwen3-asr-flash")
            or "filetrans" in self.asr_model
        ):
            raise ValueError("Qwen ASR 必须配置 compatible Provider、HTTPS 地址、API Key 和同步 ASR 模型")
        if self.tts_provider == "dashscope" and (
            self.provider_mode != "compatible"
            or not self.dashscope_websocket_url.startswith("wss://")
            or not self.provider_api_key
        ):
            raise ValueError("DashScope TTS 必须配置 compatible Provider、WSS 地址和 API Key")
        if self.environment == "production":
            required = (
                self.database_url.startswith("mysql+pymysql://"),
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
                raise ValueError("生产环境必须配置 MySQL/Redis/S3/Worker/真实 Provider/微信/HTTPS/独立密钥")
        return self
