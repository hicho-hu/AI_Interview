"""Frozen admin schema for revision a61d8c4e9201. Future changes use new migrations."""

import sqlalchemy as sa

from app.core.security import new_id, utcnow
from app.db.types import MYSQL_TABLE_OPTIONS, LongText, UTCDateTime


def col(name, type_, meaning, nullable=False, **kwargs):
    return sa.Column(name, type_, nullable=nullable, comment=meaning, **kwargs)


def reference(name, target, meaning, nullable=False):
    return sa.Column(name, sa.String(36), sa.ForeignKey(target), nullable=nullable, comment=meaning)


def define_tables(metadata):
    names = []

    def table(name, meaning, *columns, **kwargs):
        names.append(name)
        return sa.Table(
            name,
            metadata,
            col("id", sa.String(36), "记录唯一标识（UUID）", primary_key=True, default=new_id),
            col("created_at", UTCDateTime(), "创建时间（UTC）", default=utcnow),
            col("updated_at", UTCDateTime(), "更新时间（UTC）", default=utcnow, onupdate=utcnow),
            *columns,
            comment=meaning,
            **MYSQL_TABLE_OPTIONS,
            **kwargs,
        )

    table(
        "admin_users",
        "独立后台管理员账号、角色及多因素认证，不复用求职者身份",
        col("login_name", sa.String(80), "管理员登录名", unique=True),
        col("display_name", sa.String(80), "管理员显示姓名"),
        col("password_hash", sa.String(256), "scrypt 密码散列，包含随机盐；不保存明文"),
        col("role", sa.String(24), "角色：SUPER_ADMIN/OPERATIONS/SUPPORT/QUALITY/FINANCE/AUDITOR"),
        col("status", sa.String(24), "状态：ACTIVE=启用，DISABLED=禁用", default="ACTIVE"),
        col("mfa_secret_encrypted", LongText, "加密的 TOTP 密钥；空表示未启用 MFA", nullable=True),
        col("last_mfa_step", sa.BigInteger, "最近使用的 TOTP 时间步，防止重放", nullable=True),
        col("last_login_at", UTCDateTime(), "最近登录时间（UTC）", nullable=True),
    )
    table(
        "admin_sessions",
        "后台可撤销会话；JWT audience 与求职端隔离",
        reference("admin_id", "admin_users.id", "管理员 ID，关联 admin_users.id"),
        col("token_id", sa.String(36), "JWT jti 唯一标识", unique=True),
        col("expires_at", UTCDateTime(), "会话到期时间（UTC）"),
        col("revoked_at", UTCDateTime(), "撤销时间（UTC）", nullable=True),
    )
    table(
        "admin_audit_logs",
        "后台追加式操作审计，业务 API 不提供更新或删除",
        reference("admin_id", "admin_users.id", "操作管理员 ID；登录失败时可空", nullable=True),
        col("action", sa.String(80), "操作类型"),
        col("resource_id", sa.String(120), "资源 ID，不记录媒体 URL 和业务正文"),
        col("reason", sa.String(500), "业务操作原因；不应填入敏感数据"),
        col("result", sa.String(24), "结果：SUCCESS/DENIED"),
        col("ip", sa.String(64), "请求来源 IP"),
        col("request_id", sa.String(80), "请求追踪 ID"),
    )
    table(
        "admin_idempotency_records",
        "管理员写操作幂等请求与加密响应",
        reference("admin_id", "admin_users.id", "管理员 ID，关联 admin_users.id"),
        col("scope", sa.String(120), "操作作用域"),
        col("key", sa.String(120), "客户端幂等键"),
        col("request_hash", sa.String(64), "请求内容 SHA256 摘要"),
        col("response_encrypted", LongText, "加密响应快照；不保存播放 URL"),
        sa.UniqueConstraint("admin_id", "scope", "key", name="uq_admin_idempotency"),
    )
    table(
        "admin_approval_requests",
        "关键操作双人审批，保存不可变更快照",
        reference("applicant_id", "admin_users.id", "申请管理员 ID"),
        reference("reviewer_id", "admin_users.id", "审核管理员 ID；不得等于申请人", nullable=True),
        col("kind", sa.String(40), "类型：PRODUCT_PUBLISH/PAYMENT_CONFIG/ENTITLEMENT_GRANT/REFUND"),
        col("resource_id", sa.String(120), "目标资源 ID"),
        col("payload_encrypted", LongText, "加密申请内容快照"),
        col("reason", sa.String(500), "申请理由及关联工单"),
        col("status", sa.String(24), "状态：PENDING/APPROVED/REJECTED", default="PENDING"),
        col("review_note", sa.String(500), "审核意见", nullable=True),
    )
    table(
        "product_skus",
        "按 SKU 和版本保存商品配置，已发布版本只影响新订单",
        col("code", sa.String(40), "商品 SKU 编码"),
        col("version", sa.Integer, "配置版本号，从 1 递增"),
        col("name", sa.String(100), "商品显示名"),
        col("kind", sa.String(24), "类型：SINGLE/MONTH/QUARTER/YEAR/PACK"),
        col("amount_minor", sa.Integer, "售价（人民币分）"),
        col("original_minor", sa.Integer, "原价（人民币分）"),
        col("credits", sa.Integer, "一次性发放的面试次数，不按月重置"),
        col("validity_days", sa.Integer, "计划有效天数，首版仅配置，不执行到期回收"),
        col("currency", sa.String(3), "币种：CNY", default="CNY"),
        col("description", sa.String(500), "商品描述"),
        col("status", sa.String(24), "状态：DRAFT/IN_REVIEW/PUBLISHED/RETIRED", default="DRAFT"),
        reference("created_by", "admin_users.id", "创建管理员 ID"),
        col("published_at", UTCDateTime(), "发布时间（UTC）", nullable=True),
        sa.UniqueConstraint("code", "version", name="uq_product_sku_version"),
    )
    table(
        "order_product_snapshots",
        "订单创建时固化的商品价格和权益快照，发布新价不改历史订单",
        reference("order_id", "orders.id", "订单 ID，关联 orders.id"),
        reference("product_id", "product_skus.id", "商品版本 ID；历史常量商品可空", nullable=True),
        col("snapshot_json", sa.JSON, "SKU、版本、金额、币种和次数快照"),
        sa.UniqueConstraint("order_id", name="uq_order_product_snapshot"),
    )
    table(
        "payment_channel_configs",
        "支付配置元数据；只保存密钥托管引用，真实适配器另行接入",
        col("channel", sa.String(16), "渠道：WECHAT/ALIPAY"),
        col("environment", sa.String(16), "环境：sandbox/production"),
        col("config_encrypted", LongText, "加密商户标识、HTTPS 回调与密钥引用；无明文私钥"),
        col("enabled", sa.Boolean, "配置开关，不代表真实支付已接通", default=False),
        col("version", sa.Integer, "配置版本号", default=1),
        sa.UniqueConstraint("channel", "environment", name="uq_payment_channel_environment"),
    )
    table(
        "refund_requests",
        "全额退款申请及双人审批；到账结果必须由渠道确认",
        reference(
            "order_id",
            "orders.id",
            "目标订单 ID",
        ),
        reference("applicant_id", "admin_users.id", "申请管理员 ID"),
        reference("approval_id", "admin_approval_requests.id", "关联审批 ID", nullable=True),
        col("amount_minor", sa.Integer, "退款金额（人民币分）"),
        col("reason", sa.String(500), "退款原因"),
        col("status", sa.String(24), "状态：PENDING/APPROVED/REJECTED/SUCCEEDED", default="PENDING"),
        col("provider_refund_no", sa.String(120), "渠道退款流水；dev 前缀仅为开发模拟", nullable=True),
    )
    table(
        "reconciliation_batches",
        "导入渠道账单后与业务订单核对，不伪造渠道拉单",
        col("channel", sa.String(16), "渠道：WECHAT/ALIPAY/DEV"),
        col("bill_date", sa.String(10), "账单日期 YYYY-MM-DD（运营时区）"),
        col("status", sa.String(24), "状态：MATCHED/DIFFERENCES"),
        col("total_rows", sa.Integer, "导入账单行数"),
        col("difference_count", sa.Integer, "未匹配条目数"),
        reference("created_by", "admin_users.id", "导入管理员 ID"),
    )
    table(
        "reconciliation_items",
        "对账差异工单；不直接修改订单金额",
        reference("batch_id", "reconciliation_batches.id", "关联对账批次 ID"),
        col("order_id", sa.String(36), "账单提供的订单 ID；可为不存在的订单"),
        col(
            "difference_type", sa.String(40), "差异：MISSING_PLATFORM/MISSING_CHANNEL/AMOUNT/STATUS/DUPLICATE"
        ),
        col("amount_minor", sa.Integer, "渠道账单金额（人民币分）"),
        col("status", sa.String(24), "工单状态：OPEN/RESOLVED", default="OPEN"),
        col("resolution", sa.String(500), "处理结论", nullable=True),
    )
    table(
        "quality_review_tasks",
        "授权质检样本与问题处理任务，限定审阅人资源范围",
        reference("interview_id", "interviews.id", "关联面试 ID"),
        reference("assignee_id", "admin_users.id", "受派管理员 ID"),
        reference("created_by", "admin_users.id", "创建管理员 ID"),
        col("reason", sa.String(500), "抽检原因及关联工单"),
        col("status", sa.String(24), "状态：PENDING/IN_REVIEW/COMPLETED/NEEDS_FIX", default="PENDING"),
        col("tags", sa.JSON, "问题标签数组"),
        col("conclusion", sa.String(2000), "处理结论", nullable=True),
    )
    for name in names:
        sa.Index("ix_" + name + "_created_at", metadata.tables[name].c.created_at)
    return names
