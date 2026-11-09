"""DashScope WebSocket speech synthesis, adapted to the application's TTS contract."""

import base64
import io
import wave
from threading import Event, Lock
from typing import Literal

import dashscope
from dashscope.audio.tts_v2 import AudioFormat, ResultCallback, SpeechSynthesizer
from dashscope.common.error import DashScopeException, RequestFailure
from pydantic import BaseModel, Field, ValidationError
from websocket import WebSocketException

from app.core.errors import BusinessError, require
from app.providers.live import audio_turn, live_sink

_sdk_lock = Lock()


class _Usage(BaseModel):
    input_tokens: int = Field(ge=0, le=10**12, strict=True)
    output_tokens: int = Field(ge=0, le=10**12, strict=True)


class _Header(BaseModel):
    event: Literal["task-finished"]


class _Payload(BaseModel):
    usage: _Usage


class _Response(BaseModel):
    header: _Header
    payload: _Payload


class DashScopeTTS:
    def __init__(self, settings):
        self.settings = settings

    def synthesize(self, text):
        s = self.settings
        synthesizer = None
        sink, turn_id = live_sink.get(), audio_turn.get()
        done = Event()
        chunks: list[bytes] = []
        failures: list[str] = []

        class Callback(ResultCallback):
            def on_data(self, data):
                if sum(map(len, chunks)) + len(data) > 10 * 1024 * 1024:
                    failures.append("too_large")
                    done.set()
                    return
                chunks.append(data)
                if sink:
                    sink(
                        {
                            "type": "audio",
                            "turn_id": turn_id,
                            "sample_rate": 24000,
                            "pcm": base64.b64encode(data).decode(),
                        }
                    )

            def on_complete(self):
                done.set()

            def on_error(self, message):
                failures.append("provider_error")
                done.set()

        if sink:
            sink({"type": "audio_reset", "turn_id": turn_id})
        try:
            # The v1 SDK copies the process API key during construction. Serialize
            # that short step and restore it before starting any network request.
            with _sdk_lock:
                previous_key = dashscope.api_key
                try:
                    dashscope.api_key = s.provider_api_key
                    synthesizer = SpeechSynthesizer(
                        model=s.tts_model,
                        voice=s.tts_voice,
                        url=s.dashscope_websocket_url,
                        format=AudioFormat.PCM_24000HZ_MONO_16BIT
                        if sink
                        else AudioFormat.MP3_22050HZ_MONO_256KBPS,
                        **({"callback": Callback()} if sink else {}),
                    )
                finally:
                    dashscope.api_key = previous_key
            audio = synthesizer.call(text, timeout_millis=max(1, int(s.provider_timeout_seconds * 1000)))
            if sink:
                if not done.wait(s.provider_timeout_seconds):
                    raise TimeoutError("streaming synthesis timed out")
                require(not failures, "AI_PROVIDER_BUSY", "流式语音合成中断", 503)
                output = io.BytesIO()
                with wave.open(output, "wb") as wav:
                    wav.setnchannels(1)
                    wav.setsampwidth(2)
                    wav.setframerate(24000)
                    wav.writeframes(b"".join(chunks))
                audio = output.getvalue()
            response = synthesizer.get_response() or {}
        except RequestFailure as exc:
            transient = any(
                word in (exc.name or "").lower() for word in ("throttl", "timeout", "internal", "unavailable")
            )
            if transient:
                raise BusinessError("AI_PROVIDER_BUSY", "语音合成服务繁忙", 503) from exc
            raise BusinessError(
                "AI_PROVIDER_REJECTED", "语音合成请求被拒绝，请检查模型/音色/密钥配置", 502
            ) from exc
        except (TimeoutError, OSError, WebSocketException) as exc:
            raise BusinessError("AI_PROVIDER_BUSY", "语音合成连接失败或超时", 503) from exc
        except DashScopeException as exc:
            raise BusinessError("AI_PROVIDER_REJECTED", "语音合成配置无效", 502) from exc
        finally:
            if synthesizer is not None:
                try:
                    synthesizer.close()
                except (OSError, WebSocketException):
                    pass
        require(
            isinstance(audio, bytes) and 0 < len(audio) <= 10 * 1024 * 1024,
            "AI_OUTPUT_INVALID",
            "语音合成未返回有效音频",
            502,
        )
        try:
            usage = _Response.model_validate(response).payload.usage
        except ValidationError as exc:
            raise BusinessError("AI_OUTPUT_INVALID", "语音合成任务状态或用量字段无效", 502) from exc
        input_tokens, output_tokens = usage.input_tokens, usage.output_tokens
        return {
            "data": {
                "audio_base64": base64.b64encode(audio).decode(),
                "mime_type": "audio/wav" if sink else "audio/mpeg",
            },
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cost_microunits": round(
                    (
                        input_tokens * s.tts_input_cost_per_million
                        + output_tokens * s.tts_output_cost_per_million
                    )
                    / 1000000
                ),
                "units": {
                    "tts_characters": len(text),
                    "cost_source": "configured_rate",
                    "tts_provider": "dashscope",
                },
            },
        }
