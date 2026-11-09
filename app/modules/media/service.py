import hashlib
import io
import json
import subprocess
import tempfile
import wave
from datetime import timedelta, timezone
from pathlib import Path

from sqlalchemy import select

from app.core.errors import BusinessError, require
from app.core.security import new_id, utcnow
from app.db.models import Interview, MediaAsset, ResumeAsset, Turn
from app.db.repository import owned
from app.modules.identity.service import IdentityService
from app.modules.shared import Service

MIMES = {
    "RESUME": {
        "application/pdf": {".pdf"},
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": {".docx"},
    },
    "AUDIO": {
        "audio/wav": {".wav"},
        "audio/x-wav": {".wav"},
        "audio/mpeg": {".mp3"},
        "audio/mp4": {".m4a", ".mp4"},
        "audio/aac": {".aac"},
        "audio/webm": {".webm"},
    },
    "VIDEO": {"video/mp4": {".mp4"}, "video/webm": {".webm"}},
    "AVATAR": {"image/png": {".png"}, "image/jpeg": {".jpg", ".jpeg"}},
    "JD_IMAGE": {"image/png": {".png"}, "image/jpeg": {".jpg", ".jpeg"}},
}


MIMES["SESSION_AUDIO"] = MIMES["AUDIO"]
MIMES["SESSION_VIDEO"] = MIMES["VIDEO"]
MIMES["SESSION_CAPTIONS"] = {"text/vtt": {".vtt"}}


