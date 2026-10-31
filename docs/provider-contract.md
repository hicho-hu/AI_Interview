# Provider 接入契约

## 可直接配置的兼容 Provider

`PROVIDER_MODE=compatible`，`PROVIDER_URL` 填含 `/v1` 的 base URL，配置 `PROVIDER_API_KEY`、`PROVIDER_MODEL`、`ASR_MODEL`、`TTS_MODEL`、`TTS_VOICE`。
适配器直接调用 Chat Completions JSON、multipart ASR、MP3 TTS。只支持 JSON 模式的模型可设置 `COMPATIBLE_JSON_SCHEMA=false`。
供应商须分别支持相应模态；更改 base URL 不保证厂商支持音频端点。

ASR 从本服务存储读对象，必要时提取单声道 16kHz WAV；模型不提供置信度时返回 null，不伪造置信度。
LLM 输出统一经过 Pydantic 和业务证据校验；结构无效、429、5xx 和连接失败受控重试。
兼容适配器生成报告时，先将回答拆成带编号的原文片段，再要求模型返回证据编号；服务端校验编号及逐题归属后还原为原文，原有报告 API 的 `evidence` 仍为逐字引文。未知编号、跨题引用和缺失证据的评分均会被拒绝。
报告调用仅由持久化任务层重试，避免任务与模型调用两层重试造成最多九次完整生成。校验失败日志包含原因及已知字段名，不记录回答、模型正文或任意上游字段。
使用 OpenAI Docs 技能核对协议：[Chat Completions](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)、[ASR](https://developers.openai.com/api/docs/guides/speech-to-text)、[TTS](https://developers.openai.com/api/docs/guides/text-to-speech)。

## Qwen3.8-Omni-Flash + Qwen-Audio-3.1-TTS-Flash

依赖已加入 `pyproject.toml` 和 `uv.lock`，执行 `uv sync --frozen --extra dev` 安装。将以下配置写入本地 `.env`，API Key 使用北京地域的环境变量 `DASHSCOPE_API_KEY`，也支持 `PROVIDER_API_KEY`（同时配置时优先使用后者）：

```dotenv
PROVIDER_MODE=compatible
PROVIDER_URL=https://ws-lds0gazpv5zfs7j1.cn-beijing.maas.aliyuncs.com/compatible-mode/v1
PROVIDER_MODEL=qwen3.8-omni-flash
ASR_PROVIDER=qwen
ASR_MODEL=qwen3-asr-flash
TTS_PROVIDER=dashscope
DASHSCOPE_WEBSOCKET_URL=wss://ws-lds0gazpv5zfs7j1.cn-beijing.maas.aliyuncs.com/api-ws/v1/inference
TTS_MODEL=qwen-audio-3.1-tts-flash
TTS_VOICE=longanhuan_v3.1
```

API Key 只写入被 Git 忽略的本地 `.env` 或注入运行环境。Compose 已将上述配置传入 API、Worker、Beat；保存配置后运行 `docker compose up -d --build` 会重建并应用。直接在本机运行时，API 和 Celery 进程也从项目根目录的 `.env` 读取同一配置，修改后须重启进程。

可在项目根目录运行 `.venv/bin/python -m scripts.verify_qwen`，用合成文本分别验证 Chat 和 TTS（会产生实际模型调用用量）。失败时运行 `.venv/bin/python -m scripts.verify_qwen --diagnose` 查看 HTTP 状态、供应商错误码及屏蔽密钥后的错误原因；脚本不会读取用户简历或录音，也不会输出密钥、模型正文或音频。

如果业务空间专属域名返回 `403 Workspace endpoint access denied`，可运行 `.venv/bin/python -m scripts.verify_qwen --diagnose-websocket` 检查 TTS 握手，并使用 `.venv/bin/python -m scripts.verify_qwen --shared-endpoint` 对照测试北京通用地址；此参数只临时覆盖验证进程的地址。2026-11-01 本机实测：所配置专属域名的 Chat 与 WebSocket 握手均返回 403，同一密钥通过以下北京通用地址完成了结构化文本和 MP3 生成。本机 `.env` 已切换为：

```dotenv
PROVIDER_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
DASHSCOPE_WEBSOCKET_URL=wss://dashscope.aliyuncs.com/api-ws/v1/inference
```

此结果针对上述两个模型的当前调用方式；业务空间域名的拒绝原因仍需在供应商侧确认。阿里云建议使用业务空间专属域名，通用域名自 2026-09-30 起不再支持新特性，未来启用其他能力时须重新核对支持范围。参见 [官方接入域名说明](https://help.aliyun.com/zh/model-studio/regions)。

Chat 适配器自动发送 `modalities=["text"]`、`stream=true`、`stream_options.include_usage=true`，拼接 `delta.content`，读取独立的 usage 分片，并要求正常结束；中断、超时和限流沿用现有重试机制。内部聚合完成后继续走 Pydantic 和业务证据校验，HTTP 业务接口仍返回原有契约。

该模型只输出文本，不发送 `audio` 参数。Qwen 不支持 JSON Schema 模式，适配器自动改用 `response_format={"type":"json_object"}`，并设置 `reasoning_effort="none"`；字段约束由系统提示词和服务端校验共同保证。参见 [Qwen Omni 官方文档](https://help.aliyun.com/zh/model-studio/qwen-omni)。

TTS 使用 DashScope `SpeechSynthesizer`，每次调用传入独立 WebSocket 地址和所配置的模型/音色，设置超时并关闭连接；显式请求 MP3，转换为现有 `audio_base64` / `audio/mpeg` 契约，再由业务层存储为私有题目音频。只有完整完成的任务及有效 Token 用量会被接受。

Qwen-Audio-3.1-TTS-Flash 按输入/输出 Token 计费，因此使用 `TTS_INPUT_COST_PER_MILLION`、`TTS_OUTPUT_COST_PER_MILLION` 设置费率，不使用字符费率；实际 Token 数来自 `task-finished` 的 `payload.usage`。参见 [官方服务端事件及计费用量](https://help.aliyun.com/en/model-studio/qwen-audio-tts-server-events)。

录音转写设置 `ASR_PROVIDER=qwen`、`ASR_MODEL=qwen3-asr-flash`，使用同一 `PROVIDER_URL` 和密钥，通过 `/chat/completions` 发送 `input_audio` 消息和内联 WAV Data URI；不调用千问未提供的 `/audio/transcriptions`。转码仍在本机完成，响应必须完整结束并通过 Pydantic 校验；保留实际 Token 用量和时长计费记录，不使用供应商返回的情绪等标注，不伪造置信度。协议见 [Qwen-ASR 官方文档](https://help.aliyun.com/zh/model-studio/qwen-asr-api-reference)。其他支持 multipart ASR 的供应商继续使用默认 `ASR_PROVIDER=compatible`。

可运行 `.venv/bin/python -m scripts.verify_qwen --asr`，用固定合成语音验证 TTS → 转码 → ASR 的真实链路（会产生模型调用用量）；脚本只输出状态、用量及预期短句是否识别成功。修改配置后重启 API 和 Worker，已失败的回答可通过 `POST /api/v1/tasks/{task_id}/retry` 使用原录音重试，无须重复上传。

## 自有 HTTP Gateway

`PROVIDER_MODE=http`，base URL **不含** `/v1`。需要实现以下服务端端点：

```
POST /v1/job、/v1/resume、/v1/plan、/v1/decision、/v1/report、/v1/asr、/v1/tts
Authorization: Bearer <PROVIDER_API_KEY>
{
  "model": "configured-model",
  "system_prompt": "...",
  "payload": {"...": "业务数据，均视为不可信输入"},
  "response_schema": {"...": "Pydantic JSON Schema"}
}
```

返回：

```json
{
  "data": {},
  "usage": {
    "input_tokens": 100,
    "output_tokens": 50,
    "cost_microunits": 123,
    "units": {"asr_duration_ms": 1000, "tts_characters": 20}
  }
}
```

`data` 对应 app/schemas/contracts.py 中结构。ASR 返回 text/confidence(可 null)/language/duration_ms；TTS 返回 audio_base64/mime_type（audio/mpeg）。
ASR 的 file_url 是短时签名地址，网关必须从其运行网络可达；同一 payload 的 object_key/mime_type 为辅助元数据。
建议网关实现内容安全过滤和供应商级实际账单计价；禁止把原始供应商错误/密钥返回客户端。

## 计价与成本

金额单位：订单为人民币分；AI 成本为人民币微元（1 元 = 1,000,000 微元）。网关需统一币种。
兼容 Provider 的成本按环境配置费率估算，不内置可能变化的厂商价格：

|环境变量|费率单位|
|---|---|
|LLM_INPUT_COST_PER_MILLION|每百万输入 Token 的微元|
|LLM_OUTPUT_COST_PER_MILLION|每百万输出 Token 的微元|
|ASR_COST_PER_MINUTE|每分钟微元|
|TTS_COST_PER_THOUSAND_CHARS|每千字符微元|
|TTS_INPUT_COST_PER_MILLION|DashScope TTS 每百万输入 Token 的微元|
|TTS_OUTPUT_COST_PER_MILLION|DashScope TTS 每百万输出 Token 的微元|

开发默认费率为 0，表示未校准，不能据此核算真实毛利。接入生产须按供应商当前账单设置。
预算为调用前软阈值：达到阈值拒绝下一次调用；正在进行的调用及并发调用可能超出阈值。实际硬预算应同时配置供应商账户限额。
对象存储直传下载的真实流量须从云存储访问日志/账单对账；签发下载 URL 不能当作真实已下载字节。

## 模拟 Provider

默认 `mock` 无网络、无费用。按 JD 原文生成演示问题、按长度演示追问；ASR 返回空文本/低置信度触发澄清；TTS 不制造假语音；报告分数为 null，明确显示无法判断。
本地自动化测试使用固定 SpeechFixture 验证 ASR/TTS 链路，不能作为真实语音质量验收。

## 报告任务状态

`GET /api/v1/interviews/{id}/report` 同时返回 `status`、`task_id`、`processing_status`、`retry_count`、`last_error_code`、`failure_code`、`retryable` 和 `report`。`FINISHING` 表示任务仍在执行或自动重试；`REPORT_FAILED` 表示重试耗尽，应停止“生成中”轮询并展示失败及重试入口。`retryable=true` 时可调用 `POST /api/v1/interviews/{id}/retry`，继续使用已保存的回答生成报告。
