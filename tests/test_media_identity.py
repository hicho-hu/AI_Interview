import base64
import hashlib
import io
import wave

from docx import Document
from sqlalchemy import select

from app.db.models import JobProfile, Task, User
from app.providers.ai import MockProvider
from tests.conftest import data, interview, job, login
from tests.test_interview import ANSWER


def wav_bytes():
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\x00\x00" * 16000)
    return output.getvalue()


def upload(client, headers, blob, payload, resume=False):
    result = data(
        client.post(
            "/api/v1/" + ("resumes" if resume else "media") + "/upload-url",
            headers=headers,
            json={**payload, "size": len(blob)},
        )
    )
    response = client.put(result["upload_url"], content=blob, headers=result["headers"])
    data(response)
    ready = data(
        client.post(
            "/api/v1/media/" + result["asset_id"] + "/complete",
            headers=headers,
            json={"checksum": hashlib.sha256(blob).hexdigest()},
        )
    )
    assert ready["status"] == "READY"
    return result


def test_auth_consents_and_encryption(client):
    assert client.get("/api/v1/users/me").status_code == 401
    headers, uid = login(client, consents=False)
    bad = client.post(
        "/api/v1/job-profiles",
        headers=headers,
        json={"job_title": "测试", "jd_text": "岗位需求：开发 Python 数据服务"},
    )
    assert bad.status_code == 403
    headers, _ = login(client)
    jid = job(client, headers)
    with client.app.state.runtime.db.session() as db:
        assert "Python" not in db.get(JobProfile, jid).jd_encrypted
    data(client.delete("/api/v1/users/me/consents/AI_PROCESSING", headers=headers))
    assert (
        client.post(
            "/api/v1/interviews",
            headers={**headers, "Idempotency-Key": "create"},
            json={"job_profile_id": jid},
        ).status_code
        == 403
    )
    data(client.post("/api/v1/auth/logout", headers=headers))
    assert client.get("/api/v1/users/me", headers=headers).status_code == 401


