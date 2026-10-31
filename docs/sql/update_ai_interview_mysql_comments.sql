-- AI 模拟面试服务：现有数据库字段/表注释增量更新 SQL
-- 适用结构：Alembic f4c8a1d2e903；执行后登记为 3b9e7c2a4106。
-- 仅修改 COMMENT，不删除表、不改变业务记录，不改字段类型、可空性、默认值、索引或外键。
-- MODIFY COLUMN 需要完整字段定义，本文件保留原结构定义；不会使用业务数据 UPDATE。
-- 最后一条 UPDATE 仅同步 Alembic 迁移版本标记；同版本可重复执行。
-- 示例：mysql --default-character-set=utf8mb4 -h 127.0.0.1 -u root AI_Interview < docs/sql/update_ai_interview_mysql_comments.sql
-- 若额外自定义过字段类型/默认值，使用 alembic upgrade head（自动反射保留），或 --live-schema 重新生成。
-- 与 alembic upgrade head 选择一种执行；MySQL DDL 隐式提交，不保证整份脚本可整体回滚。

SET NAMES utf8mb4;

ALTER TABLE `users`
    MODIFY COLUMN id VARCHAR(36) NOT NULL COMMENT '记录唯一标识（UUID，由应用生成）',
    MODIFY COLUMN created_at DATETIME(6) NOT NULL COMMENT '记录创建时间（UTC，精确到微秒，由应用写入）',
    MODIFY COLUMN updated_at DATETIME(6) NOT NULL COMMENT '记录最近更新时间（UTC，由应用在修改记录时刷新）',
    MODIFY COLUMN openid_hash VARCHAR(64) NOT NULL COMMENT '登录身份的 HMAC-SHA256 哈希，不保存明文微信 OpenID',
    MODIFY COLUMN nickname VARCHAR(80) NOT NULL COMMENT '用户显示昵称',
    MODIFY COLUMN status VARCHAR(24) NOT NULL COMMENT '账号状态：ACTIVE=正常，DELETING=删除中，DELETED=已删除',
    MODIFY COLUMN credits INTEGER NOT NULL COMMENT '当前可用面试次数；创建面试时扣减预占，未消费释放时归还',
    COMMENT = '用户账号、昵称、账号状态及剩余面试额度';

ALTER TABLE `user_consents`
    MODIFY COLUMN id VARCHAR(36) NOT NULL COMMENT '记录唯一标识（UUID，由应用生成）',
    MODIFY COLUMN created_at DATETIME(6) NOT NULL COMMENT '记录创建时间（UTC，精确到微秒，由应用写入）',
    MODIFY COLUMN updated_at DATETIME(6) NOT NULL COMMENT '记录最近更新时间（UTC，由应用在修改记录时刷新）',
    MODIFY COLUMN user_id VARCHAR(36) NOT NULL COMMENT '所属用户 ID，关联 users.id，用于数据归属校验',
    MODIFY COLUMN consent_type VARCHAR(30) NOT NULL COMMENT '授权类型：PRIVACY=隐私，TERMS=协议，AI_PROCESSING=AI 处理，MICROPHONE=麦克风，CAMERA=摄像头',
    MODIFY COLUMN policy_version VARCHAR(30) NOT NULL COMMENT '用户同意的隐私政策或协议版本号',
    MODIFY COLUMN revoked_at DATETIME(6) COMMENT '撤销授权时间（UTC）；为空表示尚未撤销',
    COMMENT = '用户隐私、协议、AI 处理及录音录像授权记录';

ALTER TABLE `revoked_tokens`
    MODIFY COLUMN id VARCHAR(36) NOT NULL COMMENT '记录唯一标识（UUID，由应用生成）',
    MODIFY COLUMN created_at DATETIME(6) NOT NULL COMMENT '记录创建时间（UTC，精确到微秒，由应用写入）',
    MODIFY COLUMN updated_at DATETIME(6) NOT NULL COMMENT '记录最近更新时间（UTC，由应用在修改记录时刷新）',
    MODIFY COLUMN jti VARCHAR(36) NOT NULL COMMENT '被撤销 JWT 的唯一标识，用于拦截该令牌',
    MODIFY COLUMN expires_at DATETIME(6) NOT NULL COMMENT '原登录令牌的到期时间（UTC），到期后可清理撤销记录',
    COMMENT = '已撤销的登录令牌，用于退出后禁止旧令牌继续访问';

