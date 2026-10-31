import base64
import json
from contextlib import contextmanager

import dashscope
import httpx
import pytest
from dashscope.audio.tts_v2 import AudioFormat
from dashscope.common.error import RequestFailure
from pydantic import ValidationError
from sqlalchemy import select

from app.core.config import Settings
from app.core.errors import BusinessError
from app.db.models import AICallLog
from app.providers import dashscope_tts
from app.providers.ai import MockProvider
from app.providers.compatible import INSTRUCTIONS, CompatibleProvider
from app.schemas.contracts import JobStructure, SynthesizedAudio
from tests.conftest import data, interview, login
from tests.test_interview import ANSWER

CHAT_URL = "https://workspace.example.invalid/compatible-mode/v1"
WS_URL = "wss://workspace.example.invalid/api-ws/v1/inference"


def qwen_settings(**kwargs):
    return Settings(
        _env_file=None,
        provider_mode="compatible",
        provider_url=CHAT_URL,
        provider_model="qwen3.8-omni-flash",
        provider_api_key="synthetic-test-key",
        tts_provider="dashscope",
        dashscope_websocket_url=WS_URL,
        tts_model="qwen-audio-3.1-tts-flash",
        tts_voice="longanhuan_v3.1",
        llm_input_cost_per_million=1000000,
        llm_output_cost_per_million=2000000,
        tts_input_cost_per_million=1000000,
        tts_output_cost_per_million=3000000,
        **kwargs,
    )


def sse_chunk(value):
    return "data: " + json.dumps(value, ensure_ascii=False) + "\r\n\r\n"


def completion_stream(content, *, finish="stop", usage=True, done=True):
    result = ": heartbeat\r\n\r\n"
    result += sse_chunk({"choices": [{"delta": {"role": "assistant", "content": None}}], "usage": None})
    result += sse_chunk({"choices": [{"delta": {"reasoning_content": "ignored"}}]})
    for offset in range(0, len(content), 13):
        result += sse_chunk({"choices": [{"delta": {"content": content[offset : offset + 13]}}]})
    result += sse_chunk({"choices": [{"delta": {}, "finish_reason": finish}]})
    if usage:
        result += sse_chunk({"choices": [], "usage": {"prompt_tokens": 20, "completion_tokens": 5}})
    if done:
        result += "data: [DONE]\r\n\r\n"
    return result.encode()


class ByteStream(httpx.SyncByteStream):
    """Split SSE lines and Chinese UTF-8 characters across network chunks."""

    def __init__(self, content):
        self.content = content
        self.closed = False

    def __iter__(self):
        for offset in range(0, len(self.content), 7):
            yield self.content[offset : offset + 7]

    def close(self):
        self.closed = True


def mock_stream(monkeypatch, handler):
    @contextmanager
    def stream(method, url, **kwargs):
        assert kwargs["follow_redirects"] is False
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            with client.stream(method, url, **kwargs) as response:
                yield response

    monkeypatch.setattr(httpx, "stream", stream)


def fake_tts(monkeypatch, *, audio=b"ID3-synthetic-speech", event="task-finished", usage=None, error=None):
    instances = []

    class Synthesizer:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.api_key = dashscope.api_key
            self.closed = False
            instances.append(self)

        def call(self, text, timeout_millis):
            self.text, self.timeout_millis = text, timeout_millis
            if error:
                raise error
            return audio

        def get_response(self):
            return {
                "header": {"event": event},
                "payload": {"usage": usage if usage is not None else {"input_tokens": 4, "output_tokens": 8}},
            }

        def close(self):
            self.closed = True

    monkeypatch.setattr(dashscope_tts, "SpeechSynthesizer", Synthesizer)
    return instances


def test_dashscope_key_alias_and_explicit_provider_key(monkeypatch, tmp_path):
    monkeypatch.delenv("PROVIDER_API_KEY", raising=False)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "synthetic-dashscope-key")
    assert Settings(_env_file=None).provider_api_key == "synthetic-dashscope-key"
    assert Settings(_env_file=None, provider_api_key="explicit-key").provider_api_key == "explicit-key"
    env_file = tmp_path / ".env"
    env_file.write_text("DASHSCOPE_API_KEY=synthetic-dotenv-key\n")
    monkeypatch.delenv("DASHSCOPE_API_KEY")
    assert Settings(_env_file=env_file).provider_api_key == "synthetic-dotenv-key"
    monkeypatch.setenv("DASHSCOPE_API_KEY", "synthetic-dashscope-key")
    monkeypatch.setenv("PROVIDER_API_KEY", "synthetic-provider-key")
    assert Settings(_env_file=None).provider_api_key == "synthetic-provider-key"


@pytest.mark.parametrize("url", ["", "https://example.invalid", "ws://example.invalid"])
def test_dashscope_tts_requires_secure_endpoint(url):
    with pytest.raises(ValidationError, match="WSS"):
        Settings(
            _env_file=None,
            provider_mode="compatible",
            provider_api_key="test",
            tts_provider="dashscope",
            dashscope_websocket_url=url,
        )


