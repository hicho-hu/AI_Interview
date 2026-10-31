"""Frozen Chinese schema documentation for migration 3b9e7c2a4106.

Keep this snapshot immutable after release; future schema/comment changes get a new version.
Each entry captures the original MySQL type, nullability and business meaning.
"""

from typing import NamedTuple

import sqlalchemy as sa
from sqlalchemy.dialects import mysql
from sqlalchemy.schema import CreateColumn


class ColumnDoc(NamedTuple):
    sql_type: str
    nullable: bool
    comment: str


def field(sql_type, comment, nullable=False):
    return ColumnDoc(sql_type, nullable, comment)


ENTITY = {
    "id": field("VARCHAR(36)", "记录唯一标识（UUID，由应用生成）"),
    "created_at": field("DATETIME(6)", "记录创建时间（UTC，精确到微秒，由应用写入）"),
    "updated_at": field("DATETIME(6)", "记录最近更新时间（UTC，由应用在修改记录时刷新）"),
}
OWNED = {
    **ENTITY,
    "user_id": field("VARCHAR(36)", "所属用户 ID，关联 users.id，用于数据归属校验"),
}

TABLE_COMMENTS = {
    "users": "用户账号、昵称、账号状态及剩余面试额度",
    "user_consents": "用户隐私、协议、AI 处理及录音录像授权记录",
    "revoked_tokens": "已撤销的登录令牌，用于退出后禁止旧令牌继续访问",
    "job_profiles": "目标岗位 JD、岗位级别及 AI 解析出的结构化岗位画像",
    "media_assets": "简历、录音、录像及提问语音的对象存储元数据，不保存文件二进制",
    "resume_assets": "简历文件关联、解析状态、加密原文和结构化简历摘要",
    "interviews": "一次模拟面试的配置、状态、当前轮次及起止时间",
    "interview_plans": "结合岗位、简历和配置生成的个性化面试计划",
    "interview_turns": "面试主问题、追问、澄清及对应回答和 AI 决策",
    "interview_reports": "按版本保存的面试评估报告及综合评分",
    "ai_call_logs": "AI 调用的供应商、模型、用量、延迟、结果和成本记录",
    "usage_ledger": "面试额度预占、消费、释放、购买及退款账本",
    "orders": "训练额度套餐订单、支付状态及退款信息",
    "outbox_events": "事务 Outbox 与异步任务，支持可靠派发、租约和失败重试",
    "idempotency_records": "关键写操作的幂等键、请求摘要及原始响应快照",
    "audit_logs": "用户或管理员关键操作的审计记录，不保存敏感业务原文",
    "feedback": "用户反馈、报告纠错、申诉及管理员处理回复",
    "alembic_version": "Alembic 数据库迁移版本标记，由迁移工具维护",
}

