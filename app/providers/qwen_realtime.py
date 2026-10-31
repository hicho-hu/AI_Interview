"""Qwen-ASR-Realtime transport. Only this server receives the provider API key."""

import json
from urllib.parse import urlencode, urlsplit, urlunsplit

from websocket import create_connection


def endpoint(settings):
    if (
        not settings.realtime_asr_enabled
        or settings.provider_mode != "compatible"
        or settings.asr_provider != "qwen"
    ):
        return ""
    if settings.realtime_asr_url:
        return settings.realtime_asr_url
    parsed = urlsplit(settings.dashscope_websocket_url)
    if parsed.scheme == "wss" and parsed.hostname:
        return urlunsplit(("wss", parsed.netloc, "/api-ws/v1/realtime", "", ""))
    parsed = urlsplit(settings.provider_url)
    if parsed.scheme == "https" and parsed.hostname and "dashscope" in parsed.hostname:
        return urlunsplit(("wss", parsed.netloc, "/api-ws/v1/realtime", "", ""))
    return ""


def connect(settings, language):
    url = endpoint(settings)
    ws = create_connection(
        url + ("&" if "?" in url else "?") + urlencode({"model": settings.realtime_asr_model}),
        header={"Authorization": f"Bearer {settings.provider_api_key}"},
        timeout=10,
        enable_multithread=True,
    )
    ws.send(
        json.dumps(
            {
                "type": "session.update",
                "event_id": "configure",
                "session": {
                    "input_audio_format": "pcm",
                    "sample_rate": 16000,
                    "input_audio_transcription": {"language": language.split("-")[0]},
                    "turn_detection": {"type": "server_vad", "threshold": 0.0, "silence_duration_ms": 400},
                },
            }
        )
    )
    return ws