ALTER TABLE `job_profiles`
    MODIFY COLUMN id VARCHAR(36) NOT NULL COMMENT '记录唯一标识（UUID，由应用生成）',
    MODIFY COLUMN created_at DATETIME(6) NOT NULL COMMENT '记录创建时间（UTC，精确到微秒，由应用写入）',
    MODIFY COLUMN updated_at DATETIME(6) NOT NULL COMMENT '记录最近更新时间（UTC，由应用在修改记录时刷新）',
    MODIFY COLUMN user_id VARCHAR(36) NOT NULL COMMENT '所属用户 ID，关联 users.id，用于数据归属校验',
    MODIFY COLUMN job_title VARCHAR(120) NOT NULL COMMENT '目标招聘岗位名称，例如 Python 后端工程师',
    MODIFY COLUMN seniority VARCHAR(30) NOT NULL COMMENT '岗位级别：JUNIOR=初级，MIDDLE=中级，SENIOR=高级，EXPERT=专家',
    MODIFY COLUMN jd_encrypted LONGTEXT NOT NULL COMMENT '加密保存的招聘岗位描述 JD 原文',
    MODIFY COLUMN profile_encrypted LONGTEXT COMMENT '加密岗位画像 JSON，包含职责、技能、经验要求及能力维度',
    MODIFY COLUMN status VARCHAR(24) NOT NULL COMMENT '岗位解析状态：PREPARING=处理中，READY=就绪，FAILED=失败，DELETED=已删除',
    MODIFY COLUMN task_id VARCHAR(36) COMMENT '岗位解析异步任务 ID，对应 outbox_events.id；任务创建前可为空',
    COMMENT = '目标岗位 JD、岗位级别及 AI 解析出的结构化岗位画像';

ALTER TABLE `media_assets`
    MODIFY COLUMN id VARCHAR(36) NOT NULL COMMENT '记录唯一标识（UUID，由应用生成）',
    MODIFY COLUMN created_at DATETIME(6) NOT NULL COMMENT '记录创建时间（UTC，精确到微秒，由应用写入）',
    MODIFY COLUMN updated_at DATETIME(6) NOT NULL COMMENT '记录最近更新时间（UTC，由应用在修改记录时刷新）',
    MODIFY COLUMN user_id VARCHAR(36) NOT NULL COMMENT '所属用户 ID，关联 users.id，用于数据归属校验',
    MODIFY COLUMN interview_id VARCHAR(36) COMMENT '关联面试 ID；独立简历或 JD 图片可为空',
    MODIFY COLUMN turn_id VARCHAR(36) COMMENT '关联面试轮次 ID；独立文件可为空，分段录像共用同一轮次',
    MODIFY COLUMN media_type VARCHAR(24) NOT NULL COMMENT '媒体用途：AUDIO=回答录音，VIDEO=回答录像，RESUME=简历，JD_IMAGE=岗位图片，TTS=提问语音',
    MODIFY COLUMN object_key VARCHAR(500) NOT NULL COMMENT '私有对象存储中的唯一文件键，不是永久公开下载地址',
    MODIFY COLUMN filename VARCHAR(200) NOT NULL COMMENT '上传或生成文件的名称，包含文件扩展名',
    MODIFY COLUMN mime_type VARCHAR(120) NOT NULL COMMENT '文件 MIME 类型，例如 audio/wav、video/mp4、application/pdf',
    MODIFY COLUMN size_bytes BIGINT NOT NULL COMMENT '文件大小，单位字节；完成上传时与实际对象大小核对',
    MODIFY COLUMN duration_ms INTEGER COMMENT '音视频实际时长，单位毫秒；非音视频文件可为空',
    MODIFY COLUMN checksum VARCHAR(64) COMMENT '文件 SHA-256 校验值，用于内容完整性核验',
    MODIFY COLUMN etag VARCHAR(128) COMMENT '对象存储返回的 ETag，用于上传校验和条件复制',
    MODIFY COLUMN status VARCHAR(24) NOT NULL COMMENT '媒体状态：PENDING_UPLOAD=待上传，READY=可用，DELETING=删除中，DELETED=已删除；过期可由 expires_at 判断',
    MODIFY COLUMN expires_at DATETIME(6) NOT NULL COMMENT '文件保留截止时间（UTC），到期禁止访问并安排清理',
    MODIFY COLUMN deleted_at DATETIME(6) COMMENT '文件删除完成时间（UTC）；未删除时为空',
    COMMENT = '简历、录音、录像及提问语音的对象存储元数据，不保存文件二进制';

