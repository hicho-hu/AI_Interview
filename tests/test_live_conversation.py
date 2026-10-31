import base64
import io
import json
import queue
import time
import wave

from app.modules.media.service import MediaService
from app.providers.ai import MockProvider
from app.providers.live import audio_turn, live_sink
from app.schemas.contracts import UploadComplete, UploadInput
from tests.conftest import data, interview, login


def wav(pcm, rate=16000):
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(pcm)
    return output.getvalue()


class StreamingProvider(MockProvider):
    asr_calls = 0

    def invoke(self, operation, payload, schema):
        if operation == "asr":
            self.asr_calls += 1
        if operation == "tts" and live_sink.get():
            pcm = b"\x01\x00" * 2400
            live_sink.get()({"type": "audio_reset", "turn_id": audio_turn.get()})
            live_sink.get()(
                {
                    "type": "audio",
                    "turn_id": audio_turn.get(),
                    "sample_rate": 24000,
                    "pcm": base64.b64encode(pcm).decode(),
                }
            )
            time.sleep(0.02)
            return {
                "data": {"audio_base64": base64.b64encode(wav(pcm, 24000)).decode(), "mime_type": "audio/wav"}
            }
        return super().invoke(operation, payload, schema)


def setup(client):
    headers, uid = login(client)
    row = interview(client, headers, config={"mode": "VOICE", "question_count": 2, "allow_follow_up": True})
    data(client.post(f"/api/v1/interviews/{row['id']}/start", headers=headers))
    provider = StreamingProvider()
    client.app.state.runtime.provider = provider
    return headers, uid, row, provider


def events(response):
    assert response.is_success, response.text
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]


def test_stream_pushes_transcript_and_pcm_before_committed_state_and_is_idempotent(client):
    headers, uid, row, _ = setup(client)
    path = f"/api/v1/interviews/{row['id']}/turns/{row['current_question']['id']}/submit-stream"
    request_headers = {**headers, "Idempotency-Key": "live-answer"}
    output = events(
        client.post(path, headers=request_headers, json={"text": "我负责 React 组件开发和性能优化。"})
    )
    kinds = [event["type"] for event in output]
    assert (
        kinds.index("transcript") < kinds.index("question") < kinds.index("audio") < kinds.index("complete")
    )
    assert output[-1]["interview"]["current_question"]["question_audio"]["content_type"] == "audio/wav"
    again = events(
        client.post(path, headers=request_headers, json={"text": "我负责 React 组件开发和性能优化。"})
    )
    assert again[-1]["type"] == "complete"
    assert len(data(client.get(f"/api/v1/interviews/{row['id']}/turns", headers=headers))["items"]) == 2
    other, _ = login(client, "other-live-owner")
    assert (
        client.post(path, headers={**other, "Idempotency-Key": "wrong"}, json={"text": "回答"}).status_code
        == 404
    )


class FakeASRSocket:
    def __init__(self):
        self.items = queue.Queue()
        self.items.put(json.dumps({"type": "session.updated"}))

    def recv(self):
        return self.items.get(timeout=3)

    def send(self, message):
        event = json.loads(message)
        if event["type"] == "input_audio_buffer.append":
            self.items.put(
                json.dumps(
                    {
                        "type": "conversation.item.input_audio_transcription.text",
                        "item_id": "speech",
                        "text": "我负责",
                        "stash": "组件开发",
                    }
                )
            )
        elif event["type"] == "session.finish":
            self.items.put(
                json.dumps(
                    {
                        "type": "conversation.item.input_audio_transcription.completed",
                        "item_id": "speech",
                        "transcript": "我负责组件开发。",
                    }
                )
            )
            self.items.put(json.dumps({"type": "session.finished"}))

    def close(self):
        self.items.put(json.dumps({"type": "session.finished"}))


def live_proof(client, monkeypatch, headers, row, pcm):
    from app.providers import qwen_realtime

    settings = client.app.state.runtime.settings
    settings.provider_mode, settings.asr_provider = "compatible", "qwen"
    settings.realtime_asr_url = "wss://test.invalid/realtime"
    monkeypatch.setattr(qwen_realtime, "connect", lambda *_: FakeASRSocket())
    with client.websocket_connect(f"/api/v1/interviews/{row['id']}/live-transcription") as ws:
        ws.send_json({"token": headers["Authorization"][7:], "turn_id": row["current_question"]["id"]})
        assert ws.receive_json()["type"] == "ready"
        ws.send_bytes(pcm)
        partial = ws.receive_json()
        assert partial["text"] == "我负责组件开发"
        ws.send_json({"type": "finish"})
        assert ws.receive_json()["type"] == "caption"
        result = ws.receive_json()
        assert result["type"] == "complete"
        return result["transcript_id"]


def upload_audio(client, uid, row, pcm):
    rt = client.app.state.runtime
    audio = wav(pcm)
    with rt.db.session() as db:
        svc = MediaService(rt, db, uid)
        ticket = svc.upload(
            UploadInput(
                filename="answer.wav",
                content_type="audio/wav",
                media_type="AUDIO",
                size=len(audio),
                duration_ms=len(pcm) // 32,
                interview_id=row["id"],
                turn_id=row["current_question"]["id"],
            )
        )
        from app.db.models import MediaAsset

        asset = db.get(MediaAsset, ticket["asset_id"])
        rt.storage.put(asset.object_key, audio, "audio/wav")
        svc.complete(asset.id, UploadComplete())
        return asset.id


def test_verified_live_transcript_skips_second_asr_and_captions_are_live(client, monkeypatch):
    headers, uid, row, provider = setup(client)
    pcm = b"\x01\x00" * 16000
    proof = live_proof(client, monkeypatch, headers, row, pcm)
    asset = upload_audio(client, uid, row, pcm)
    output = events(
        client.post(
            f"/api/v1/interviews/{row['id']}/turns/{row['current_question']['id']}/submit-stream",
            headers={**headers, "Idempotency-Key": "live-proof"},
            json={"asset_id": asset, "client_duration_ms": 1000, "live_transcript_id": proof},
        )
    )
    assert next(event for event in output if event["type"] == "transcript")["text"] == "我负责组件开发。"
    assert provider.asr_calls == 0


def test_live_transcript_proof_cannot_be_reused_for_different_audio(client, monkeypatch):
    headers, uid, row, provider = setup(client)
    proof = live_proof(client, monkeypatch, headers, row, b"\x01\x00" * 16000)
    asset = upload_audio(client, uid, row, b"\x02\x00" * 16000)
    events(
        client.post(
            f"/api/v1/interviews/{row['id']}/turns/{row['current_question']['id']}/submit-stream",
            headers={**headers, "Idempotency-Key": "wrong-proof"},
            json={"asset_id": asset, "client_duration_ms": 1000, "live_transcript_id": proof},
        )
    )
    assert provider.asr_calls == 1


def test_webvtt_is_validated_and_owned(client):
    headers, uid, row, _ = setup(client)
    rt = client.app.state.runtime
    content = b"WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nTest caption\n"
    with rt.db.session() as db:
        svc = MediaService(rt, db, uid)
        ticket = svc.upload(
            UploadInput(
                filename="captions.vtt",
                content_type="text/vtt",
                media_type="SESSION_CAPTIONS",
                size=len(content),
                duration_ms=1000,
                interview_id=row["id"],
            )
        )
        from app.db.models import MediaAsset

        asset = db.get(MediaAsset, ticket["asset_id"])
        rt.storage.put(asset.object_key, content, "text/vtt")
        assert svc.complete(asset.id, UploadComplete())["status"] == "READY"