def test_docx_resume_parse_and_delete(client):
    headers, _ = login(client)
    document = Document()
    document.add_paragraph("求职者：开发过 Python API，负责订单接口与数据库索引优化。")
    buffer = io.BytesIO()
    document.save(buffer)
    result = upload(
        client,
        headers,
        buffer.getvalue(),
        {
            "filename": "resume.docx",
            "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        },
        resume=True,
    )
    parsed = data(client.get("/api/v1/resumes/" + result["resume_id"], headers=headers))
    assert parsed["parse_status"] == "READY"
    assert "Python" in parsed["summary"]["summary"]
    jid = job(client, headers)
    iv = data(
        client.post(
            "/api/v1/interviews",
            headers={**headers, "Idempotency-Key": "with-resume"},
            json={
                "job_profile_id": jid,
                "resume_asset_id": parsed["id"],
                "config": {"mode": "TEXT", "question_count": 1},
            },
        )
    )
    row = data(client.get("/api/v1/interviews/" + iv["interview_id"], headers=headers))
    assert "简历" in row["current_question"]["question"]
    data(client.delete("/api/v1/resumes/" + parsed["id"], headers=headers))
    assert data(client.get("/api/v1/media/" + result["asset_id"], headers=headers))["status"] == "DELETED"


class SpeechFixture(MockProvider):
    def invoke(self, operation, payload, schema):
        if operation == "tts":
            return {
                "data": {
                    "audio_base64": base64.b64encode(b"ID3-test-mp3").decode(),
                    "mime_type": "audio/mpeg",
                },
                "usage": {"cost_microunits": 10},
            }
        if operation == "asr":
            return {
                "data": {"text": ANSWER, "confidence": 0.95, "language": "zh-CN", "duration_ms": 1000},
                "usage": {"cost_microunits": 20, "units": {"asr_duration_ms": 1000}},
            }
        return super().invoke(operation, payload, schema)


def test_voice_asr_tts_and_private_media(client):
    client.app.state.runtime.provider = SpeechFixture()
    headers, _ = login(client)
    row = interview(client, headers, config={"mode": "VOICE", "question_count": 1})
    audio = row["current_question"]["question_audio"]
    assert audio["download_url"]
    assert client.get(audio["download_url"]).content == b"ID3-test-mp3"
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
    media = data(client.get("/api/v1/media/" + result["asset_id"], headers=headers))
    assert client.get(media["download_url"] + "tamper").status_code == 403
    assert client.put(result["upload_url"], content=wav_bytes(), headers=result["headers"]).status_code == 409
    submitted = data(
        client.post(
            path + "/turns/" + row["current_question"]["id"] + "/submit",
            headers={**headers, "Idempotency-Key": "audio-answer"},
            json={"asset_id": result["asset_id"], "client_duration_ms": 1000},
        )
    )
    assert data(client.get("/api/v1/tasks/" + submitted["task_id"], headers=headers))["status"] == "SUCCEEDED"
    turns = data(client.get(path + "/turns", headers=headers))["items"]
    assert turns[0]["transcript"] == ANSWER
    assert data(client.get(path + "/report", headers=headers))["status"] == "COMPLETED"


def test_low_asr_quality_clarifies_and_excludes_scoring(client):
    headers, _ = login(client)
    row = interview(client, headers, config={"mode": "VOICE", "question_count": 1, "max_clarifications": 1})
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
    data(
        client.post(
            path + "/turns/" + row["current_question"]["id"] + "/submit",
            headers={**headers, "Idempotency-Key": "low"},
            json={"asset_id": result["asset_id"]},
        )
    )
    new = data(client.get(path, headers=headers))
    assert new["current_question"]["turn_type"] == "CLARIFICATION"
    data(
        client.post(
            path + "/turns/" + new["current_question"]["id"] + "/submit",
            headers={**headers, "Idempotency-Key": "clarified"},
            json={"text": ANSWER},
        )
    )
    report = data(client.get(path + "/report", headers=headers))["report"]
    assert report["unassessable_turn_ids"] == [row["current_question"]["id"]]
    assert len(report["reviews"]) == 1


def test_upload_rejects_spoofed_files_and_cross_user(client):
    headers, _ = login(client)
    other, _ = login(client, "bob")
    invalid = client.post(
        "/api/v1/resumes/upload-url",
        headers=headers,
        json={"filename": "../x.pdf", "content_type": "application/pdf", "size": 10},
    )
    assert invalid.status_code == 400
    result = data(
        client.post(
            "/api/v1/resumes/upload-url",
            headers=headers,
            json={"filename": "x.pdf", "content_type": "application/pdf", "size": 10},
        )
    )
    data(client.put(result["upload_url"], content=b"evil-bytes", headers=result["headers"]))
    assert (
        client.post("/api/v1/media/" + result["asset_id"] + "/complete", headers=headers, json={}).status_code
        == 415
    )
    assert client.get("/api/v1/media/" + result["asset_id"], headers=other).status_code == 404


def test_personal_data_deletion_removes_sensitive_payloads(client):
    headers, uid = login(client)
    interview(client, headers)
    deletion = data(client.delete("/api/v1/users/me/data", headers=headers))
    assert deletion["task_id"]
    assert client.get("/api/v1/users/me", headers=headers).status_code == 401
    rt = client.app.state.runtime
    with rt.db.session() as db:
        assert db.get(User, uid).status == "DELETED"
        for task in db.scalars(select(Task).where(Task.user_id == uid, Task.id != deletion["task_id"])):
            assert task.payload_encrypted is None
        for job_row in db.scalars(select(JobProfile).where(JobProfile.user_id == uid)):
            assert rt.security.decrypt(job_row.jd_encrypted) == "[已删除]"


def test_video_segments_are_associated_replayed_and_transcribed(client, monkeypatch):
    import json
    from types import SimpleNamespace

    monkeypatch.setattr(
        "app.modules.media.service.subprocess.run",
        lambda *a, **k: SimpleNamespace(
            stdout=json.dumps(
                {
                    "format": {"duration": "1"},
                    "streams": [
                        {"codec_type": "audio"},
                        {"codec_type": "video", "height": 720, "width": 1280},
                    ],
                }
            ).encode()
        ),
    )
    headers, _ = login(client)
    client.app.state.runtime.provider = SpeechFixture()
    row = interview(client, headers, config={"mode": "VIDEO", "question_count": 1})
    path = "/api/v1/interviews/" + row["id"]
    data(client.post(path + "/start", headers=headers))
    blob = b"\x00\x00\x00\x18ftyp" + b"\x00" * 20
    assets = []
    for i in range(2):
        assets.append(
            upload(
                client,
                headers,
                blob,
                {
                    "filename": f"part{i}.mp4",
                    "content_type": "video/mp4",
                    "media_type": "VIDEO",
                    "duration_ms": 1000,
                    "interview_id": row["id"],
                    "turn_id": row["current_question"]["id"],
                },
            )["asset_id"]
        )
    listing = data(client.get("/api/v1/media?turn_id=" + row["current_question"]["id"], headers=headers))
    assert sum(item["media_type"] == "VIDEO" for item in listing["items"]) == 2
    for item in listing["items"]:
        assert client.get(item["download_url"]).is_success
    data(
        client.post(
            path + "/turns/" + row["current_question"]["id"] + "/submit",
            headers={**headers, "Idempotency-Key": "segments"},
            json={"asset_ids": assets, "client_duration_ms": 2000},
        )
    )
    assert (
        data(client.get(path + "/turns", headers=headers))["items"][0]["transcript"] == ANSWER + "\n" + ANSWER
    )
    assert data(client.get(path + "/report", headers=headers))["status"] == "COMPLETED"