ALTER TABLE `resume_assets`
    MODIFY COLUMN id VARCHAR(36) NOT NULL COMMENT '记录唯一标识（UUID，由应用生成）',
    MODIFY COLUMN created_at DATETIME(6) NOT NULL COMMENT '记录创建时间（UTC，精确到微秒，由应用写入）',
    MODIFY COLUMN updated_at DATETIME(6) NOT NULL COMMENT '记录最近更新时间（UTC，由应用在修改记录时刷新）',
    MODIFY COLUMN user_id VARCHAR(36) NOT NULL COMMENT '所属用户 ID，关联 users.id，用于数据归属校验',
    MODIFY COLUMN asset_id VARCHAR(36) NOT NULL COMMENT '简历原始文件资产 ID，关联 media_assets.id，一份文件对应一份解析记录',
    MODIFY COLUMN parse_status VARCHAR(24) NOT NULL COMMENT '简历解析状态：PENDING_UPLOAD=待上传，PROCESSING=处理中，READY=就绪，FAILED=失败，DELETED=已删除',
    MODIFY COLUMN text_encrypted LONGTEXT COMMENT '从 PDF/DOCX 提取的简历文字原文，采用加密存储',
    MODIFY COLUMN summary_encrypted LONGTEXT COMMENT '加密结构化简历 JSON，含教育、工作、项目、技能及原文证据',
    MODIFY COLUMN task_id VARCHAR(36) COMMENT '简历解析异步任务 ID，关联 outbox_events.id',
    COMMENT = '简历文件关联、解析状态、加密原文和结构化简历摘要';

ALTER TABLE `interviews`
    MODIFY COLUMN id VARCHAR(36) NOT NULL COMMENT '记录唯一标识（UUID，由应用生成）',
    MODIFY COLUMN created_at DATETIME(6) NOT NULL COMMENT '记录创建时间（UTC，精确到微秒，由应用写入）',
    MODIFY COLUMN updated_at DATETIME(6) NOT NULL COMMENT '记录最近更新时间（UTC，由应用在修改记录时刷新）',
    MODIFY COLUMN user_id VARCHAR(36) NOT NULL COMMENT '所属用户 ID，关联 users.id，用于数据归属校验',
    MODIFY COLUMN job_profile_id VARCHAR(36) NOT NULL COMMENT '目标岗位档案 ID，关联 job_profiles.id',
    MODIFY COLUMN resume_asset_id VARCHAR(36) COMMENT '可选简历解析记录 ID，关联 resume_assets.id',
    MODIFY COLUMN status VARCHAR(24) NOT NULL COMMENT '面试状态：DRAFT=草稿，PREPARING=准备中，READY=待开始，IN_PROGRESS=进行中，PAUSED=暂停，FINISHING=报告生成中，COMPLETED=完成，FAILED=准备失败，REPORT_FAILED=报告失败，CANCELLED=取消',
    MODIFY COLUMN mode VARCHAR(16) NOT NULL COMMENT '面试模式：TEXT=文字，VOICE=语音，VIDEO=视频',
    MODIFY COLUMN config_json JSON NOT NULL COMMENT '面试配置 JSON，含时长、难度、题数、语言、面试类型和追问/澄清上限',
    MODIFY COLUMN current_turn_no INTEGER NOT NULL COMMENT '当前轮次序号，从 1 开始；首题生成前为 0',
    MODIFY COLUMN version INTEGER NOT NULL COMMENT '乐观锁版本号；状态或轮次更新时递增，防止并发覆盖',
    MODIFY COLUMN started_at DATETIME(6) COMMENT '面试正式开始时间（UTC）；未开始时为空',
    MODIFY COLUMN ended_at DATETIME(6) COMMENT '面试结束或取消时间（UTC）；未结束时为空',
    MODIFY COLUMN paused_at DATETIME(6) COMMENT '本次暂停开始时间（UTC）；未暂停或恢复后为空',
    MODIFY COLUMN paused_seconds INTEGER NOT NULL COMMENT '累计暂停时长，单位秒；计算有效面试时间时扣除',
    MODIFY COLUMN prepare_task_id VARCHAR(36) COMMENT '面试计划与首题准备任务 ID，关联 outbox_events.id',
    MODIFY COLUMN report_task_id VARCHAR(36) COMMENT '最近一次报告生成任务 ID，关联 outbox_events.id',
    MODIFY COLUMN failure_code VARCHAR(80) COMMENT '最近一次准备或报告失败的业务错误码；成功后清空',
    MODIFY COLUMN deleted_at DATETIME(6) COMMENT '面试记录软删除时间（UTC）；未删除时为空',
    COMMENT = '一次模拟面试的配置、状态、当前轮次及起止时间';

