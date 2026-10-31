"""OpenAI-compatible Chat Completions / audio protocols, isolated from business modules."""

import base64
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import httpx

from app.core.errors import BusinessError, require
from app.providers.ai import SYSTEM_PROMPT

INSTRUCTIONS = {
    "job": "仅根据 jd_text 结构化岗位职责、技能、经验和能力维度。保留原文事实。",
    "resume": "提取简历事实；summary 等不得引入原文没有的事实。evidence 必须是原文逐字片段。",
    "plan": "生成 config.question_count 道不同主问题，覆盖岗位能力。每题 jd_evidence 必须为 JD 原文子串。"
    "结合可选简历真实经历，resume_job_matches 仅陈述有证据的关联。考虑难度、面试类型和语言。",
    "decision": "分析当前问题和回答，输出 FOLLOW_UP/NEXT_QUESTION/CLARIFY。"
    "追问只深入一个缺口并提供 question，不要求编造业绩；优先覆盖岗位能力。",
    "report": "逐一点评所有 turns，reviews.turn_id 集合必须与 turns 完全一致。"
    "六个维度名称：岗位相关性、结构逻辑、专业深度、案例证据、问题解决、表达清晰度。"
    "每个有分数维度必须有回答原文 evidence，无证据 score=null、feedback 包含无法判断。"
    "overall_score 为非空维度分数的算术平均值，无有效分数则为 null。"
    "逐题 evidence 必须出自该题 answer。给出优势、差距、训练建议、练习题，不编造经历。",
}


class CompatibleProvider:
    def __init__(self, settings, storage):
        self.settings, self.storage = settings, storage

    def request(self, path, **kwargs):
        s = self.settings
        try:
            response = httpx.post(
                s.provider_url.rstrip("/") + path,
                headers={"Authorization": f"Bearer {s.provider_api_key}"},
                timeout=s.provider_timeout_seconds,
                follow_redirects=False,
                **kwargs,
            )
        except httpx.HTTPError as exc:
            raise BusinessError("AI_PROVIDER_BUSY", "AI 服务连接失败", 503) from exc
        require(response.status_code not in (429, 500, 502, 503, 504), "AI_PROVIDER_BUSY", "AI 服务繁忙", 503)
        require(not response.is_error, "AI_PROVIDER_REJECTED", "AI 服务拒绝请求，请检查模型/密钥配置", 502)
        return response

    def invoke(self, operation, payload, schema):
        s = self.settings
        usage: dict[str, Any] = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cost_microunits": 0,
            "units": {"cost_source": "configured_rate"},
        }
        if operation == "asr":
            audio = self.storage.read(payload["object_key"])
            if payload["mime_type"] not in ("audio/wav", "audio/x-wav") or len(audio) > 24 * 1024 * 1024:
                try:
                    with tempfile.TemporaryDirectory() as folder:
                        source, target = Path(folder) / "source", Path(folder) / "answer.wav"
                        source.write_bytes(audio)
                        subprocess.run(
                            [
                                "ffmpeg",
                                "-v",
                                "error",
                                "-i",
                                str(source),
                                "-vn",
                                "-ac",
                                "1",
                                "-ar",
                                "16000",
                                "-t",
                                "180",
                                str(target),
                            ],
                            check=True,
                            timeout=40,
                            capture_output=True,
                        )
                        audio = target.read_bytes()
                except (FileNotFoundError, subprocess.SubprocessError) as exc:
                    raise BusinessError(
                        "MEDIA_PROCESSOR_UNAVAILABLE", "音轨提取失败，请检查 ffmpeg", 503
                    ) from exc
            response = self.request(
                "/audio/transcriptions",
                files={"file": ("answer.wav", audio, "audio/wav")},
                data={
                    "model": s.asr_model,
                    "language": payload["language"].split("-")[0],
                    "response_format": "json",
                },
            )
            raw = response.json()
            data = {
                "text": raw["text"],
                "confidence": None,
                "language": payload["language"],
                "duration_ms": payload["duration_ms"],
            }
            usage["units"]["asr_duration_ms"] = payload["duration_ms"]
            usage["cost_microunits"] = round(payload["duration_ms"] * s.asr_cost_per_minute / 60000)
        elif operation == "tts":
            response = self.request(
                "/audio/speech",
                json={
                    "model": s.tts_model,
                    "voice": s.tts_voice,
                    "input": payload["text"],
                    "response_format": "mp3",
                },
            )
            data = {"audio_base64": base64.b64encode(response.content).decode(), "mime_type": "audio/mpeg"}
            usage["units"]["tts_characters"] = len(payload["text"])
            usage["cost_microunits"] = round(len(payload["text"]) * s.tts_cost_per_thousand_chars / 1000)
        else:
            require(operation in INSTRUCTIONS, "AI_NOT_SUPPORTED", "不支持的 AI 操作", 501)
            fmt = (
                {"type": "json_schema", "json_schema": {"name": operation, "schema": schema, "strict": False}}
                if s.compatible_json_schema
                else {"type": "json_object"}
            )
            raw = self.request(
                "/chat/completions",
                json={
                    "model": s.provider_model,
                    "messages": [
                        {
                            "role": "system",
                            "content": SYSTEM_PROMPT
                            + "\n"
                            + INSTRUCTIONS[operation]
                            + "\n输出 JSON Schema："
                            + json.dumps(schema, ensure_ascii=False),
                        },
                        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                    ],
                    "response_format": fmt,
                },
            ).json()
            data = json.loads(raw["choices"][0]["message"]["content"])
            usage["input_tokens"] = raw.get("usage", {}).get("prompt_tokens", 0)
            usage["output_tokens"] = raw.get("usage", {}).get("completion_tokens", 0)
            usage["cost_microunits"] = round(
                (
                    usage["input_tokens"] * s.llm_input_cost_per_million
                    + usage["output_tokens"] * s.llm_output_cost_per_million
                )
                / 1000000
            )
        return {"data": data, "usage": usage}
