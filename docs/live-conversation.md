# 连续面试的低延迟对话与字幕

`POST /api/v1/interviews/{id}/turns/{turn_id}/submit-stream` 接收与普通提交相同的 Answer 和 Idempotency-Key，先提交持久化任务，再立即执行。短租约防止普通 Outbox 抢先调度；API 异常退出后由既有 Worker 恢复。SSE 依次推送 accepted、transcribing、transcript、thinking、question、audio_reset、audio、audio_end、complete/deferred；音频为 24 kHz 单声道有符号 16 位小端 PCM 的 Base64。客户端无需等待 1.5 秒轮询或整句 TTS 合成完毕。断开连接不取消持久化回答任务；音频和提问在生成完成后正常保存为题目媒体资产，便于重放。

`WS /api/v1/interviews/{id}/live-transcription` 在首条 JSON 消息内接收 token 与 turn_id，不把凭据放进 URL。后续二进制消息为 16 kHz 单声道 16 位小端 PCM，单块最多 32 KB、单轮最多 3 分钟；`{"type":"finish"}` 结束本次采集。服务端核验身份、面试归属、当前轮次和麦克风/AI 授权，限制频率与费用预算，并定期重新核验授权。浏览器只收到 ready/caption/complete/error，不会收到供应商密钥，也不使用供应商情绪标签。转写完成后短期缓存加密的文本与 PCM SHA-256，返回 live_transcript_id。

提交回答时可附 live_transcript_id。服务端只有在用户、轮次、有效期及上传 WAV 的 PCM 哈希完全匹配时，才复用实时转写；不匹配或过期会使用原有 ASR。业务事实仍保存在数据库，缓存不决定题目状态或评分。追问未完全持久化期间可使用短期受限的下一轮信息开始字幕；提交答案仍须等对应轮次落库，异常回滚时不会把回答误绑到其他题。

DashScope TTS 在流式请求内输出 PCM，普通任务仍使用原来的完整 MP3 协议。流式音频同时收集为 WAV 保存，所有用量和失败通过既有 AI 日志记录。模型输出仍通过 Decision/SynthesizedAudio 校验；实时追问提示使用简短问句及摘要，报告仍使用原始回答证据。

`SESSION_CAPTIONS` 是整场字幕资产，类型为 text/vtt、扩展名 .vtt、UTF-8 WEBVTT 文件头，最大 1 MB。它与整场音视频一样按 owner 和 interview 鉴权，不关联单题。接口、资产和缓存增加均不涉及数据库结构变更。

配置：REALTIME_ASR_ENABLED（默认 true）、REALTIME_ASR_MODEL（默认 qwen3-asr-flash-realtime）、REALTIME_ASR_URL（WSS，可从现有 DashScope 地址推导）。仅在 compatible Provider + qwen ASR 下启用实时转写；其他配置保留回答后转写。部署需重载服务，网关须支持 WebSocket、SSE 并关闭代理缓冲。

延迟由静音检测、上传、模型推理、首包语音和网络共同决定，不承诺零毫秒。Qwen 实时 ASR 不提供逐字时间戳；当前字幕使用客户端记录的句段时间，候选人识别中的文本可能被后续结果修正。

验证：`pytest tests/test_live_conversation.py` 覆盖流式事件先后、重复提交、资源归属、实时字幕、匹配音频复用 ASR、错误音频回退和字幕文件上传；完整 pytest 与 Ruff 均须通过。

供应商协议参考：[实时 ASR 客户端事件](https://help.aliyun.com/en/model-studio/qwen-asr-realtime-client-events)、[实时 ASR 服务端事件](https://help.aliyun.com/en/model-studio/qwen-asr-realtime-server-events)。