ALTER TABLE `interview_plans`
    MODIFY COLUMN id VARCHAR(36) NOT NULL COMMENT '记录唯一标识（UUID，由应用生成）',
    MODIFY COLUMN created_at DATETIME(6) NOT NULL COMMENT '记录创建时间（UTC，精确到微秒，由应用写入）',
    MODIFY COLUMN updated_at DATETIME(6) NOT NULL COMMENT '记录最近更新时间（UTC，由应用在修改记录时刷新）',
    MODIFY COLUMN interview_id VARCHAR(36) NOT NULL COMMENT '所属面试 ID，关联 interviews.id；每场面试一份计划',
    MODIFY COLUMN plan_encrypted LONGTEXT NOT NULL COMMENT '加密面试计划 JSON，含主问题、考察维度、JD 证据及简历关联',
    MODIFY COLUMN prompt_version VARCHAR(30) NOT NULL COMMENT '生成计划使用的提示词版本号',
    MODIFY COLUMN model_name VARCHAR(100) NOT NULL COMMENT '生成计划使用的模型名称；mock 表示开发模拟供应商',
    COMMENT = '结合岗位、简历和配置生成的个性化面试计划';

ALTER TABLE `interview_turns`
    MODIFY COLUMN id VARCHAR(36) NOT NULL COMMENT '记录唯一标识（UUID，由应用生成）',
    MODIFY COLUMN created_at DATETIME(6) NOT NULL COMMENT '记录创建时间（UTC，精确到微秒，由应用写入）',
    MODIFY COLUMN updated_at DATETIME(6) NOT NULL COMMENT '记录最近更新时间（UTC，由应用在修改记录时刷新）',
    MODIFY COLUMN interview_id VARCHAR(36) NOT NULL COMMENT '所属面试 ID，关联 interviews.id',
    MODIFY COLUMN turn_no INTEGER NOT NULL COMMENT '场内轮次序号，从 1 开始；与 interview_id 组合唯一',
    MODIFY COLUMN parent_turn_id VARCHAR(36) COMMENT '被追问或澄清的上一轮 ID；主问题为空',
    MODIFY COLUMN turn_type VARCHAR(24) NOT NULL COMMENT '题目类型：QUESTION=主问题，FOLLOW_UP=追问，CLARIFICATION=澄清',
    MODIFY COLUMN main_index INTEGER NOT NULL COMMENT '对应主问题在计划中的下标，从 0 开始；追问和澄清沿用原主问题下标',
    MODIFY COLUMN competency VARCHAR(120) NOT NULL COMMENT '本轮问题考察的岗位能力维度',
    MODIFY COLUMN question_encrypted LONGTEXT NOT NULL COMMENT '加密保存的 AI 提问文本',
    MODIFY COLUMN question_asset_id VARCHAR(36) COMMENT 'AI 提问 TTS 语音资产 ID，关联 media_assets.id；文字模式可为空',
    MODIFY COLUMN answer_asset_id VARCHAR(36) COMMENT '回答的首段音视频资产 ID；文字回答为空，多段完整列表记录在任务载荷中',
    MODIFY COLUMN transcript_encrypted LONGTEXT COMMENT '加密回答文本：用户文字输入或 ASR 语音转写，多段回答按顺序合并',
    MODIFY COLUMN confidence JSON COMMENT 'ASR 置信度（0～1），文字回答为 1；供应商未提供置信度时为 null',
    MODIFY COLUMN decision_encrypted LONGTEXT COMMENT '加密 AI 决策 JSON，含追问/下一题/澄清/结束、原因和回答摘要',
    MODIFY COLUMN status VARCHAR(24) NOT NULL COMMENT '轮次状态：WAITING_ANSWER=待回答，SUBMITTED=已提交待处理，COMPLETED=处理完成，FAILED=处理失败，DELETED=已删除',
    MODIFY COLUMN task_id VARCHAR(36) COMMENT '回答转写与分析任务 ID，关联 outbox_events.id',
    COMMENT = '面试主问题、追问、澄清及对应回答和 AI 决策';