def aware(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def inspect_media(data, mime, kind, *, ffprobe_bin="ffprobe"):
    if kind == "SESSION_CAPTIONS":
        try:
            valid = len(data) <= 1024 * 1024 and data.decode("utf-8").startswith("WEBVTT\n")
        except UnicodeDecodeError:
            valid = False
        require(valid, "MEDIA_INVALID_FORMAT", "字幕必须为 UTF-8 WebVTT 文件", 415)
        return None
    if mime == "application/pdf":
        valid = data.startswith(b"%PDF-")
    elif "wordprocessingml" in mime:
        valid = data.startswith(b"PK\x03\x04")
    elif mime in ("audio/wav", "audio/x-wav"):
        valid = data.startswith(b"RIFF") and data[8:12] == b"WAVE"
    elif mime.endswith("/mp4"):
        valid = data[4:8] == b"ftyp"
    elif mime.endswith("/webm"):
        valid = data.startswith(b"\x1aE\xdf\xa3")
    elif mime == "audio/mpeg":
        valid = data.startswith(b"ID3") or data[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2")
    elif mime == "audio/aac":
        valid = data[:2] in (b"\xff\xf1", b"\xff\xf9")
    elif mime == "image/png":
        valid = data.startswith(b"\x89PNG\r\n\x1a\n")
    else:
        valid = data.startswith(b"\xff\xd8\xff")
    require(valid, "MEDIA_INVALID_FORMAT", "文件头与声明类型不符", 415)
    if kind == "AVATAR":
        require(len(data) <= 2 * 1024 * 1024, "MEDIA_TOO_LARGE", "头像最大 2 MB", 400)
        try:
            with tempfile.NamedTemporaryFile() as temp:
                temp.write(data)
                temp.flush()
                result = subprocess.run(
                    [ffprobe_bin, "-v", "error", "-show_streams", "-of", "json", temp.name],
                    capture_output=True,
                    timeout=10,
                    check=True,
                )
                streams = json.loads(result.stdout).get("streams", [])
            require(
                len(streams) == 1
                and streams[0].get("codec_name") in ("png", "mjpeg")
                and 0 < streams[0].get("width", 0) <= 2048
                and 0 < streams[0].get("height", 0) <= 2048,
                "MEDIA_INVALID_FORMAT",
                "头像须为不超过 2048×2048 的 PNG 或 JPEG",
                415,
            )
        except OSError as exc:
            raise BusinessError(
                "MEDIA_PROCESSOR_UNAVAILABLE", "头像处理服务暂不可用，请联系管理员", 503
            ) from exc
        except (subprocess.SubprocessError, ValueError, KeyError, TypeError) as exc:
            raise BusinessError("MEDIA_INVALID_FORMAT", "无法解码头像图片", 415) from exc
        return None
    if kind not in ("AUDIO", "VIDEO", "SESSION_AUDIO", "SESSION_VIDEO"):
        return None
    if mime in ("audio/wav", "audio/x-wav"):
        try:
            with wave.open(io.BytesIO(data)) as audio:
                return int(audio.getnframes() * 1000 / audio.getframerate())
        except (wave.Error, EOFError, ZeroDivisionError) as exc:
            raise BusinessError("MEDIA_INVALID_FORMAT", "WAV 文件无效", 415) from exc
    try:
        with tempfile.NamedTemporaryFile() as temp:
            temp.write(data)
            temp.flush()
            result = subprocess.run(
                [ffprobe_bin, "-v", "error", "-show_format", "-show_streams", "-of", "json", temp.name],
                capture_output=True,
                timeout=20,
                check=True,
            )
            info = json.loads(result.stdout)
            duration = info.get("format", {}).get("duration")
            if duration in (None, "N/A"):
                # Browser MediaRecorder WebM files can omit the container duration.
                # Measure all streams so a longer video cannot bypass the limit.
                result = subprocess.run(
                    [
                        ffprobe_bin,
                        "-v",
                        "error",
                        "-show_entries",
                        "packet=pts_time,duration_time",
                        "-of",
                        "json",
                        temp.name,
                    ],
                    capture_output=True,
                    timeout=20,
                    check=True,
                )
                packets = json.loads(result.stdout)["packets"]
                duration = max(float(p["pts_time"]) + float(p["duration_time"]) for p in packets) - min(
                    float(p["pts_time"]) for p in packets
                )
        streams = info.get("streams", [])
        require(
            any(s["codec_type"] == "audio" for s in streams), "MEDIA_INVALID_FORMAT", "媒体必须包含音轨", 415
        )
        if kind in ("VIDEO", "SESSION_VIDEO"):
            video = next((s for s in streams if s["codec_type"] == "video"), None)
            require(
                video and video.get("height", 0) <= 720 and video.get("width", 0) <= 1280,
                "MEDIA_INVALID_FORMAT",
                "视频须为 720p 或更低分辨率",
                415,
            )
        return int(float(duration) * 1000)
    except OSError as exc:
        raise BusinessError("MEDIA_PROCESSOR_UNAVAILABLE", "录音处理服务暂不可用，请联系管理员", 503) from exc
    except (
        subprocess.SubprocessError,
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        OverflowError,
    ) as exc:
        raise BusinessError("MEDIA_INVALID_FORMAT", "无法解码媒体文件", 415) from exc


class MediaService(Service):
    def upload(self, data, resume=False):
        kind = "RESUME" if resume else data.media_type
        IdentityService(self.rt, self.db, self.uid).require_consents("PRIVACY", "TERMS")
        if kind == "AVATAR":
            require(data.size <= 2 * 1024 * 1024, "MEDIA_TOO_LARGE", "头像最大 2 MB")
        session_recording = kind in ("SESSION_AUDIO", "SESSION_VIDEO", "SESSION_CAPTIONS")
        if session_recording:
            IdentityService(self.rt, self.db, self.uid).require_consents(
                "MICROPHONE", *(["CAMERA"] if kind == "SESSION_VIDEO" else [])
            )
            require(
                data.interview_id and data.duration_ms,
                "MEDIA_INTERVIEW_REQUIRED",
                "整场录制必须关联面试和时长",
            )
            interview = owned(self.db, Interview, data.interview_id, self.uid)
            require(
                not interview.deleted_at
                and interview.status
                in ("IN_PROGRESS", "PAUSED", "FINISHING", "COMPLETED", "REPORT_FAILED", "CANCELLED"),
                "INTERVIEW_INVALID_STATE",
                "当前面试不允许保存整场录制",
                409,
            )
            require(data.turn_id is None, "MEDIA_SESSION_SCOPE", "整场录制不能关联单题轮次")
            require(data.duration_ms <= 7200000, "MEDIA_TOO_LONG", "整场录制最长 2 小时")
        if kind in ("AUDIO", "VIDEO"):
            IdentityService(self.rt, self.db, self.uid).require_consents(
                "MICROPHONE", *(["CAMERA"] if kind == "VIDEO" else [])
            )
            require(
                data.interview_id and data.duration_ms, "MEDIA_INTERVIEW_REQUIRED", "音视频必须关联面试和时长"
            )
            interview = owned(self.db, Interview, data.interview_id, self.uid)
            require(
                not interview.deleted_at and interview.status in ("IN_PROGRESS", "PAUSED"),
                "INTERVIEW_INVALID_STATE",
                "当前面试不允许上传",
                409,
            )
            require(data.turn_id, "MEDIA_TURN_REQUIRED", "音视频必须关联轮次")
            turn = self.db.get(Turn, data.turn_id)
            require(turn and turn.interview_id == interview.id, "RESOURCE_NOT_FOUND", "轮次不存在", 404)
        require(
            "/" not in data.filename and "\\" not in data.filename and data.filename not in (".", ".."),
            "MEDIA_INVALID_FILENAME",
            "文件名无效",
        )
        ext = Path(data.filename).suffix.lower()
        require(
            ext in MIMES[kind].get(data.content_type, set()), "MEDIA_INVALID_FORMAT", "不支持的文件格式", 415
        )
        maximum = (
            512 * 1024 * 1024
            if session_recording
            else 10 * 1024 * 1024
            if kind in ("RESUME", "JD_IMAGE")
            else self.rt.settings.max_upload_bytes
        )
        require(data.size <= maximum, "MEDIA_TOO_LARGE", "文件超过限制", 413)
        if kind == "SESSION_CAPTIONS":
            require(data.size <= 1024 * 1024, "MEDIA_TOO_LARGE", "字幕文件最大 1 MB", 413)
        if kind in ("AUDIO", "VIDEO"):
            require(
                data.duration_ms and data.duration_ms <= (120000 if kind == "VIDEO" else 180000),
                "MEDIA_TOO_LONG",
                "单题录音最长 3 分钟，录像最长 2 分钟",
            )
        if kind == "VIDEO":
            require(data.duration_ms <= 120000, "MEDIA_TOO_LONG", "视频单段最长 2 分钟")
        asset_id = new_id()
        row = MediaAsset(
            id=asset_id,
            user_id=self.uid,
            interview_id=data.interview_id
            if kind in ("AUDIO", "VIDEO", "SESSION_AUDIO", "SESSION_VIDEO", "SESSION_CAPTIONS")
            else None,
            turn_id=data.turn_id if kind in ("AUDIO", "VIDEO") else None,
            media_type=kind,
            filename=data.filename,
            mime_type=data.content_type,
            size_bytes=data.size,
            duration_ms=data.duration_ms,
            object_key=(
                f"ai-interview/user/{self.uid}/incoming/{asset_id}{ext}"
                if self.rt.settings.storage_backend == "oss"
                else f"{self.rt.settings.environment}/users/{self.uid}/{kind.lower()}/{asset_id}{ext}"
            ),
            expires_at=utcnow() + timedelta(days=self.rt.settings.media_retention_days),
        )
        self.db.add(row)
        self.db.flush()
        cloud = self.rt.settings.storage_backend == "oss"
        result = {
            "asset_id": row.id,
            "upload_url": f"/api/v1/storage/sts-token?asset_id={row.id}"
            if cloud
            else self.rt.storage.url(row, "upload"),
            "method": "OSS_STS" if cloud else "PUT",
            "headers": {"Content-Type": row.mime_type},
            "expires_in": self.rt.settings.upload_url_seconds,
        }
        if cloud:
            result.update(storage_backend="oss", object_key=row.object_key)
        if kind == "RESUME":
            parsed = ResumeAsset(user_id=self.uid, asset_id=row.id)
            self.db.add(parsed)
            self.db.flush()
            result["resume_id"] = parsed.id
        self.audit("MEDIA_UPLOAD_REQUESTED", row.id)
        return result

    def complete(self, asset_id, data):
        row = owned(self.db, MediaAsset, asset_id, self.uid)
        require(aware(row.expires_at) > utcnow(), "MEDIA_EXPIRED", "媒体已过期", 410)
        if row.status == "READY":
            return self.view(row)
        require(row.status == "PENDING_UPLOAD", "MEDIA_INVALID_STATE", "媒体状态不可完成上传", 409)
        head = self.rt.storage.head(row.object_key)
        require(head["size"] == row.size_bytes, "MEDIA_SIZE_MISMATCH", "对象大小与声明不一致")
        require(
            not head.get("mime") or head["mime"] == row.mime_type,
            "MEDIA_INVALID_FORMAT",
            "对象 Content-Type 不一致",
            415,
        )
        require(not data.etag or data.etag.strip('"') == head["etag"], "MEDIA_ETAG_MISMATCH", "ETag 不一致")
        reader = getattr(self.rt.storage, "read_version", None)
        content = reader(row.object_key, head["etag"]) if reader else self.rt.storage.read(row.object_key)
        require(len(content) == row.size_bytes, "MEDIA_SIZE_MISMATCH", "上传文件内容大小发生变化")
        digest = hashlib.sha256(content).hexdigest()
        require(
            not data.checksum or data.checksum.lower() == digest, "MEDIA_CHECKSUM_MISMATCH", "SHA256 不一致"
        )
        duration = inspect_media(
            content, row.mime_type, row.media_type, ffprobe_bin=self.rt.settings.ffprobe_bin
        )
        if duration is not None:
            require(
                0
                < duration
                <= (
                    7200000
                    if row.media_type in ("SESSION_AUDIO", "SESSION_VIDEO")
                    else 120000
                    if row.media_type == "VIDEO"
                    else 180000
                ),
                "MEDIA_TOO_LONG",
                "媒体实际时长超出范围",
            )
            require(
                abs(duration - row.duration_ms) <= max(1500, duration * 0.1),
                "MEDIA_DURATION_MISMATCH",
                "媒体实际时长与声明不一致",
            )
            row.duration_ms = duration
        row.object_key = self.rt.storage.finalize(row, head["etag"])
        row.checksum, row.etag, row.status = digest, head["etag"], "READY"
        if row.media_type == "RESUME":
            parsed = self.db.scalar(select(ResumeAsset).where(ResumeAsset.asset_id == row.id))
            parsed.parse_status = "PROCESSING"
            parsed.task_id = self.task("PARSE_RESUME", parsed.id).id
        self.audit("MEDIA_UPLOAD_COMPLETED", row.id)
        return self.view(row)

    def view(self, row):
        valid = row.status == "READY" and aware(row.expires_at) > utcnow()
        return {
            "id": row.id,
            "media_type": row.media_type,
            "interview_id": row.interview_id,
            "turn_id": row.turn_id,
            "status": "EXPIRED"
            if row.status == "READY" and aware(row.expires_at) <= utcnow()
            else row.status,
            "content_type": row.mime_type,
            "size_bytes": row.size_bytes,
            "duration_ms": row.duration_ms,
            "checksum": row.checksum,
            "expires_at": str(row.expires_at),
            "download_url": self.rt.storage.url(row) if valid else None,
        }

    def delete(self, asset_id):
        row = owned(self.db, MediaAsset, asset_id, self.uid)
        if row.status not in ("DELETED", "DELETING"):
            row.status = "DELETING"
            self.task("DELETE_MEDIA", row.id)
            self.audit("MEDIA_DELETE_REQUESTED", row.id)
        return {"asset_id": row.id, "status": row.status}