COLUMN_DOCS = {
    "users": {
        **ENTITY,
        "openid_hash": field("VARCHAR(64)", "登录身份的 HMAC-SHA256 哈希，不保存明文微信 OpenID"),
        "nickname": field("VARCHAR(80)", "用户显示昵称"),
        "status": field("VARCHAR(24)", "账号状态：ACTIVE=正常，DELETING=删除中，DELETED=已删除"),
        "credits": field("INTEGER", "当前可用面试次数；创建面试时扣减预占，未消费释放时归还"),
    },
    "user_consents": {
        **OWNED,
        "consent_type": field(
            "VARCHAR(30)",
            "授权类型：PRIVACY=隐私，TERMS=协议，AI_PROCESSING=AI 处理，MICROPHONE=麦克风，CAMERA=摄像头",
        ),
        "policy_version": field("VARCHAR(30)", "用户同意的隐私政策或协议版本号"),
        "revoked_at": field("DATETIME(6)", "撤销授权时间（UTC）；为空表示尚未撤销", True),
    },
    "revoked_tokens": {
        **ENTITY,
        "jti": field("VARCHAR(36)", "被撤销 JWT 的唯一标识，用于拦截该令牌"),
        "expires_at": field("DATETIME(6)", "原登录令牌的到期时间（UTC），到期后可清理撤销记录"),
    },
    "job_profiles": {
        **OWNED,
        "job_title": field("VARCHAR(120)", "目标招聘岗位名称，例如 Python 后端工程师"),
        "seniority": field("VARCHAR(30)", "岗位级别：JUNIOR=初级，MIDDLE=中级，SENIOR=高级，EXPERT=专家"),
        "jd_encrypted": field("LONGTEXT", "加密保存的招聘岗位描述 JD 原文"),
        "profile_encrypted": field("LONGTEXT", "加密岗位画像 JSON，包含职责、技能、经验要求及能力维度", True),
        "status": field(
            "VARCHAR(24)", "岗位解析状态：PREPARING=处理中，READY=就绪，FAILED=失败，DELETED=已删除"
        ),
        "task_id": field("VARCHAR(36)", "岗位解析异步任务 ID，对应 outbox_events.id；任务创建前可为空", True),
    },
    "media_assets": {
        **OWNED,
        "interview_id": field("VARCHAR(36)", "关联面试 ID；独立简历或 JD 图片可为空", True),
        "turn_id": field("VARCHAR(36)", "关联面试轮次 ID；独立文件可为空，分段录像共用同一轮次", True),
        "media_type": field(
            "VARCHAR(24)",
            "媒体用途：AUDIO=回答录音，VIDEO=回答录像，RESUME=简历，JD_IMAGE=岗位图片，TTS=提问语音",
        ),
        "object_key": field("VARCHAR(500)", "私有对象存储中的唯一文件键，不是永久公开下载地址"),
        "filename": field("VARCHAR(200)", "上传或生成文件的名称，包含文件扩展名"),
        "mime_type": field("VARCHAR(120)", "文件 MIME 类型，例如 audio/wav、video/mp4、application/pdf"),
        "size_bytes": field("BIGINT", "文件大小，单位字节；完成上传时与实际对象大小核对"),
        "duration_ms": field("INTEGER", "音视频实际时长，单位毫秒；非音视频文件可为空", True),
        "checksum": field("VARCHAR(64)", "文件 SHA-256 校验值，用于内容完整性核验", True),
        "etag": field("VARCHAR(128)", "对象存储返回的 ETag，用于上传校验和条件复制", True),
        "status": field(
            "VARCHAR(24)",
            "媒体状态：PENDING_UPLOAD=待上传，READY=可用，DELETING=删除中，DELETED=已删除；过期可由 expires_at 判断",
        ),
        "expires_at": field("DATETIME(6)", "文件保留截止时间（UTC），到期禁止访问并安排清理"),
        "deleted_at": field("DATETIME(6)", "文件删除完成时间（UTC）；未删除时为空", True),
    },
    "resume_assets": {
        **OWNED,
        "asset_id": field(
            "VARCHAR(36)", "简历原始文件资产 ID，关联 media_assets.id，一份文件对应一份解析记录"
        ),
        "parse_status": field(
            "VARCHAR(24)",
            "简历解析状态：PENDING_UPLOAD=待上传，PROCESSING=处理中，READY=就绪，FAILED=失败，DELETED=已删除",
        ),
        "text_encrypted": field("LONGTEXT", "从 PDF/DOCX 提取的简历文字原文，采用加密存储", True),
        "summary_encrypted": field(
            "LONGTEXT", "加密结构化简历 JSON，含教育、工作、项目、技能及原文证据", True
        ),
        "task_id": field("VARCHAR(36)", "简历解析异步任务 ID，关联 outbox_events.id", True),
    },
    "interviews": {
        **OWNED,
        "job_profile_id": field("VARCHAR(36)", "目标岗位档案 ID，关联 job_profiles.id"),
        "resume_asset_id": field("VARCHAR(36)", "可选简历解析记录 ID，关联 resume_assets.id", True),
        "status": field(
            "VARCHAR(24)",
            "面试状态：DRAFT=草稿，PREPARING=准备中，READY=待开始，IN_PROGRESS=进行中，PAUSED=暂停，FINISHING=报告生成中，COMPLETED=完成，FAILED=准备失败，REPORT_FAILED=报告失败，CANCELLED=取消",
        ),
        "mode": field("VARCHAR(16)", "面试模式：TEXT=文字，VOICE=语音，VIDEO=视频"),
        "config_json": field("JSON", "面试配置 JSON，含时长、难度、题数、语言、面试类型和追问/澄清上限"),
        "current_turn_no": field("INTEGER", "当前轮次序号，从 1 开始；首题生成前为 0"),
        "version": field("INTEGER", "乐观锁版本号；状态或轮次更新时递增，防止并发覆盖"),
        "started_at": field("DATETIME(6)", "面试正式开始时间（UTC）；未开始时为空", True),
        "ended_at": field("DATETIME(6)", "面试结束或取消时间（UTC）；未结束时为空", True),
        "paused_at": field("DATETIME(6)", "本次暂停开始时间（UTC）；未暂停或恢复后为空", True),
        "paused_seconds": field("INTEGER", "累计暂停时长，单位秒；计算有效面试时间时扣除"),
        "prepare_task_id": field("VARCHAR(36)", "面试计划与首题准备任务 ID，关联 outbox_events.id", True),
        "report_task_id": field("VARCHAR(36)", "最近一次报告生成任务 ID，关联 outbox_events.id", True),
        "failure_code": field("VARCHAR(80)", "最近一次准备或报告失败的业务错误码；成功后清空", True),
        "deleted_at": field("DATETIME(6)", "面试记录软删除时间（UTC）；未删除时为空", True),
    },
    "interview_plans": {
        **ENTITY,
        "interview_id": field("VARCHAR(36)", "所属面试 ID，关联 interviews.id；每场面试一份计划"),
        "plan_encrypted": field("LONGTEXT", "加密面试计划 JSON，含主问题、考察维度、JD 证据及简历关联"),
        "prompt_version": field("VARCHAR(30)", "生成计划使用的提示词版本号"),
        "model_name": field("VARCHAR(100)", "生成计划使用的模型名称；mock 表示开发模拟供应商"),
    },
    "interview_turns": {
        **ENTITY,
        "interview_id": field("VARCHAR(36)", "所属面试 ID，关联 interviews.id"),
        "turn_no": field("INTEGER", "场内轮次序号，从 1 开始；与 interview_id 组合唯一"),
        "parent_turn_id": field("VARCHAR(36)", "被追问或澄清的上一轮 ID；主问题为空", True),
        "turn_type": field("VARCHAR(24)", "题目类型：QUESTION=主问题，FOLLOW_UP=追问，CLARIFICATION=澄清"),
        "main_index": field("INTEGER", "对应主问题在计划中的下标，从 0 开始；追问和澄清沿用原主问题下标"),
        "competency": field("VARCHAR(120)", "本轮问题考察的岗位能力维度"),
        "question_encrypted": field("LONGTEXT", "加密保存的 AI 提问文本"),
        "question_asset_id": field(
            "VARCHAR(36)", "AI 提问 TTS 语音资产 ID，关联 media_assets.id；文字模式可为空", True
        ),
        "answer_asset_id": field(
            "VARCHAR(36)", "回答的首段音视频资产 ID；文字回答为空，多段完整列表记录在任务载荷中", True
        ),
        "transcript_encrypted": field(
            "LONGTEXT", "加密回答文本：用户文字输入或 ASR 语音转写，多段回答按顺序合并", True
        ),
        "confidence": field("JSON", "ASR 置信度（0～1），文字回答为 1；供应商未提供置信度时为 null", True),
        "decision_encrypted": field(
            "LONGTEXT", "加密 AI 决策 JSON，含追问/下一题/澄清/结束、原因和回答摘要", True
        ),
        "status": field(
            "VARCHAR(24)",
            "轮次状态：WAITING_ANSWER=待回答，SUBMITTED=已提交待处理，COMPLETED=处理完成，FAILED=处理失败，DELETED=已删除",
        ),
        "task_id": field("VARCHAR(36)", "回答转写与分析任务 ID，关联 outbox_events.id", True),
    },
    "interview_reports": {
        **ENTITY,
        "interview_id": field("VARCHAR(36)", "所属面试 ID，关联 interviews.id"),
        "report_version": field("INTEGER", "报告版本号，从 1 递增；重新生成保留旧版本"),
        "overall_score": field("JSON", "综合得分（0～100）；证据不足或模拟环境时为 JSON null", True),
        "content_encrypted": field("LONGTEXT", "加密完整报告 JSON，含维度评分、逐题证据、建议和原始轮次快照"),
        "prompt_version": field("VARCHAR(30)", "生成报告使用的提示词与评分规则版本号"),
        "model_name": field("VARCHAR(100)", "生成报告使用的模型名称；mock 表示开发模拟供应商"),
    },
    "ai_call_logs": {
        **OWNED,
        "interview_id": field("VARCHAR(36)", "关联面试 ID；独立岗位或简历解析调用可为空", True),
        "turn_id": field("VARCHAR(36)", "关联轮次 ID；非逐题调用或新轮次回滚时可为空", True),
        "provider": field("VARCHAR(40)", "AI 适配器类型，例如 mock、http、compatible"),
        "model": field("VARCHAR(100)", "本次调用使用的实际模型名称"),
        "call_type": field(
            "VARCHAR(30)",
            "调用用途：job=岗位解析，resume=简历解析，plan=计划，decision=追问决策，report=报告，asr=转写，tts=语音合成",
        ),
        "input_tokens": field("INTEGER", "本次调用的输入 Token 数；供应商未计量时为 0"),
        "output_tokens": field("INTEGER", "本次调用的输出 Token 数；供应商未计量时为 0"),
        "latency_ms": field("INTEGER", "本次供应商调用耗时，单位毫秒"),
        "cost_microunits": field(
            "BIGINT", "AI 调用成本，单位人民币微元（1 元=1000000 微元）；按网关账单或配置费率计价"
        ),
        "units": field("JSON", "额外用量 JSON，可含 ASR 毫秒数、TTS 字符数及成本来源", True),
        "status": field("VARCHAR(24)", "调用结果：SUCCEEDED=成功，FAILED=失败；重试逐次记录"),
        "request_id": field("VARCHAR(80)", "触发此次调用的 HTTP 请求追踪 ID", True),
    },
    "usage_ledger": {
        **OWNED,
        "interview_id": field("VARCHAR(36)", "关联面试 ID；注册赠送、购买或退款可为空", True),
        "usage_type": field(
            "VARCHAR(30)", "账本类型：WELCOME=注册赠送，INTERVIEW=面试额度，PURCHASE=购买，REFUND=退款"
        ),
        "quantity": field("INTEGER", "额度变动次数；增加为正、扣减为负，预占释放由 status 表示"),
        "amount_minor": field("BIGINT", "对应金额，单位人民币分；购买为正、退款为负，无金额变动为 0"),
        "status": field(
            "VARCHAR(24)",
            "额度状态：GRANTED=已发放，RESERVED=已预占，CONSUMED=已消费，RELEASED=已释放，REFUNDED=已退款",
        ),
        "idempotency_key": field("VARCHAR(200)", "全局唯一账本幂等键，用于防止重复发放、扣费或退款"),
    },
    "orders": {
        **OWNED,
        "product_code": field(
            "VARCHAR(40)", "产品编码：SINGLE=单次，PACK_10=10 次套餐，MONTHLY=20 次月度额度包"
        ),
        "amount_minor": field("INTEGER", "订单应付金额，单位人民币分"),
        "credits": field("INTEGER", "订单支付成功后发放的面试次数"),
        "currency": field("VARCHAR(3)", "订单币种代码，当前为 CNY（人民币）"),
        "status": field("VARCHAR(24)", "订单状态：PENDING=待支付，PAID=已支付，REFUNDED=已全额退款"),
        "provider_trade_no": field(
            "VARCHAR(120)", "支付供应商唯一交易流水号；未支付时为空，开发支付使用 dev 前缀", True
        ),
        "paid_at": field("DATETIME(6)", "支付成功时间（UTC）；未支付时为空", True),
        "refunded_at": field("DATETIME(6)", "全额退款完成时间（UTC）；未退款时为空", True),
    },
    "outbox_events": {
        **OWNED,
        "event_type": field(
            "VARCHAR(40)",
            "任务类型，例如 PARSE_JOB、PARSE_RESUME、PREPARE_INTERVIEW、PROCESS_ANSWER、GENERATE_REPORT、DELETE_MEDIA、DELETE_USER",
        ),
        "aggregate_id": field("VARCHAR(36)", "本任务操作的业务对象 ID，具体含义由 event_type 决定"),
        "payload_encrypted": field("LONGTEXT", "加密任务输入 JSON，例如回答分段资产列表或报告冻结快照", True),
        "result_encrypted": field("LONGTEXT", "加密任务结果 JSON；任务成功前可为空", True),
        "status": field(
            "VARCHAR(24)",
            "任务状态：PENDING=待处理，QUEUED=已入队，RUNNING=执行中，RETRYING=等待重试，SUCCEEDED=成功，FAILED=失败，CANCELLED=取消",
        ),
        "retry_count": field("INTEGER", "当前处理周期内已失败次数，用于退避和判断重试上限"),
        "available_at": field("DATETIME(6)", "任务最早可执行时间（UTC），延迟重试时向后推迟"),
        "lease_until": field("DATETIME(6)", "派发或执行租约到期时间（UTC），过期后可重新领取", True),
        "lease_token": field("VARCHAR(36)", "执行租约唯一令牌，用于防止并发重复领取和过期执行者写回", True),
        "error_code": field("VARCHAR(80)", "最近一次任务失败的业务错误码", True),
        "request_id": field("VARCHAR(80)", "创建任务的 HTTP 请求追踪 ID；定时任务可为空", True),
    },
    "idempotency_records": {
        **OWNED,
        "scope": field("VARCHAR(200)", "幂等操作范围，区分创建面试、提交回答、结束、订单等操作"),
        "key": field("VARCHAR(120)", "客户端 Idempotency-Key；与 user_id、scope 组合唯一，区分大小写"),
        "request_hash": field("VARCHAR(64)", "规范化请求内容的 SHA-256 摘要，识别同键不同请求"),
        "response_encrypted": field("LONGTEXT", "加密原始响应 JSON；重复请求直接返回该结果"),
    },
    "audit_logs": {
        **OWNED,
        "action": field("VARCHAR(60)", "操作标识，例如授权、创建面试、提交回答、删除数据等审计事件"),
        "resource_id": field("VARCHAR(120)", "操作涉及的资源标识；通常为业务记录 ID，也可为授权类型"),
        "request_id": field("VARCHAR(80)", "本次操作的 HTTP 请求追踪 ID", True),
    },
    "feedback": {
        **OWNED,
        "interview_id": field("VARCHAR(36)", "反馈涉及的面试 ID；通用反馈可为空", True),
        "category": field(
            "VARCHAR(30)", "反馈类型：CONTENT=内容问题，REPORT=报告纠错，PRIVACY=隐私，OTHER=其他"
        ),
        "content_encrypted": field("LONGTEXT", "加密保存的用户反馈、纠错或申诉内容"),
        "status": field(
            "VARCHAR(30)", "处理状态：OPEN=待处理，IN_REVIEW=审核中，RESOLVED=已解决，REJECTED=已驳回"
        ),
        "reply_encrypted": field("LONGTEXT", "加密保存的管理员处理回复；尚未回复时为空", True),
    },
    "alembic_version": {
        "version_num": field("VARCHAR(32)", "当前已应用的 Alembic 迁移版本号，由迁移工具维护，不是业务版本"),
    },
}


