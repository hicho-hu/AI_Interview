import json
from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import func, select

from app.core.config import Settings
from app.core.errors import BusinessError
from app.core.security import utcnow
from app.db.models import AICallLog, MediaAsset, Report, Task, User
from app.modules.media.service import inspect_media
from app.providers.ai import HTTPProvider, MockProvider
from app.providers.compatible import CompatibleProvider
from app.schemas.contracts import JobStructure
from app.workers.processor import cleanup
from tests.conftest import data, interview, login
from tests.test_billing_tasks import exhaust
from tests.test_interview import ANSWER
from tests.test_media_identity import SpeechFixture, upload, wav_bytes


def test_compatible_chat_asr_tts_contracts(client, monkeypatch):
    rt = client.app.state.runtime
    settings = Settings(
        provider_mode="compatible",
        provider_url="https://example.invalid/v1",
        provider_api_key="test-key",
        llm_input_cost_per_million=1000000,
        llm_output_cost_per_million=2000000,
        asr_cost_per_minute=600,
        tts_cost_per_thousand_chars=1000,
    )
    provider = CompatibleProvider(settings, rt.storage)
    calls = []

    def post(url, **kwargs):
        calls.append((url, kwargs))
        if url.endswith("chat/completions"):
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": '{"result": "ok"}'}}],
                    "usage": {"prompt_tokens": 20, "completion_tokens": 5},
                },
            )
        if url.endswith("audio/transcriptions"):
            assert kwargs["files"]["file"][1] == wav_bytes()
            return httpx.Response(200, json={"text": ANSWER})
        return httpx.Response(200, content=b"ID3-speech")

    monkeypatch.setattr(httpx, "post", post)
    output = provider.invoke("job", {"jd_text": "data"}, JobStructure.model_json_schema())
    assert output["usage"]["cost_microunits"] == 30
    assert calls[0][1]["json"]["response_format"]["type"] == "json_schema"
    rt.storage.put("test/answer.wav", wav_bytes(), "audio/wav")
    asr = provider.invoke(
        "asr",
        {"object_key": "test/answer.wav", "mime_type": "audio/wav", "language": "zh-CN", "duration_ms": 1000},
        {},
    )
    assert asr["data"]["confidence"] is None  # never invent a provider confidence
    assert asr["usage"]["cost_microunits"] == 10
    assert provider.invoke("tts", {"text": "你好"}, {})["usage"]["cost_microunits"] == 2


def test_gateway_errors_are_sanitized(monkeypatch):
    provider = HTTPProvider(Settings(provider_url="https://example.invalid"))
    monkeypatch.setattr(httpx, "post", lambda *a, **k: httpx.Response(503, text="secret upstream response"))
    with pytest.raises(BusinessError, match="AI 服务繁忙"):
        provider.invoke("job", {}, {})
    monkeypatch.setattr(httpx, "post", lambda *a, **k: httpx.Response(200, json={"invalid": "data"}))
    with pytest.raises(BusinessError) as caught:
        provider.invoke("job", {}, {})
    assert caught.value.code == "AI_OUTPUT_INVALID"


def test_wechat_exchange_and_identity_hash(client, monkeypatch):
    rt = client.app.state.runtime
    rt.settings.wechat_app_id, rt.settings.wechat_app_secret = "appid", "secret"

    def get(url, **kwargs):
        assert kwargs["params"]["js_code"] == "valid-code"
        return httpx.Response(200, json={"openid": "private-openid", "session_key": "private-session"})

    monkeypatch.setattr(httpx, "get", get)
    result = data(client.post("/api/v1/auth/wechat-login", json={"code": "valid-code"}))
    assert "private-openid" not in json.dumps(result)
    with rt.db.session() as db:
        assert db.get(User, result["user"]["id"]).openid_hash != "private-openid"


