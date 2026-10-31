"""Qwen's synchronous ASR uses audio chat messages, not multipart transcriptions."""

import base64
from typing import Literal

from pydantic import BaseModel, Field

from app.core.errors import BusinessError


class _Message(BaseModel):
    role: Literal["assistant"]
    content: str


class _Choice(BaseModel):
    finish_reason: Literal["stop"]
    message: _Message


class _Usage(BaseModel):
    prompt_tokens: int = Field(ge=0, le=10**12, strict=True)
    completion_tokens: int = Field(ge=0, le=10**12, strict=True)


class _Response(BaseModel):
    choices: list[_Choice] = Field(min_length=1, max_length=1)
    usage: _Usage


class QwenASR:
    def __init__(self, request):
        self.request = request

    def transcribe(self, audio, model, language):
        response = self.request(
            "/chat/completions",
            json={
                "model": model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_audio",
                                "input_audio": {
                                    "data": "data:audio/wav;base64," + base64.b64encode(audio).decode()
                                },
                            }
                        ],
                    }
                ],
                "stream": False,
                "asr_options": {"language": language.split("-")[0], "enable_itn": False},
            },
        )
        try:
            return _Response.model_validate(response.json())
        except (ValueError, TypeError) as exc:
            raise BusinessError("AI_OUTPUT_INVALID", "语音识别响应未完整返回或结构无效", 502) from exc