def column_type(sql_type):
    if sql_type.startswith("VARCHAR("):
        return mysql.VARCHAR(int(sql_type[8:-1]))
    return {
        "DATETIME(6)": mysql.DATETIME(fsp=6),
        "INTEGER": mysql.INTEGER(),
        "BIGINT": mysql.BIGINT(),
        "LONGTEXT": mysql.LONGTEXT(),
        "JSON": mysql.JSON(),
    }[sql_type]


def baseline_table(name):
    """Frozen f4c8a1d2e903 definitions for offline comment-only migration generation."""
    options = dict(mysql_engine="InnoDB", mysql_charset="utf8mb4", mysql_collate="utf8mb4_0900_bin")
    if name == "alembic_version":
        options["mysql_collate"] = "utf8mb4_0900_ai_ci"
    columns = [
        sa.Column(key, column_type(doc.sql_type), nullable=doc.nullable, comment=doc.comment)
        for key, doc in COLUMN_DOCS[name].items()
    ]
    constraints = (
        [sa.PrimaryKeyConstraint("version_num", name="alembic_version_pkc")]
        if name == "alembic_version"
        else []
    )
    return sa.Table(name, sa.MetaData(), *columns, *constraints, comment=TABLE_COMMENTS[name], **options)


def attach_schema_documentation(metadata):
    """Fail fast when a model column is introduced without a corresponding field explanation."""
    for table in metadata.tables.values():
        documented = COLUMN_DOCS[table.name]
        if set(table.c.keys()) != set(documented):
            raise ValueError(f"{table.name} 的字段与注释文档不一致，请为新增或删除字段维护注释")
        table.comment = TABLE_COMMENTS[table.name]
        for column in table.c:
            column.comment = documented[column.name].comment