ALTER TABLE `interview_reports`
    MODIFY COLUMN id VARCHAR(36) NOT NULL COMMENT '记录唯一标识（UUID，由应用生成）',
    MODIFY COLUMN created_at DATETIME(6) NOT NULL COMMENT '记录创建时间（UTC，精确到微秒，由应用写入）',
    MODIFY COLUMN updated_at DATETIME(6) NOT NULL COMMENT '记录最近更新时间（UTC，由应用在修改记录时刷新）',
    MODIFY COLUMN interview_id VARCHAR(36) NOT NULL COMMENT '所属面试 ID，关联 interviews.id',
    MODIFY COLUMN report_version INTEGER NOT NULL COMMENT '报告版本号，从 1 递增；重新生成保留旧版本',
    MODIFY COLUMN overall_score JSON COMMENT '综合得分（0～100）；证据不足或模拟环境时为 JSON null',
    MODIFY COLUMN content_encrypted LONGTEXT NOT NULL COMMENT '加密完整报告 JSON，含维度评分、逐题证据、建议和原始轮次快照',
    MODIFY COLUMN prompt_version VARCHAR(30) NOT NULL COMMENT '生成报告使用的提示词与评分规则版本号',
    MODIFY COLUMN model_name VARCHAR(100) NOT NULL COMMENT '生成报告使用的模型名称；mock 表示开发模拟供应商',
    COMMENT = '按版本保存的面试评估报告及综合评分';

