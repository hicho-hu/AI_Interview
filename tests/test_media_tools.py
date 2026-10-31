import io
import json
import shutil
import subprocess
import wave
from types import SimpleNamespace

import httpx
import pytest

from app.core.config import Settings
from app.core.errors import BusinessError
from app.db.models import MediaAsset
from app.modules.media.service import inspect_media
from app.providers.compatible import CompatibleProvider
from scripts.start_local import media_tools
from tests.conftest import data, interview, login
from tests.test_media_identity import upload


def test_compressed_upload_can_retry_after_processor_is_restored(client, monkeypatch):
    headers, _ = login(client)
    row = interview(client, headers, config={"mode": "VOICE", "question_count": 1})
    data(client.post(f"/api/v1/interviews/{row['id']}/start", headers=headers))
    blob = b"\x1aE\xdf\xa3synthetic-webm"
    requested = data(
        client.post(
            "/api/v1/media/upload-url",
            headers=headers,
            json={
                "filename": "answer.webm",
                "content_type": "audio/webm",
                "media_type": "AUDIO",
                "size": len(blob),
                "duration_ms": 1000,
                "interview_id": row["id"],
                "turn_id": row["current_question"]["id"],
            },
        )
    )
    data(client.put(requested["upload_url"], content=blob, headers=requested["headers"]))
    settings = client.app.state.runtime.settings
    settings.ffprobe_bin = "/synthetic/missing/ffprobe"
    path = f"/api/v1/media/{requested['asset_id']}"
    failed = client.post(path + "/complete", headers=headers, json={})
    assert failed.status_code == 503
    assert failed.json()["code"] == "MEDIA_PROCESSOR_UNAVAILABLE"
    assert data(client.get(path, headers=headers))["status"] == "PENDING_UPLOAD"

    settings.ffprobe_bin = "/configured/tools/ffprobe"

    def probe(command, **kwargs):
        assert command[0] == settings.ffprobe_bin
        return SimpleNamespace(
            stdout=json.dumps({"format": {"duration": "1"}, "streams": [{"codec_type": "audio"}]})
        )

    monkeypatch.setattr("app.modules.media.service.subprocess.run", probe)
    ready = data(client.post(path + "/complete", headers=headers, json={}))
    assert ready["status"] == "READY"
    assert ready["duration_ms"] == 1000
    repeated = data(client.post(path + "/complete", headers=headers, json={}))
    assert (repeated["id"], repeated["status"]) == (ready["id"], "READY")


@pytest.mark.parametrize("duration_ms", [1000, 181000])
def test_webm_without_container_duration_uses_audio_packets(monkeypatch, duration_ms):
    results = iter(
        [
            {"format": {}, "streams": [{"codec_type": "audio"}]},
            {
                "packets": [
                    {"pts_time": "-0.007", "duration_time": "0.020"},
                    {"pts_time": str(duration_ms / 1000 - 0.027), "duration_time": "0.020"},
                ]
            },
        ]
    )

    def probe(command, **kwargs):
        assert command[0] == "/tools/ffprobe"
        return SimpleNamespace(stdout=json.dumps(next(results)))

    monkeypatch.setattr("app.modules.media.service.subprocess.run", probe)
    measured = inspect_media(b"\x1aE\xdf\xa3synthetic", "audio/webm", "AUDIO", ffprobe_bin="/tools/ffprobe")
    assert abs(measured - duration_ms) <= 1


def test_webm_without_duration_or_audio_packets_is_rejected(monkeypatch):
    results = iter([{"format": {}, "streams": [{"codec_type": "audio"}]}, {"packets": []}])
    monkeypatch.setattr(
        "app.modules.media.service.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(stdout=json.dumps(next(results))),
    )
    with pytest.raises(BusinessError) as error:
        inspect_media(b"\x1aE\xdf\xa3synthetic", "audio/webm", "AUDIO")
    assert error.value.code == "MEDIA_INVALID_FORMAT"


def test_live_webm_upload_and_asr_conversion(client, monkeypatch):
    settings = client.app.state.runtime.settings
    if not shutil.which(settings.ffmpeg_bin) or not shutil.which(settings.ffprobe_bin):
        pytest.skip("FFmpeg/FFprobe are required for the real synthetic WebM integration")
    blob = subprocess.run(
        [
            settings.ffmpeg_bin,
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=48000:cl=mono",
            "-t",
            "1",
            "-c:a",
            "libopus",
            "-live",
            "1",
            "-f",
            "webm",
            "pipe:1",
        ],
        check=True,
        capture_output=True,
        timeout=20,
    ).stdout
    headers, _ = login(client)
    row = interview(client, headers, config={"mode": "VOICE", "question_count": 1})
    data(client.post(f"/api/v1/interviews/{row['id']}/start", headers=headers))
    result = upload(
        client,
        headers,
        blob,
        {
            "filename": "answer.webm",
            "content_type": "audio/webm",
            "media_type": "AUDIO",
            "duration_ms": 1000,
            "interview_id": row["id"],
            "turn_id": row["current_question"]["id"],
        },
    )
    rt = client.app.state.runtime
    provider = CompatibleProvider(settings, rt.storage)

    def transcribe(path, **kwargs):
        assert path == "/audio/transcriptions"
        filename, content, mime = kwargs["files"]["file"]
        assert (filename, mime) == ("answer.wav", "audio/wav")
        with wave.open(io.BytesIO(content)) as audio:
            assert (audio.getnchannels(), audio.getframerate()) == (1, 16000)
            assert 15000 <= audio.getnframes() <= 17000
        return httpx.Response(200, json={"text": "合成录音测试"})

    monkeypatch.setattr(provider, "request", transcribe)
    with rt.db.session() as db:
        asset = db.get(MediaAsset, result["asset_id"])
        response = provider.invoke(
            "asr",
            {
                "object_key": asset.object_key,
                "mime_type": asset.mime_type,
                "duration_ms": asset.duration_ms,
                "language": "zh-CN",
            },
            {},
        )
    assert response["data"]["text"] == "合成录音测试"


@pytest.mark.parametrize(
    "failure", [FileNotFoundError(), PermissionError(), subprocess.TimeoutExpired("ffprobe", 10)]
)
def test_startup_stops_when_media_tools_cannot_run(monkeypatch, failure):
    def unavailable(command, **kwargs):
        if command[0] == "/tools/ffprobe":
            raise failure
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("scripts.start_local.subprocess.run", unavailable)
    settings = Settings(_env_file=None, ffmpeg_bin="/tools/ffmpeg", ffprobe_bin="/tools/ffprobe")
    with pytest.raises(RuntimeError, match="ffprobe 不可用"):
        media_tools(settings)
