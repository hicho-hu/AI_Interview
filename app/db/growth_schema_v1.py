"""Frozen account, invitation and promotion schema for revision c83f5a7e0203."""

import sqlalchemy as sa

from app.core.security import new_id, utcnow
from app.db.types import MYSQL_TABLE_OPTIONS, LongText, UTCDateTime


def define_tables(metadata):
    names = []

    def c(name, type_, meaning, **kwargs):
        return sa.Column(name, type_, comment=meaning, nullable=kwargs.pop("nullable", False), **kwargs)

    def ref(name, target, meaning, **kwargs):
        return sa.Column(
            name,
            sa.String(36),
            sa.ForeignKey(target),
            comment=meaning,
            nullable=kwargs.pop("nullable", False),
            **kwargs,
        )

    def table(name, meaning, *columns):
        names.append(name)
        return sa.Table(
            name,
            metadata,
            c("id", sa.String(36), "记录唯一标识（UUID）", primary_key=True, default=new_id),
            c("created_at", UTCDateTime(), "创建时间（UTC）", default=utcnow),
            c("updated_at", UTCDateTime(), "最后变更时间（UTC）", default=utcnow, onupdate=utcnow),
            *columns,
            comment=meaning,
            **MYSQL_TABLE_OPTIONS,
        )

    table(
        "user_accounts",
        "用户邮箱账号、安全凭证与资料扩展；关联既有业务用户",
        ref("user_id", "users.id", "用户 ID，关联 users.id", unique=True),
        c(
            "email_hash",
            sa.String(64),
            "规范化邮箱的 HMAC，唯一查找键；不保存明文邮箱",
            nullable=True,
            unique=True,
        ),
        c("email_encrypted", LongText, "加密的已验证邮箱地址", nullable=True),
        c("password_hash", sa.String(256), "scrypt 密码散列，含随机盐；不保存明文密码", nullable=True),
        c(
            "auth_version",
            sa.Integer,
            "用户会话版本；改密和换绑后递增，使旧令牌失效",
            default=0,
            server_default="0",
        ),
        ref("avatar_asset_id", "media_assets.id", "已验证的头像媒体资产 ID", nullable=True),
        c(
            "phone_encrypted",
            LongText,
            "可选联系手机号（加密）；仅联系信息，未认证且不用于登录",
            nullable=True,
        ),
    )
    table(
        "email_challenges",
        "邮箱验证码挑战，限定用途、收件人、有效期和一次性消费",
        ref("user_id", "users.id", "绑定邮箱的操作用户 ID；注册或找回密码时为空", nullable=True),
        c("email_hash", sa.String(64), "验证码目标邮箱 HMAC"),
        c("purpose", sa.String(24), "用途：REGISTER/RESET_PASSWORD/BIND_EMAIL"),
        c("code_hash", sa.String(64), "验证码 HMAC，不保存或记录明文验证码"),
        c("expires_at", UTCDateTime(), "验证码过期时间（UTC）"),
        c("used_at", UTCDateTime(), "成功消费时间（UTC）；空表示未使用", nullable=True),
    )
    table(
        "invitation_codes",
        "每个用户唯一的邀请码；注册归因后不可重绑邀请人",
        ref("user_id", "users.id", "邀请人用户 ID", unique=True),
        c("code", sa.String(24), "公开邀请码，大小写规范化", unique=True),
    )
    table(
        "invitation_configs",
        "邀请活动配置版本；新版本仅影响之后验证邮箱并注册的用户",
        c("version", sa.Integer, "配置版本号，递增且唯一", unique=True),
        c("enabled", sa.Boolean, "是否启用邀请奖励", default=True),
        c("threshold", sa.Integer, "邀请人每累计多少名有效新注册用户获得一次奖励，单位人"),
        c("inviter_rewards", sa.JSON, "邀请人每达门槛发放的 TEXT/VOICE/VIDEO 次数组合"),
        c("invitee_rewards", sa.JSON, "受邀人成功注册额外发放的 TEXT/VOICE/VIDEO 次数组合"),
        ref("created_by", "admin_users.id", "发布配置的管理员 ID"),
    )
    table(
        "referral_registrations",
        "有效邀请注册记录；一名新用户仅归因一次，规则取注册时配置快照",
        ref("invitee_id", "users.id", "验证邮箱并成功注册的受邀用户 ID", unique=True),
        ref("inviter_id", "users.id", "邀请人用户 ID"),
        c("config_version", sa.Integer, "注册时邀请规则版本，0表示系统默认版本"),
        c("threshold", sa.Integer, "注册时门槛快照，单位人"),
        c("inviter_rewards", sa.JSON, "注册时邀请人奖励次数组合快照"),
        c("invitee_rewards", sa.JSON, "注册时受邀人奖励次数组合快照"),
    )
    sa.Index(
        "ix_referral_inviter_version",
        metadata.tables["referral_registrations"].c.inviter_id,
        metadata.tables["referral_registrations"].c.config_version,
    )
    table(
        "invitation_rewards",
        "邀请人每个配置版本的里程碑奖励，唯一键防止重复发放",
        ref("inviter_id", "users.id", "获得奖励的邀请人 ID"),
        c("config_version", sa.Integer, "活动配置版本"),
        c("milestone", sa.Integer, "已达到的第几个门槛，从 1 递增"),
        ref("usage_id", "usage_ledger.id", "实际奖励权益流水 ID"),
        sa.UniqueConstraint(
            "inviter_id", "config_version", "milestone", name="uq_invitation_reward_milestone"
        ),
    )
    table(
        "promotion_links",
        "管理后台创建的分佣推广链接及佣金配置",
        c("code", sa.String(24), "公开推广码，唯一", unique=True),
        c("name", sa.String(100), "推广活动名称"),
        c("promoter_name", sa.String(100), "推广合作方或推广人名称"),
        c("rate_bps", sa.Integer, "佣金比例，单位基点；100表示百分之一，范围0至10000"),
        c(
            "status",
            sa.String(16),
            "链接状态：ACTIVE=可用于新注册，DISABLED=停止新注册归因",
            default="ACTIVE",
        ),
        ref("created_by", "admin_users.id", "创建推广链接的管理员 ID"),
    )
    table(
        "promotion_attributions",
        "新注册用户的推广归因，账号唯一且不能在购买时改写",
        ref("user_id", "users.id", "被推广注册的用户 ID", unique=True),
        ref("link_id", "promotion_links.id", "推广链接 ID"),
    )
    table(
        "promotion_commissions",
        "订单分佣应计台账；订单唯一，退款冲销，不执行真实转账",
        ref("order_id", "orders.id", "产生佣金的订单 ID", unique=True),
        ref("link_id", "promotion_links.id", "归属推广链接 ID"),
        c("rate_bps", sa.Integer, "订单创建时固化的佣金比例（基点）"),
        c("amount_minor", sa.Integer, "佣金金额（人民币分），按订单实付乘比例向下取整"),
        c("status", sa.String(16), "应计状态：ACCRUED=已计提，REVERSED=退款已冲销"),
        c("reversed_at", UTCDateTime(), "退款冲销时间（UTC）", nullable=True),
    )
    return [metadata.tables[name] for name in names]