ALTER TABLE `ai_call_logs`
    MODIFY COLUMN id VARCHAR(36) NOT NULL COMMENT '记录唯一标识（UUID，由应用生成）',
    MODIFY COLUMN created_at DATETIME(6) NOT NULL COMMENT '记录创建时间（UTC，精确到微秒，由应用写入）',
    MODIFY COLUMN updated_at DATETIME(6) NOT NULL COMMENT '记录最近更新时间（UTC，由应用在修改记录时刷新）',
    MODIFY COLUMN user_id VARCHAR(36) NOT NULL COMMENT '所属用户 ID，关联 users.id，用于数据归属校验',
    MODIFY COLUMN interview_id VARCHAR(36) COMMENT '关联面试 ID；独立岗位或简历解析调用可为空',
    MODIFY COLUMN turn_id VARCHAR(36) COMMENT '关联轮次 ID；非逐题调用或新轮次回滚时可为空',
    MODIFY COLUMN provider VARCHAR(40) NOT NULL COMMENT 'AI 适配器类型，例如 mock、http、compatible',
    MODIFY COLUMN model VARCHAR(100) NOT NULL COMMENT '本次调用使用的实际模型名称',
    MODIFY COLUMN call_type VARCHAR(30) NOT NULL COMMENT '调用用途：job=岗位解析，resume=简历解析，plan=计划，decision=追问决策，report=报告，asr=转写，tts=语音合成',
    MODIFY COLUMN input_tokens INTEGER NOT NULL COMMENT '本次调用的输入 Token 数；供应商未计量时为 0',
    MODIFY COLUMN output_tokens INTEGER NOT NULL COMMENT '本次调用的输出 Token 数；供应商未计量时为 0',
    MODIFY COLUMN latency_ms INTEGER NOT NULL COMMENT '本次供应商调用耗时，单位毫秒',
    MODIFY COLUMN cost_microunits BIGINT NOT NULL COMMENT 'AI 调用成本，单位人民币微元（1 元=1000000 微元）；按网关账单或配置费率计价',
    MODIFY COLUMN units JSON COMMENT '额外用量 JSON，可含 ASR 毫秒数、TTS 字符数及成本来源',
    MODIFY COLUMN status VARCHAR(24) NOT NULL COMMENT '调用结果：SUCCEEDED=成功，FAILED=失败；重试逐次记录',
    MODIFY COLUMN request_id VARCHAR(80) COMMENT '触发此次调用的 HTTP 请求追踪 ID',
    COMMENT = 'AI 调用的供应商、模型、用量、延迟、结果和成本记录';

ALTER TABLE `usage_ledger`
    MODIFY COLUMN id VARCHAR(36) NOT NULL COMMENT '记录唯一标识（UUID，由应用生成）',
    MODIFY COLUMN created_at DATETIME(6) NOT NULL COMMENT '记录创建时间（UTC，精确到微秒，由应用写入）',
    MODIFY COLUMN updated_at DATETIME(6) NOT NULL COMMENT '记录最近更新时间（UTC，由应用在修改记录时刷新）',
    MODIFY COLUMN user_id VARCHAR(36) NOT NULL COMMENT '所属用户 ID，关联 users.id，用于数据归属校验',
    MODIFY COLUMN interview_id VARCHAR(36) COMMENT '关联面试 ID；注册赠送、购买或退款可为空',
    MODIFY COLUMN usage_type VARCHAR(30) NOT NULL COMMENT '账本类型：WELCOME=注册赠送，INTERVIEW=面试额度，PURCHASE=购买，REFUND=退款',
    MODIFY COLUMN quantity INTEGER NOT NULL COMMENT '额度变动次数；增加为正、扣减为负，预占释放由 status 表示',
    MODIFY COLUMN amount_minor BIGINT NOT NULL COMMENT '对应金额，单位人民币分；购买为正、退款为负，无金额变动为 0',
    MODIFY COLUMN status VARCHAR(24) NOT NULL COMMENT '额度状态：GRANTED=已发放，RESERVED=已预占，CONSUMED=已消费，RELEASED=已释放，REFUNDED=已退款',
    MODIFY COLUMN idempotency_key VARCHAR(200) NOT NULL COMMENT '全局唯一账本幂等键，用于防止重复发放、扣费或退款',
    COMMENT = '面试额度预占、消费、释放、购买及退款账本';

