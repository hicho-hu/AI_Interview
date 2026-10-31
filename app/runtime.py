import contextvars
import json
import logging
import time

from pydantic import ValidationError
from sqlalchemy import func, select

from app.core.cache import Cache
from app.core.config import Settings
from app.core.errors import BusinessError, require
from app.core.security import Security, utcnow
from app.db.models import AICallLog
from app.db.repository import Database
from app.providers.ai import HTTPProvider, MockProvider, backoff
from app.providers.compatible import CompatibleProvider
from app.providers.storage import LocalStorage, S3Storage

call_buffer = contextvars.ContextVar("ai_call_buffer", default=None)
checkpoint_buffer = contextvars.ContextVar("answer_checkpoints", default=None)
logger = logging.getLogger(__name__)


class Runtime:
    def __init__(self, settings=None):
        self.settings = settings or Settings()
        self.security = Security(self.settings)
        self.db = Database(self.settings)
        self.cache = Cache(self.settings)
        self.storage = (S3Storage if self.settings.storage_backend == "s3" else LocalStorage)(
            self.settings, self.security
        )
        self.provider = (
            MockProvider()
            if self.settings.provider_mode == "mock"
            else CompatibleProvider(self.settings, self.storage)
            if self.settings.provider_mode == "compatible"
            else HTTPProvider(self.settings)
        )
        self.model_name = "mock" if self.settings.provider_mode == "mock" else self.settings.provider_model

    def ai(
        self,
        session,
        user_id,
        operation,
        payload,
        schema=None,
        interview_id=None,
        turn_id=None,
        request_id=None,
        validate=None,
    ):
        daily = session.scalar(
            select(func.coalesce(func.sum(AICallLog.cost_microunits), 0)).where(
                AICallLog.created_at >= utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
            )
        )
        require(
            daily < self.settings.global_daily_cost_limit_microunits,
            "AI_COST_LIMIT",
            "今日 AI 成本预算已用完",
            403,
        )
        if interview_id:
            used = session.scalar(
                select(func.coalesce(func.sum(AICallLog.cost_microunits), 0)).where(
                    AICallLog.interview_id == interview_id
                )
            )
            pending = sum(
                x.cost_microunits for x in (call_buffer.get() or []) if x.interview_id == interview_id
            )
            require(
                used + pending < self.settings.provider_cost_limit_microunits,
                "AI_COST_LIMIT",
                "单场 AI 成本预算已用完",
                403,
            )
        # Report jobs already have durable retries in the outbox. Nesting a full
        # retry loop here can regenerate the same large report nine times.
        retry_count = 0 if operation == "report" else self.settings.provider_retry_count
        for attempt in range(retry_count + 1):
            require(
                not self.cache.get("provider:cooldown"),
                "AI_CIRCUIT_OPEN",
                "AI 供应商暂时熔断，请稍后重试",
                503,
            )
            require(
                self.cache.hit("provider:requests:minute", 1000), "AI_CIRCUIT_OPEN", "供应商请求频率过高", 503
            )
            started = time.monotonic()
            log = AICallLog(
                user_id=user_id,
                interview_id=interview_id,
                turn_id=turn_id,
                provider=self.settings.provider_mode,
                model=(
                    self.settings.asr_model
                    if operation == "asr"
                    else self.settings.tts_model
                    if operation == "tts"
                    else self.model_name
                )
                if self.settings.provider_mode == "compatible"
                else self.model_name,
                call_type=operation,
                latency_ms=0,
                status="FAILED",
                input_tokens=0,
                output_tokens=0,
                cost_microunits=0,
                request_id=request_id,
            )
            try:
                output = self.provider.invoke(
                    operation, payload, schema.model_json_schema() if schema else {}
                )
                usage = output.get("usage", {})
                for name in ("input_tokens", "output_tokens", "cost_microunits"):
                    value = usage.get(name, 0)
                    require(
                        isinstance(value, int) and 0 <= value <= 10**12,
                        "AI_OUTPUT_INVALID",
                        "AI 用量字段无效",
                        502,
                    )
                    setattr(log, name, value)
                log.units = usage.get("units", {})
                result = schema.model_validate(output["data"]).model_dump() if schema else output["data"]
                if validate:
                    validate(result)
                log.status = "SUCCEEDED"
                return result
            except (ValidationError, KeyError, TypeError, ValueError) as exc:
                error = BusinessError("AI_OUTPUT_INVALID", "AI 输出未通过结构校验", 502)
                error.__cause__ = exc
            except BusinessError as exc:
                error = exc
                if error.code == "AI_PROVIDER_BUSY" and not self.cache.hit("provider:failures", 5, 60):
                    self.cache.put("provider:cooldown", "1", 30)
            finally:
                log.latency_ms = int((time.monotonic() - started) * 1000)
                buffer = call_buffer.get()
                if buffer is not None:
                    buffer.append(log)
                else:
                    session.add(log)
            if error.code == "AI_OUTPUT_INVALID":
                details = {
                    "event": "ai_output_invalid",
                    "operation": operation,
                    "interview_id": interview_id,
                    "request_id": request_id,
                    "attempt": attempt + 1,
                    "reason": error.message,
                }
                if isinstance(error.__cause__, ValidationError):
                    # Only log schema-owned field names, never model input or
                    # arbitrary extra keys that may contain private content.
                    fields = set(schema.model_fields) if schema else set()
                    details["validation_errors"] = [
                        {
                            "type": item["type"],
                            "field": item["loc"][0]
                            if item["loc"] and item["loc"][0] in fields
                            else "unknown",
                        }
                        for item in error.__cause__.errors(
                            include_input=False, include_context=False, include_url=False
                        )
                    ]
                logger.warning(json.dumps(details, ensure_ascii=False))
            if error.code not in ("AI_OUTPUT_INVALID", "AI_PROVIDER_BUSY") or attempt >= retry_count:
                raise error
            backoff(attempt)

    def drain(self, limit=100):
        from app.workers.processor import process_available

        return process_available(self, limit)
