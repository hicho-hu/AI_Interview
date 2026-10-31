import base64
import json

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from app.core.config import Settings
from app.core.errors import BusinessError
from app.db.models import AICallLog
from app.providers.ai import MockProvider
from app.providers.compatible import CompatibleProvider
from tests.conftest import data, interview, login
from tests.test_billing_tasks import exhaust
from tests.test_interview import ANSWER
from tests.test_media_identity import upload, wav_bytes


def settings(**kwargs):
    return Settings(
        _env_file=None,
        provider_mode="compatible",
        provider_url="https://example.invalid/v1",
        provider_api_key="synthetic-key",
        asr_provider="qwen",
        asr_model="qwen3-asr-flash",
        asr_cost_per_minute=600,
        **kwargs,
    )


def completion(text=ANSWER):
    return {
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "role": "assistant",
                    "content": text,
                    "annotations": [{"type": "audio_info", "language": "zh", "emotion": "neutral"}],
                },
            }
        ],
        "usage": {"prompt_tokens": 25, "completion_tokens": 10, "seconds": 1},
    }


@pytest.mark.parametrize("language", ["zh-CN", "en-US"])
def test_qwen_asr_audio_chat_protocol_and_usage(client, monkeypatch, language):
    rt = client.app.state.runtime
    rt.storage.put("test/synthetic.wav", wav_bytes(), "audio/wav")

    def post(url, **kwargs):
        assert url == "https://example.invalid/v1/chat/completions"
        assert kwargs["headers"]["Authorization"] == "Bearer synthetic-key"
        assert kwargs["follow_redirects"] is False
        body = kwargs["json"]
        assert body["model"] == "qwen3-asr-flash"
        assert body["stream"] is False
        assert body["asr_options"] == {"language": language.split("-")[0], "enable_itn": False}
        assert "response_format" not in body
        audio = body["messages"][0]["content"][0]
        assert audio["type"] == "input_audio"
        prefix, encoded = audio["input_audio"]["data"].split(",", 1)
        assert prefix == "data:audio/wav;base64"
        assert base64.b64decode(encoded) == wav_bytes()
        return httpx.Response(200, json=completion())

    monkeypatch.setattr(httpx, "post", post)
    result = CompatibleProvider(settings(), rt.storage).invoke(
        "asr",
        {
            "object_key": "test/synthetic.wav",
            "mime_type": "audio/wav",
            "duration_ms": 1000,
            "language": language,
        },
        {},
    )
    assert result["data"] == {"text": ANSWER, "confidence": None, "duration_ms": 1000, "language": language}
    assert result["usage"]["input_tokens"] == 25
    assert result["usage"]["output_tokens"] == 10
    assert result["usage"]["cost_microunits"] == 10
    assert "emotion" not in json.dumps(result)


@pytest.mark.parametrize(
    "invalid",
    [
        {"choices": [], "usage": {"prompt_tokens": 25, "completion_tokens": 10}},
        {
            "choices": [{"finish_reason": "length", "message": {"role": "assistant", "content": "partial"}}],
            "usage": {"prompt_tokens": 25, "completion_tokens": 10},
        },
        {
            "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": None}}],
            "usage": {"prompt_tokens": 25, "completion_tokens": 10},
        },
        {**completion(), "usage": {"prompt_tokens": -1, "completion_tokens": 10}},
        {**completion(), "usage": {"prompt_tokens": True, "completion_tokens": 10}},
        {**completion(), "usage": None},
    ],
)
def test_qwen_asr_rejects_partial_or_invalid_responses(client, monkeypatch, invalid):
    rt = client.app.state.runtime
    rt.storage.put("test/synthetic.wav", wav_bytes(), "audio/wav")
    monkeypatch.setattr(httpx, "post", lambda *a, **k: httpx.Response(200, json=invalid))
    with pytest.raises(BusinessError) as error:
        CompatibleProvider(settings(), rt.storage).invoke(
            "asr",
            {
                "object_key": "test/synthetic.wav",
                "mime_type": "audio/wav",
                "duration_ms": 1000,
                "language": "zh-CN",
            },
            {},
        )
    assert error.value.code == "AI_OUTPUT_INVALID"


@pytest.mark.parametrize("model", ["whisper-1", "qwen3-asr-flash-filetrans"])
def test_qwen_asr_configuration_rejects_incompatible_model(model):
    with pytest.raises(ValidationError, match="同步 ASR 模型"):
        Settings(
            _env_file=None,
            provider_mode="compatible",
            provider_url="https://example.invalid/v1",
            provider_api_key="synthetic-key",
            asr_provider="qwen",
            asr_model=model,
        )


def test_failed_transcription_retries_existing_answer_with_qwen(client, monkeypatch):
    headers, uid = login(client)
    row = interview(client, headers, config={"mode": "VOICE", "question_count": 1})
    path = "/api/v1/interviews/" + row["id"]
    data(client.post(path + "/start", headers=headers))
    requested = upload(
        client,
        headers,
        wav_bytes(),
        {
            "filename": "answer.wav",
            "content_type": "audio/wav",
            "media_type": "AUDIO",
            "duration_ms": 1000,
            "interview_id": row["id"],
            "turn_id": row["current_question"]["id"],
        },
    )
    rt = client.app.state.runtime
    provider = CompatibleProvider(settings(), rt.storage)
    # Reproduce the previously configured Whisper route against a Qwen endpoint.
    provider.settings.asr_provider, provider.settings.asr_model = "compatible", "whisper-1"

    class AnswerProvider(MockProvider):
        def invoke(self, operation, payload, schema):
            if operation == "asr":
                return provider.invoke(operation, payload, schema)
            return super().invoke(operation, payload, schema)

    rt.provider = AnswerProvider()
    calls = []

    def post(url, **kwargs):
        calls.append(url)
        return (
            httpx.Response(404, json={"error": "not found"})
            if url.endswith("/audio/transcriptions")
            else (httpx.Response(200, json=completion()))
        )

    monkeypatch.setattr(httpx, "post", post)
    submit_path = path + "/turns/" + row["current_question"]["id"] + "/submit"
    submit_headers = {**headers, "Idempotency-Key": "saved-audio"}
    task = data(client.post(submit_path, headers=submit_headers, json={"asset_id": requested["asset_id"]}))
    exhaust(rt, task["task_id"])
    task_path = "/api/v1/tasks/" + task["task_id"]
    assert data(client.get(task_path, headers=headers))["error_code"] == "AI_PROVIDER_REJECTED"

    provider.settings.asr_provider, provider.settings.asr_model = "qwen", "qwen3-asr-flash"
    rt.settings.provider_mode, rt.settings.asr_model = "compatible", "qwen3-asr-flash"
    data(client.post(task_path + "/retry", headers=headers))
    assert data(client.get(task_path, headers=headers))["status"] == "SUCCEEDED"
    assert data(client.get(path + "/turns", headers=headers))["items"][0]["transcript"] == ANSWER
    assert data(client.get(path + "/report", headers=headers))["status"] == "COMPLETED"
    assert client.post(task_path + "/retry", headers=headers).status_code == 409
    repeated = data(
        client.post(submit_path, headers=submit_headers, json={"asset_id": requested["asset_id"]})
    )
    assert repeated["task_id"] == task["task_id"]
    assert calls.count("https://example.invalid/v1/chat/completions") == 1
    with rt.db.session() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(AICallLog)
                .where(
                    AICallLog.user_id == uid,
                    AICallLog.call_type == "asr",
                    AICallLog.status == "SUCCEEDED",
                    AICallLog.model == "qwen3-asr-flash",
                )
            )
            == 1
        )