ALTER TABLE `orders`
    MODIFY COLUMN id VARCHAR(36) NOT NULL COMMENT '记录唯一标识（UUID，由应用生成）',
    MODIFY COLUMN created_at DATETIME(6) NOT NULL COMMENT '记录创建时间（UTC，精确到微秒，由应用写入）',
    MODIFY COLUMN updated_at DATETIME(6) NOT NULL COMMENT '记录最近更新时间（UTC，由应用在修改记录时刷新）',
    MODIFY COLUMN user_id VARCHAR(36) NOT NULL COMMENT '所属用户 ID，关联 users.id，用于数据归属校验',
    MODIFY COLUMN product_code VARCHAR(40) NOT NULL COMMENT '产品编码：SINGLE=单次，PACK_10=10 次套餐，MONTHLY=20 次月度额度包',
    MODIFY COLUMN amount_minor INTEGER NOT NULL COMMENT '订单应付金额，单位人民币分',
    MODIFY COLUMN credits INTEGER NOT NULL COMMENT '订单支付成功后发放的面试次数',
    MODIFY COLUMN currency VARCHAR(3) NOT NULL COMMENT '订单币种代码，当前为 CNY（人民币）',
    MODIFY COLUMN status VARCHAR(24) NOT NULL COMMENT '订单状态：PENDING=待支付，PAID=已支付，REFUNDED=已全额退款',
    MODIFY COLUMN provider_trade_no VARCHAR(120) COMMENT '支付供应商唯一交易流水号；未支付时为空，开发支付使用 dev 前缀',
    MODIFY COLUMN paid_at DATETIME(6) COMMENT '支付成功时间（UTC）；未支付时为空',
    MODIFY COLUMN refunded_at DATETIME(6) COMMENT '全额退款完成时间（UTC）；未退款时为空',
    COMMENT = '训练额度套餐订单、支付状态及退款信息';

ALTER TABLE `outbox_events`
    MODIFY COLUMN id VARCHAR(36) NOT NULL COMMENT '记录唯一标识（UUID，由应用生成）',
    MODIFY COLUMN created_at DATETIME(6) NOT NULL COMMENT '记录创建时间（UTC，精确到微秒，由应用写入）',
    MODIFY COLUMN updated_at DATETIME(6) NOT NULL COMMENT '记录最近更新时间（UTC，由应用在修改记录时刷新）',
    MODIFY COLUMN user_id VARCHAR(36) NOT NULL COMMENT '所属用户 ID，关联 users.id，用于数据归属校验',
    MODIFY COLUMN event_type VARCHAR(40) NOT NULL COMMENT '任务类型，例如 PARSE_JOB、PARSE_RESUME、PREPARE_INTERVIEW、PROCESS_ANSWER、GENERATE_REPORT、DELETE_MEDIA、DELETE_USER',
    MODIFY COLUMN aggregate_id VARCHAR(36) NOT NULL COMMENT '本任务操作的业务对象 ID，具体含义由 event_type 决定',
    MODIFY COLUMN payload_encrypted LONGTEXT COMMENT '加密任务输入 JSON，例如回答分段资产列表或报告冻结快照',
    MODIFY COLUMN result_encrypted LONGTEXT COMMENT '加密任务结果 JSON；任务成功前可为空',
    MODIFY COLUMN status VARCHAR(24) NOT NULL COMMENT '任务状态：PENDING=待处理，QUEUED=已入队，RUNNING=执行中，RETRYING=等待重试，SUCCEEDED=成功，FAILED=失败，CANCELLED=取消',
    MODIFY COLUMN retry_count INTEGER NOT NULL COMMENT '当前处理周期内已失败次数，用于退避和判断重试上限',
    MODIFY COLUMN available_at DATETIME(6) NOT NULL COMMENT '任务最早可执行时间（UTC），延迟重试时向后推迟',
    MODIFY COLUMN lease_until DATETIME(6) COMMENT '派发或执行租约到期时间（UTC），过期后可重新领取',
    MODIFY COLUMN lease_token VARCHAR(36) COMMENT '执行租约唯一令牌，用于防止并发重复领取和过期执行者写回',
    MODIFY COLUMN error_code VARCHAR(80) COMMENT '最近一次任务失败的业务错误码',
    MODIFY COLUMN request_id VARCHAR(80) COMMENT '创建任务的 HTTP 请求追踪 ID；定时任务可为空',
    COMMENT = '事务 Outbox 与异步任务，支持可靠派发、租约和失败重试';