class ForgedReport(MockProvider):
    def invoke(self, operation, payload, schema):
        result = super().invoke(operation, payload, schema)
        if operation == "report":
            result["data"]["overall_score"] = 100
            for dimension in result["data"]["dimensions"]:
                dimension.update(score=100, evidence=["用户从未说过的证据"])
        return result


def test_report_rejects_fabricated_evidence(client):
    headers, _ = login(client)
    row = interview(client, headers, config={"question_count": 1})
    path = "/api/v1/interviews/" + row["id"]
    data(client.post(path + "/start", headers=headers))
    client.app.state.runtime.provider = ForgedReport()
    data(
        client.post(
            path + "/turns/" + row["current_question"]["id"] + "/submit",
            headers={**headers, "Idempotency-Key": "answer"},
            json={"text": ANSWER},
        )
    )
    report_task = data(client.get(path, headers=headers))["report_task_id"]
    exhaust(client.app.state.runtime, report_task)
    assert data(client.get(path, headers=headers))["status"] == "REPORT_FAILED"
    with client.app.state.runtime.db.session() as db:
        assert db.scalar(select(func.count()).select_from(Report)) == 0
        assert db.get(Task, report_task).error_code == "AI_OUTPUT_INVALID"


class ASRThenFailure(SpeechFixture):
    def invoke(self, operation, payload, schema):
        if operation == "decision":
            raise BusinessError("AI_PROVIDER_BUSY", "failed decision", 503)
        return super().invoke(operation, payload, schema)


def test_asr_checkpoint_survives_llm_failure(client):
    headers, _ = login(client)
    rt = client.app.state.runtime
    rt.provider = ASRThenFailure()
    row = interview(client, headers, config={"mode": "VOICE", "question_count": 1})
    path = "/api/v1/interviews/" + row["id"]
    data(client.post(path + "/start", headers=headers))
    result = upload(
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
    task = data(
        client.post(
            path + "/turns/" + row["current_question"]["id"] + "/submit",
            headers={**headers, "Idempotency-Key": "audio"},
            json={"asset_id": result["asset_id"]},
        )
    )
    exhaust(rt, task["task_id"])
    with rt.db.session() as db:
        assert db.scalar(select(func.count()).select_from(AICallLog).where(AICallLog.call_type == "asr")) == 1
    assert data(client.get(path + "/turns", headers=headers))["items"][0]["transcript"] == ANSWER


def test_video_validation_requires_audio_and_720p(monkeypatch):
    blob = b"\x00\x00\x00\x18ftyp" + b"\x00" * 20

    def probe(*args, **kwargs):
        return SimpleNamespace(
            stdout=json.dumps(
                {
                    "format": {"duration": "1"},
                    "streams": [
                        {"codec_type": "audio"},
                        {"codec_type": "video", "height": 720, "width": 1280},
                    ],
                }
            ).encode()
        )

    monkeypatch.setattr("app.modules.media.service.subprocess.run", probe)
    assert inspect_media(blob, "video/mp4", "VIDEO") == 1000
    monkeypatch.setattr(
        "app.modules.media.service.subprocess.run",
        lambda *a, **k: SimpleNamespace(
            stdout=json.dumps(
                {"format": {"duration": "1"}, "streams": [{"codec_type": "video", "height": 1080}]}
            ).encode()
        ),
    )
    with pytest.raises(BusinessError):
        inspect_media(blob, "video/mp4", "VIDEO")


def test_cleanup_deletes_expired_media(client):
    headers, _ = login(client)
    row = interview(client, headers, config={"mode": "VOICE"})
    path = "/api/v1/interviews/" + row["id"]
    data(client.post(path + "/start", headers=headers))
    item = upload(
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
    with rt.db.session() as db:
        asset = db.get(MediaAsset, item["asset_id"])
        object_key = asset.object_key
        asset.expires_at = utcnow() - timedelta(seconds=1)
    cleanup(rt)
    rt.drain()
    assert not rt.storage.path(object_key).exists()


def test_production_requires_real_dependencies():
    with pytest.raises(ValueError, match="生产环境必须配置"):
        Settings(environment="production")