def test_qwen_chat_stream_and_usage(monkeypatch):
    content = json.dumps(
        {"responsibilities": ["中文职责"], "required_skills": ["Python"], "competencies": ["专业深度"]},
        ensure_ascii=False,
    )
    byte_stream = ByteStream(completion_stream(content))

    def handler(request):
        assert str(request.url) == CHAT_URL + "/chat/completions"
        assert request.headers["authorization"] == "Bearer synthetic-test-key"
        body = json.loads(request.content)
        assert body["model"] == "qwen3.8-omni-flash"
        assert body["modalities"] == ["text"]
        assert body["stream"] is True
        assert body["stream_options"] == {"include_usage": True}
        assert body["response_format"] == {"type": "json_object"}
        assert body["reasoning_effort"] == "none"
        assert "audio" not in body
        assert "不可信数据" in body["messages"][0]["content"]
        return httpx.Response(
            200, headers={"content-type": "text/event-stream; charset=utf-8"}, stream=byte_stream
        )

    mock_stream(monkeypatch, handler)
    result = CompatibleProvider(qwen_settings(), None).invoke(
        "job", {"jd_text": "示例"}, JobStructure.model_json_schema()
    )
    assert JobStructure.model_validate(result["data"]).responsibilities == ["中文职责"]
    assert result["usage"]["input_tokens"] == 20
    assert result["usage"]["output_tokens"] == 5
    assert result["usage"]["cost_microunits"] == 30
    assert byte_stream.closed


@pytest.mark.parametrize(
    "content,code",
    [
        (completion_stream("{}", done=False), "AI_PROVIDER_BUSY"),
        (completion_stream("{}", finish="length"), "AI_OUTPUT_INVALID"),
        (completion_stream("{}", usage=False), "AI_OUTPUT_INVALID"),
        (b"data: not-json\n\n", "AI_OUTPUT_INVALID"),
        (b'data: {"choices":[null]}\n\n', "AI_OUTPUT_INVALID"),
        (b'data: {"error":{"message":"private upstream details"}}\n\n', "AI_PROVIDER_BUSY"),
    ],
)
def test_qwen_stream_rejects_incomplete_or_invalid_output(monkeypatch, content, code):
    mock_stream(
        monkeypatch,
        lambda request: httpx.Response(200, headers={"content-type": "text/event-stream"}, content=content),
    )
    with pytest.raises(BusinessError) as caught:
        CompatibleProvider(qwen_settings(), None).invoke("job", {}, {})
    assert caught.value.code == code
    assert "private upstream details" not in str(caught.value)


@pytest.mark.parametrize(
    "status,code",
    [
        (401, "AI_PROVIDER_REJECTED"),
        (302, "AI_PROVIDER_REJECTED"),
        (429, "AI_PROVIDER_BUSY"),
        (503, "AI_PROVIDER_BUSY"),
    ],
)
def test_qwen_stream_http_errors_are_sanitized(monkeypatch, status, code):
    mock_stream(monkeypatch, lambda request: httpx.Response(status, text="private upstream details"))
    with pytest.raises(BusinessError) as caught:
        CompatibleProvider(qwen_settings(), None).invoke("job", {}, {})
    assert caught.value.code == code
    assert "private upstream details" not in str(caught.value)


def test_qwen_stream_timeout(monkeypatch):
    def handler(request):
        raise httpx.ReadTimeout("private upstream details")

    mock_stream(monkeypatch, handler)
    with pytest.raises(BusinessError) as caught:
        CompatibleProvider(qwen_settings(), None).invoke("job", {}, {})
    assert caught.value.code == "AI_PROVIDER_BUSY"
    assert "private upstream details" not in str(caught.value)


def test_dashscope_tts_protocol_usage_and_cleanup(monkeypatch):
    original_key, original_url = dashscope.api_key, dashscope.base_websocket_api_url
    instances = fake_tts(monkeypatch)
    result = CompatibleProvider(qwen_settings(), None).invoke("tts", {"text": "面试问题"}, {})
    output = SynthesizedAudio.model_validate(result["data"])
    assert base64.b64decode(output.audio_base64) == b"ID3-synthetic-speech"
    assert output.mime_type == "audio/mpeg"
    assert instances[0].kwargs == {
        "model": "qwen-audio-3.1-tts-flash",
        "voice": "longanhuan_v3.1",
        "url": WS_URL,
        "format": AudioFormat.MP3_22050HZ_MONO_256KBPS,
    }
    assert instances[0].api_key == "synthetic-test-key"
    assert instances[0].text == "面试问题"
    assert instances[0].timeout_millis == 30000
    assert instances[0].closed
    assert dashscope.api_key == original_key
    assert dashscope.base_websocket_api_url == original_url
    assert result["usage"]["input_tokens"] == 4
    assert result["usage"]["output_tokens"] == 8
    assert result["usage"]["cost_microunits"] == 28


