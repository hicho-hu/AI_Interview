# Provider 接入契约

## 可直接配置的兼容 Provider

`PROVIDER_MODE=compatible`，`PROVIDER_URL` 填含 `/v1` 的 base URL，配置 `PROVIDER_API_KEY`、`PROVIDER_MODEL`、`ASR_MODEL`、`TTS_MODEL`、`TTS_VOICE`。
适配器直接调用 Chat Completions JSON、multipart ASR、MP3 TTS。只支持 JSON 模式的模型可设置 `COMPATIBLE_JSON_SCHEMA=false`。
供应商须分别支持相应模态；更改 base URL 不保证厂商支持音频端点。

ASR 从本服务存储读对象，必要时提取单声道 16kHz WAV；模型不提供置信度时返回 null，不伪造置信度。
LLM 输出统一经过 Pydantic 和业务证据校验；结构无效、429、5xx 和连接失败受控重试。
使用 OpenAI Docs 技能核对协议：[Chat Completions](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)、[ASR](https://developers.openai.com/api/docs/guides/speech-to-text)、[TTS](https://developers.openai.com/api/docs/guides/text-to-speech)。

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

开发默认费率为 0，表示未校准，不能据此核算真实毛利。接入生产须按供应商当前账单设置。
预算为调用前软阈值：达到阈值拒绝下一次调用；正在进行的调用及并发调用可能超出阈值。实际硬预算应同时配置供应商账户限额。
对象存储直传下载的真实流量须从云存储访问日志/账单对账；签发下载 URL 不能当作真实已下载字节。

## 模拟 Provider

默认 `mock` 无网络、无费用。按 JD 原文生成演示问题、按长度演示追问；ASR 返回空文本/低置信度触发澄清；TTS 不制造假语音；报告分数为 null，明确显示无法判断。
本地自动化测试使用固定 SpeechFixture 验证 ASR/TTS 链路，不能作为真实语音质量验收。