def comment_alter_statements(connection=None, clear=False):
    """Render COMMENT changes only; online reflection retains all existing column attributes.

    Offline rendering targets the frozen baseline. No FIRST/AFTER, ADD/DROP, data UPDATE,
    charset conversion or index changes are included. Build every statement before execution
    so undocumented columns fail validation before any table is changed.
    """
    dialect = connection.dialect if connection is not None else mysql.dialect()
    if dialect.name != "mysql":
        raise ValueError("字段注释更新 SQL 仅适用于 MySQL")
    quote = dialect.identifier_preparer.quote_identifier
    literal = sa.String().literal_processor(dialect)
    statements = []
    for name, docs in COLUMN_DOCS.items():
        table = (
            sa.Table(name, sa.MetaData(), autoload_with=connection, resolve_fks=False)
            if connection is not None
            else baseline_table(name)
        )
        if set(table.c.keys()) != set(docs):
            raise ValueError(f"{name} 的实际字段与注释快照不一致，尚未生成任何更新语句")
        changes = []
        for key, doc in docs.items():
            column = table.c[key]
            desired = "" if clear else doc.comment
            if connection is not None and (column.comment or "") == desired:
                continue
            column.comment = desired
            definition = str(CreateColumn(column).compile(dialect=dialect)).strip()
            changes.append("MODIFY COLUMN " + definition)
        desired_table = "" if clear else TABLE_COMMENTS[name]
        if connection is None or (table.comment or "") != desired_table:
            changes.append("COMMENT = " + literal(desired_table))
        if changes:
            statements.append(f"ALTER TABLE {quote(name)}\n    " + ",\n    ".join(changes) + ";")
    return statements