@pytest.mark.parametrize(
    "options",
    [
        {"audio": None},
        {"audio": b""},
        {"event": "task-failed"},
        {"usage": {}},
        {"usage": {"input_tokens": -1, "output_tokens": 5}},
    ],
)
def test_dashscope_rejects_partial_audio_and_invalid_usage(monkeypatch, options):
    instances = fake_tts(monkeypatch, **options)
    with pytest.raises(BusinessError) as caught:
        CompatibleProvider(qwen_settings(), None).invoke("tts", {"text": "问题"}, {})
    assert caught.value.code == "AI_OUTPUT_INVALID"
    assert instances[0].closed


@pytest.mark.parametrize(
    "error,code",
    [
        (TimeoutError("private upstream details"), "AI_PROVIDER_BUSY"),
        (RequestFailure(name="Throttling", message="private upstream details"), "AI_PROVIDER_BUSY"),
        (RequestFailure(name="InvalidApiKey", message="private upstream details"), "AI_PROVIDER_REJECTED"),
    ],
)
def test_dashscope_errors_are_sanitized_and_close_connection(monkeypatch, error, code):
    instances = fake_tts(monkeypatch, error=error)
    with pytest.raises(BusinessError) as caught:
        CompatibleProvider(qwen_settings(), None).invoke("tts", {"text": "问题"}, {})
    assert caught.value.code == code
    assert "private upstream details" not in str(caught.value)
    assert instances[0].closed


def test_qwen_voice_questions_and_text_answers_complete_interview(client, monkeypatch):
    rt = client.app.state.runtime
    settings = qwen_settings()
    rt.settings = rt.settings.model_copy(
        update={
            name: getattr(settings, name)
            for name in (
                "provider_mode",
                "provider_url",
                "provider_model",
                "provider_api_key",
                "tts_provider",
                "tts_model",
                "tts_voice",
                "dashscope_websocket_url",
                "llm_input_cost_per_million",
                "llm_output_cost_per_million",
                "tts_input_cost_per_million",
                "tts_output_cost_per_million",
            )
        }
    )
    rt.provider = CompatibleProvider(rt.settings, rt.storage)
    rt.model_name = settings.provider_model
    calls = []

    def handler(request):
        body = json.loads(request.content)
        operation = next(
            name
            for name, instruction in INSTRUCTIONS.items()
            if instruction in body["messages"][0]["content"]
        )
        calls.append(operation)
        payload = json.loads(body["messages"][1]["content"])
        if operation == "report":
            for turn in payload["turns"]:
                turn["answer"] = "".join(part["text"] for part in turn["answer_segments"])
        output = MockProvider().invoke(operation, payload, {})["data"]
        if operation == "report":
            for review, turn in zip(output["reviews"], payload["turns"]):
                review["evidence"] = [turn["answer_segments"][0]["evidence_id"]]
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=completion_stream(json.dumps(output, ensure_ascii=False)),
        )

    mock_stream(monkeypatch, handler)
    instances = fake_tts(monkeypatch)
    headers, _ = login(client)
    row = interview(client, headers, config={"mode": "VOICE", "question_count": 1})
    question_audio = row["current_question"]["question_audio"]
    assert question_audio["content_type"] == "audio/mpeg"
    download = client.get(question_audio["download_url"])
    assert download.status_code == 200
    assert download.content == b"ID3-synthetic-speech"
    path = "/api/v1/interviews/" + row["id"]
    data(client.post(path + "/start", headers=headers))
    data(
        client.post(
            path + "/turns/" + row["current_question"]["id"] + "/submit",
            headers={**headers, "Idempotency-Key": "qwen-answer"},
            json={"text": ANSWER},
        )
    )
    result = data(client.get(path + "/report", headers=headers))
    assert result["status"] == "COMPLETED"
    assert result["report"]["model"] == "qwen3.8-omni-flash"
    assert result["report"]["reviews"][0]["evidence"][0] in ANSWER
    assert calls == ["job", "plan", "decision", "report"]
    assert len(instances) == 1 and instances[0].closed
    with rt.db.session() as db:
        logs = db.scalars(select(AICallLog).order_by(AICallLog.created_at)).all()
        assert len(logs) == 5 and all(log.status == "SUCCEEDED" for log in logs)
        tts_log = next(log for log in logs if log.call_type == "tts")
        assert tts_log.model == "qwen-audio-3.1-tts-flash"
        assert tts_log.cost_microunits == 28


def test_qwen_output_still_passes_runtime_schema_validation(client, monkeypatch):
    rt = client.app.state.runtime
    rt.provider = CompatibleProvider(qwen_settings(), rt.storage)
    mock_stream(
        monkeypatch,
        lambda request: httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=completion_stream('{"unexpected":"field"}'),
        ),
    )
    _, uid = login(client)
    with rt.db.session() as db, pytest.raises(BusinessError) as caught:
        rt.ai(db, uid, "job", {}, JobStructure)
    assert caught.value.code == "AI_OUTPUT_INVALID"
