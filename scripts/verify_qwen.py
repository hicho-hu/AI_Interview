"""Verify configured Qwen endpoints using synthetic text; never prints credentials or model content."""

import json
import logging
import re
import sys

import httpx
from pydantic import ValidationError
from websocket import WebSocketBadStatusException, WebSocketException, create_connection

from app.core.config import Settings
from app.core.errors import BusinessError
from app.modules.media.service import inspect_media
from app.providers.ai import backoff, decode_audio
from app.providers.compatible import CompatibleProvider
from app.schemas.contracts import JobStructure, SynthesizedAudio, Transcript


def verify_asr(settings):
    """Round-trip a fixed synthetic phrase through TTS and ASR, without reading user media."""
    if settings.asr_provider != "qwen" or settings.tts_provider != "dashscope":
        print("请先配置 ASR_PROVIDER=qwen、ASR_MODEL=qwen3-asr-flash 和 DashScope TTS。", flush=True)
        return 1
    try:
        phrase = "这是一条语音识别测试。"
        provider = CompatibleProvider(settings, None)
        synthesized = provider.invoke("tts", {"text": phrase}, SynthesizedAudio.model_json_schema())
        audio = decode_audio(SynthesizedAudio.model_validate(synthesized["data"]).audio_base64)
        duration = inspect_media(audio, "audio/mpeg", "AUDIO", ffprobe_bin=settings.ffprobe_bin)

        class SyntheticStorage:
            def read(self, key):
                return audio

        provider = CompatibleProvider(settings, SyntheticStorage())
        output = provider.invoke(
            "asr",
            {
                "object_key": "synthetic-asr-test",
                "mime_type": "audio/mpeg",
                "duration_ms": duration,
                "language": "zh-CN",
            },
            Transcript.model_json_schema(),
        )
        transcript = Transcript.model_validate(output["data"])
        recognized = "语音识别测试" in re.sub(r"\W", "", transcript.text)
        print(
            json.dumps(
                {
                    "operation": "asr",
                    "status": "ok" if recognized else "unexpected_transcript",
                    "model": settings.asr_model,
                    "duration_ms": duration,
                    "expected_phrase_recognized": recognized,
                    "usage": output["usage"],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        return 0 if recognized else 1
    except BusinessError as exc:
        print(json.dumps({"operation": "asr", "status": "failed", "code": exc.code}), flush=True)
    except (ValueError, TypeError, KeyError):
        print(json.dumps({"operation": "asr", "status": "failed", "code": "AI_OUTPUT_INVALID"}), flush=True)
    return 1


def diagnose(settings):
    try:
        with httpx.stream(
            "POST",
            settings.provider_url.rstrip("/") + "/chat/completions",
            headers={"Authorization": f"Bearer {settings.provider_api_key}"},
            json={
                "model": settings.provider_model,
                "messages": [{"role": "user", "content": "合成连接测试，请回复测试成功。"}],
                "modalities": ["text"],
                "stream": True,
                "stream_options": {"include_usage": True},
            },
            timeout=settings.provider_timeout_seconds,
            follow_redirects=False,
        ) as response:
            if not response.is_success:
                response.read()
    except httpx.HTTPError:
        print(json.dumps({"operation": "diagnose", "status": "failed", "code": "AI_PROVIDER_BUSY"}))
        return 1
    summary = {
        "operation": "diagnose",
        "endpoint": settings.provider_url,
        "http_status": response.status_code,
    }
    if not response.is_success:
        try:
            body = response.json()
            error = body.get("error") or body
            code = error.get("code")
            if isinstance(code, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", code):
                summary["provider_error_code"] = code
            message = error.get("message")
            if isinstance(message, str):
                message = message.replace(settings.provider_api_key, "[redacted]")
                summary["message"] = re.sub(r"sk-[A-Za-z0-9_.-]+", "[redacted]", message)[:300]
        except (ValueError, TypeError, AttributeError):
            pass
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return 0 if response.is_success else 1


def diagnose_websocket(settings):
    try:
        connection = create_connection(
            settings.dashscope_websocket_url,
            header={"Authorization": f"Bearer {settings.provider_api_key}"},
            timeout=10,
        )
        connection.close()
        summary = {"operation": "websocket_handshake", "status": "ok"}
    except WebSocketBadStatusException as exc:
        summary = {"operation": "websocket_handshake", "http_status": exc.status_code}
        try:
            error = json.loads(exc.resp_body)
            error = error.get("error") or error
            for name in ("code", "message"):
                value = error.get(name)
                if isinstance(value, str):
                    value = value.replace(settings.provider_api_key, "[redacted]")
                    summary[name] = re.sub(r"sk-[A-Za-z0-9_.-]+", "[redacted]", value)[:300]
        except (ValueError, TypeError, AttributeError):
            pass
    except (OSError, WebSocketException):
        summary = {"operation": "websocket_handshake", "status": "connection_failed"}
    summary["endpoint"] = settings.dashscope_websocket_url
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return 0 if summary.get("status") == "ok" else 1


def main():
    logging.getLogger("dashscope").setLevel(logging.CRITICAL)
    try:
        settings = Settings()
    except ValueError:
        print("Qwen 配置不完整或无效，请检查本地 .env。", flush=True)
        return 1
    if "--shared-endpoint" in sys.argv:
        settings = settings.model_copy(
            update={
                "provider_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "dashscope_websocket_url": "wss://dashscope.aliyuncs.com/api-ws/v1/inference",
            }
        )
    if "--diagnose" in sys.argv:
        return diagnose(settings)
    if "--diagnose-websocket" in sys.argv:
        return diagnose_websocket(settings)
    if "--asr" in sys.argv:
        return verify_asr(settings)
    if settings.provider_model != "qwen3.8-omni-flash" or settings.tts_provider != "dashscope":
        raise SystemExit("请先启用 Qwen Chat 与 DashScope TTS 配置")
    provider = CompatibleProvider(settings, None)
    failed = False
    for operation, payload, schema in (
        (
            "job",
            {"jd_text": "合成测试岗位：开发 Python API，设计 MySQL 数据库，编写自动化测试。"},
            JobStructure,
        ),
        ("tts", {"text": "这是一条语音合成连接测试。"}, SynthesizedAudio),
    ):
        for attempt in range(settings.provider_retry_count + 1):
            try:
                output = provider.invoke(operation, payload, schema.model_json_schema())
                result = schema.model_validate(output["data"])
                summary = {
                    "operation": operation,
                    "status": "ok",
                    "usage": output["usage"],
                    "attempt": attempt + 1,
                }
                if operation == "tts":
                    summary["audio_bytes"] = len(decode_audio(result.audio_base64))
                print(json.dumps(summary, ensure_ascii=False), flush=True)
                break
            except BusinessError as exc:
                summary = {
                    "operation": operation,
                    "status": "failed",
                    "code": exc.code,
                    "message": exc.message,
                }
                retryable = exc.code in ("AI_PROVIDER_BUSY", "AI_OUTPUT_INVALID")
                if exc.__cause__:
                    summary["error_type"] = type(exc.__cause__).__name__
            except (ValueError, TypeError, KeyError) as exc:
                summary = {
                    "operation": operation,
                    "status": "failed",
                    "code": "AI_OUTPUT_INVALID",
                    "error_type": type(exc).__name__,
                }
                retryable = True
                if isinstance(exc, ValidationError):
                    summary["validation_errors"] = [
                        {"type": error["type"], "location": error["loc"]}
                        for error in exc.errors(include_input=False, include_context=False, include_url=False)
                    ]
            will_retry = retryable and attempt < settings.provider_retry_count
            summary.update(attempt=attempt + 1, will_retry=will_retry)
            print(json.dumps(summary, ensure_ascii=False), flush=True)
            if not will_retry:
                failed = True
                break
            backoff(attempt)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
