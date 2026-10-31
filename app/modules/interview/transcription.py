"""Live captions and a short-lived, audio-bound transcript proof for answer submission."""

import asyncio
import base64
import hashlib
import io
import json
import time
import wave
from contextlib import suppress

from fastapi import WebSocketDisconnect
from sqlalchemy import func, select

from app.core.errors import BusinessError, require
from app.core.security import new_id, utcnow
from app.db.models import AICallLog, Turn, User
from app.modules.identity.service import IdentityService
from app.modules.interview.service import InterviewService
from app.providers import qwen_realtime


def verified_transcript(rt, uid, turn_id, proof_id, asset):
    if not proof_id:
        return None
    try:
        encrypted = rt.cache.get(f"live-asr:{uid}:{turn_id}:{proof_id}")
        if not encrypted:
            return None
        proof = json.loads(rt.security.decrypt(encrypted))
        with wave.open(io.BytesIO(rt.storage.read(asset.object_key)), "rb") as audio:
            if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) != (1, 2, 16000):
                return None
            digest = hashlib.sha256(audio.readframes(audio.getnframes())).hexdigest()
        return proof["text"] if digest == proof["sha256"] else None
    except (ValueError, KeyError, OSError, wave.Error):
        return None


async def transcribe_socket(ws, interview_id):
    rt = ws.app.state.runtime
    await ws.accept()
    upstream = None
    reader = None
    uid, turn_id, language = None, None, None
    started, pcm = time.monotonic(), bytearray()
    completed, success = asyncio.Event(), False
    pieces = {}
    final_items = set()
    failure = []
    last_check = started
    initial_cost, daily_cost = 0, 0

    def authorize(token, requested_turn):
        with rt.db.session() as db:
            user, _ = IdentityService(rt, db).authenticate(token)
            require(user.status == "ACTIVE", "AUTH_UNAUTHORIZED", "账号不可用", 401)
            identity = IdentityService(rt, db, user.id)
            identity.require_consents("PRIVACY", "TERMS", "AI_PROCESSING", "MICROPHONE")
            svc = InterviewService(rt, db, user.id)
            row = svc.get(interview_id)
            turn = svc.current(row)
            provisional = rt.cache.get(f"live-turn:{user.id}:{requested_turn}")
            valid_provisional = (
                provisional and json.loads(rt.security.decrypt(provisional)).get("interview_id") == row.id
            )
            require(
                row.status == "IN_PROGRESS"
                and turn
                and ((turn.id == requested_turn and turn.status == "WAITING_ANSWER") or valid_provisional),
                "INTERVIEW_INVALID_STATE",
                "当前轮次不可采集字幕",
                409,
            )
            used = db.scalar(
                select(func.coalesce(func.sum(AICallLog.cost_microunits), 0)).where(
                    AICallLog.interview_id == row.id
                )
            )
            daily = db.scalar(
                select(func.coalesce(func.sum(AICallLog.cost_microunits), 0)).where(
                    AICallLog.created_at >= utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
                )
            )
            require(
                used < rt.settings.provider_cost_limit_microunits
                and daily < rt.settings.global_daily_cost_limit_microunits,
                "AI_COST_LIMIT",
                "AI 成本预算已用完",
                403,
            )
            return user.id, requested_turn, row.config_json["language"], used, daily

    async def receive_provider():
        try:
            while True:
                raw = await asyncio.to_thread(upstream.recv)
                require(
                    isinstance(raw, str) and len(raw) <= 200000, "AI_OUTPUT_INVALID", "实时转写响应无效", 502
                )
                event = json.loads(raw)
                kind = event.get("type", "")
                if kind == "session.updated":
                    await ws.send_json({"type": "ready"})
                elif kind in ("error", "conversation.item.input_audio_transcription.failed"):
                    raise BusinessError("LIVE_ASR_UNAVAILABLE", "实时转写暂不可用，将使用回答后的转写", 503)
                elif kind in (
                    "conversation.item.input_audio_transcription.text",
                    "conversation.item.input_audio_transcription.completed",
                ):
                    item = event.get("item_id", "current")
                    text = (
                        event.get("transcript", "")
                        if kind.endswith("completed")
                        else event.get("text", "") + event.get("stash", "")
                    )
                    require(
                        isinstance(text, str) and len(text) <= 20000, "AI_OUTPUT_INVALID", "字幕长度无效", 502
                    )
                    pieces[item] = text
                    if kind.endswith("completed"):
                        final_items.add(item)
                    combined = "".join(pieces.values())
                    require(len(combined) <= 20000, "AI_OUTPUT_INVALID", "字幕超过限制", 502)
                    await ws.send_json(
                        {"type": "caption", "text": combined, "final": len(final_items) == len(pieces)}
                    )
                elif kind == "session.finished":
                    completed.set()
                    return
        except Exception:
            failure.append(True)
            completed.set()

    try:
        require(bool(qwen_realtime.endpoint(rt.settings)), "LIVE_ASR_UNAVAILABLE", "服务未配置实时转写", 503)
        first = await asyncio.wait_for(ws.receive_json(), timeout=5)
        token = first.get("token", "")
        requested_turn = first.get("turn_id", "")
        require(
            isinstance(token, str) and len(token) <= 8192 and isinstance(requested_turn, str),
            "AUTH_UNAUTHORIZED",
            "实时字幕认证无效",
            401,
        )
        uid, turn_id, language, initial_cost, daily_cost = await asyncio.to_thread(
            authorize, token, requested_turn
        )
        require(rt.cache.hit(f"live-asr-open:{uid}", 20), "RATE_LIMITED", "实时转写请求过于频繁", 429)
        require(
            not rt.cache.get("provider:cooldown") and rt.cache.hit("provider:requests:minute", 1000),
            "AI_CIRCUIT_OPEN",
            "语音服务繁忙，稍后将自动补全字幕",
            503,
        )
        upstream = await asyncio.to_thread(qwen_realtime.connect, rt.settings, language)
        reader = asyncio.create_task(receive_provider())
        while True:
            message = await asyncio.wait_for(ws.receive(), timeout=30)
            if message["type"] == "websocket.disconnect":
                raise WebSocketDisconnect()
            data = message.get("bytes")
            if data is not None:
                require(
                    0 < len(data) <= 32768 and len(data) % 2 == 0, "MEDIA_INVALID_FORMAT", "实时音频格式无效"
                )
                pcm.extend(data)
                require(len(pcm) <= 180 * 32000, "MEDIA_TOO_LONG", "单轮回答最长 3 分钟")
                estimated = round(len(pcm) / 32000 / 60 * rt.settings.asr_cost_per_minute)
                require(
                    initial_cost + estimated < rt.settings.provider_cost_limit_microunits
                    and daily_cost + estimated < rt.settings.global_daily_cost_limit_microunits,
                    "AI_COST_LIMIT",
                    "AI 成本预算已用完",
                    403,
                )
                if time.monotonic() - last_check >= 5:
                    await asyncio.to_thread(authorize, token, requested_turn)
                    last_check = time.monotonic()
                await asyncio.to_thread(
                    upstream.send,
                    json.dumps(
                        {
                            "type": "input_audio_buffer.append",
                            "event_id": new_id(),
                            "audio": base64.b64encode(data).decode(),
                        }
                    ),
                )
            elif json.loads(message.get("text") or "{}").get("type") == "finish":
                await asyncio.to_thread(
                    upstream.send, json.dumps({"type": "session.finish", "event_id": new_id()})
                )
                await asyncio.wait_for(completed.wait(), timeout=10)
                require(not failure, "LIVE_ASR_UNAVAILABLE", "实时字幕连接中断，将使用回答后的转写", 503)
                await asyncio.to_thread(authorize, token, requested_turn)
                text = "".join(pieces.values())
                require(
                    text.strip() and len(final_items) == len(pieces),
                    "LIVE_ASR_UNAVAILABLE",
                    "实时转写尚未完成",
                    503,
                )
                proof_id = new_id()
                rt.cache.put(
                    f"live-asr:{uid}:{turn_id}:{proof_id}",
                    rt.security.encrypt(
                        json.dumps(
                            {"text": text, "sha256": hashlib.sha256(pcm).hexdigest()}, ensure_ascii=False
                        )
                    ),
                    600,
                )
                await ws.send_json({"type": "complete", "text": text, "transcript_id": proof_id})
                success = True
                break
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        with suppress(RuntimeError, WebSocketDisconnect):
            await ws.send_json(
                {
                    "type": "error",
                    "code": exc.code if isinstance(exc, BusinessError) else "LIVE_ASR_UNAVAILABLE",
                    "message": "实时字幕暂不可用，录制继续，回答后将自动补全字幕。",
                }
            )
    finally:
        if upstream:
            await asyncio.to_thread(upstream.close)
        if reader:
            reader.cancel()
            with suppress(asyncio.CancelledError):
                await reader
        if uid:
            with rt.db.session() as db:
                if db.get(User, uid):
                    duration = round(len(pcm) / 32)
                    db.add(
                        AICallLog(
                            user_id=uid,
                            interview_id=interview_id,
                            turn_id=turn_id if db.get(Turn, turn_id) else None,
                            provider="compatible",
                            model=rt.settings.realtime_asr_model,
                            call_type="asr",
                            latency_ms=round((time.monotonic() - started) * 1000),
                            status="SUCCEEDED" if success else "FAILED",
                            input_tokens=0,
                            output_tokens=0,
                            cost_microunits=round(duration * rt.settings.asr_cost_per_minute / 60000),
                            units={
                                "asr_duration_ms": duration,
                                "asr_provider": "qwen-realtime",
                                "cost_source": "configured_rate",
                            },
                        )
                    )
        with suppress(RuntimeError, WebSocketDisconnect):
            await ws.close()