ALTER TABLE `idempotency_records`
    MODIFY COLUMN id VARCHAR(36) NOT NULL COMMENT '记录唯一标识（UUID，由应用生成）',
    MODIFY COLUMN created_at DATETIME(6) NOT NULL COMMENT '记录创建时间（UTC，精确到微秒，由应用写入）',
    MODIFY COLUMN updated_at DATETIME(6) NOT NULL COMMENT '记录最近更新时间（UTC，由应用在修改记录时刷新）',
    MODIFY COLUMN user_id VARCHAR(36) NOT NULL COMMENT '所属用户 ID，关联 users.id，用于数据归属校验',
    MODIFY COLUMN scope VARCHAR(200) NOT NULL COMMENT '幂等操作范围，区分创建面试、提交回答、结束、订单等操作',
    MODIFY COLUMN `key` VARCHAR(120) NOT NULL COMMENT '客户端 Idempotency-Key；与 user_id、scope 组合唯一，区分大小写',
    MODIFY COLUMN request_hash VARCHAR(64) NOT NULL COMMENT '规范化请求内容的 SHA-256 摘要，识别同键不同请求',
    MODIFY COLUMN response_encrypted LONGTEXT NOT NULL COMMENT '加密原始响应 JSON；重复请求直接返回该结果',
    COMMENT = '关键写操作的幂等键、请求摘要及原始响应快照';

ALTER TABLE `audit_logs`
    MODIFY COLUMN id VARCHAR(36) NOT NULL COMMENT '记录唯一标识（UUID，由应用生成）',
    MODIFY COLUMN created_at DATETIME(6) NOT NULL COMMENT '记录创建时间（UTC，精确到微秒，由应用写入）',
    MODIFY COLUMN updated_at DATETIME(6) NOT NULL COMMENT '记录最近更新时间（UTC，由应用在修改记录时刷新）',
    MODIFY COLUMN user_id VARCHAR(36) NOT NULL COMMENT '所属用户 ID，关联 users.id，用于数据归属校验',
    MODIFY COLUMN action VARCHAR(60) NOT NULL COMMENT '操作标识，例如授权、创建面试、提交回答、删除数据等审计事件',
    MODIFY COLUMN resource_id VARCHAR(120) NOT NULL COMMENT '操作涉及的资源标识；通常为业务记录 ID，也可为授权类型',
    MODIFY COLUMN request_id VARCHAR(80) COMMENT '本次操作的 HTTP 请求追踪 ID',
    COMMENT = '用户或管理员关键操作的审计记录，不保存敏感业务原文';

ALTER TABLE `feedback`
    MODIFY COLUMN id VARCHAR(36) NOT NULL COMMENT '记录唯一标识（UUID，由应用生成）',
    MODIFY COLUMN created_at DATETIME(6) NOT NULL COMMENT '记录创建时间（UTC，精确到微秒，由应用写入）',
    MODIFY COLUMN updated_at DATETIME(6) NOT NULL COMMENT '记录最近更新时间（UTC，由应用在修改记录时刷新）',
    MODIFY COLUMN user_id VARCHAR(36) NOT NULL COMMENT '所属用户 ID，关联 users.id，用于数据归属校验',
    MODIFY COLUMN interview_id VARCHAR(36) COMMENT '反馈涉及的面试 ID；通用反馈可为空',
    MODIFY COLUMN category VARCHAR(30) NOT NULL COMMENT '反馈类型：CONTENT=内容问题，REPORT=报告纠错，PRIVACY=隐私，OTHER=其他',
    MODIFY COLUMN content_encrypted LONGTEXT NOT NULL COMMENT '加密保存的用户反馈、纠错或申诉内容',
    MODIFY COLUMN status VARCHAR(30) NOT NULL COMMENT '处理状态：OPEN=待处理，IN_REVIEW=审核中，RESOLVED=已解决，REJECTED=已驳回',
    MODIFY COLUMN reply_encrypted LONGTEXT COMMENT '加密保存的管理员处理回复；尚未回复时为空',
    COMMENT = '用户反馈、报告纠错、申诉及管理员处理回复';

ALTER TABLE `alembic_version`
    MODIFY COLUMN version_num VARCHAR(32) NOT NULL COMMENT '当前已应用的 Alembic 迁移版本号，由迁移工具维护，不是业务版本',
    COMMENT = 'Alembic 数据库迁移版本标记，由迁移工具维护';

UPDATE alembic_version SET version_num='3b9e7c2a4106' WHERE version_num='f4c8a1d2e903';
