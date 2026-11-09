import secrets
from urllib.parse import urlencode

from sqlalchemy import func, select

from app.core.errors import require
from app.core.security import utcnow
from app.db.admin_models import OrderSnapshot
from app.db.growth_models import (
    InvitationCode,
    InvitationConfig,
    InvitationReward,
    PromotionAttribution,
    PromotionCommission,
    PromotionLink,
    ReferralRegistration,
    UserAccount,
)
from app.db.models import MediaAsset, Usage, User
from app.db.repository import page_query
from app.modules.billing.service import BillingService
from app.modules.shared import Service


class GrowthService(Service):
    def config(self):
        row = self.db.scalar(select(InvitationConfig).order_by(InvitationConfig.version.desc()).limit(1))
        return (
            self.config_view(row)
            if row
            else {
                "id": None,
                "version": 0,
                "enabled": True,
                "threshold": 3,
                "inviter_rewards": {"TEXT": 1, "VOICE": 0, "VIDEO": 0},
                "invitee_rewards": {"TEXT": 1, "VOICE": 0, "VIDEO": 0},
            }
        )

    @staticmethod
    def config_view(row):
        return {
            name: getattr(row, name)
            for name in ("id", "version", "enabled", "threshold", "inviter_rewards", "invitee_rewards")
        }

    def configure(self, data, admin_id):
        row = InvitationConfig(
            **data.model_dump(exclude={"reason"}), version=self.config()["version"] + 1, created_by=admin_id
        )
        self.db.add(row)
        self.db.flush()
        return self.config_view(row)

    @staticmethod
    def code():
        return "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(10))

    def invitation(self):
        BillingService(self.rt, self.db, self.uid).lock_user()
        row = self.db.scalar(select(InvitationCode).where(InvitationCode.user_id == self.uid))
        if not row:
            row = InvitationCode(user_id=self.uid, code=self.code())
            self.db.add(row)
            self.db.flush()
        return row

    def invitation_view(self):
        code = self.invitation().code
        config = self.config()
        total = self.db.scalar(
            select(func.count())
            .select_from(ReferralRegistration)
            .where(ReferralRegistration.inviter_id == self.uid)
        )
        count = self.db.scalar(
            select(func.count())
            .select_from(ReferralRegistration)
            .where(
                ReferralRegistration.inviter_id == self.uid,
                ReferralRegistration.config_version == config["version"],
            )
        )
        rewards = self.db.scalar(
            select(func.count())
            .select_from(Usage)
            .where(Usage.user_id == self.uid, Usage.usage_type.in_(("INVITER_REWARD", "INVITEE_REWARD")))
        )
        reward_rows = self.db.scalars(
            select(Usage)
            .where(Usage.user_id == self.uid, Usage.usage_type.in_(("INVITER_REWARD", "INVITEE_REWARD")))
            .order_by(Usage.created_at.desc())
        ).all()
        billing = BillingService(self.rt, self.db, self.uid)
        recent = self.db.scalars(
            select(ReferralRegistration)
            .where(ReferralRegistration.inviter_id == self.uid)
            .order_by(ReferralRegistration.created_at.desc())
            .limit(20)
        ).all()
        return {
            "code": code,
            "url": self.rt.settings.web_base_url.rstrip("/") + "/register?" + urlencode({"invite": code}),
            "valid_invites": total,
            "current_rule_invites": count,
            "reward_count": rewards,
            "next_reward_in": config["threshold"] - (count % config["threshold"]),
            "rules": config,
            "reward_records": [
                {
                    "id": row.id,
                    "usage_type": row.usage_type,
                    "quantity": row.quantity,
                    "credit_allocations": billing.allocations(row),
                    "status": row.status,
                    "created_at": row.created_at,
                }
                for row in reward_rows
            ],
            "recent": [self.invitee_view(row) for row in recent],
        }

    def invitee_view(self, row):
        user = self.db.get(User, row.invitee_id)
        account = self.db.scalar(select(UserAccount).where(UserAccount.user_id == row.invitee_id))
        asset = self.db.get(MediaAsset, account.avatar_asset_id) if account and account.avatar_asset_id else None
        return {
            "id": row.id, "registered_at": row.created_at, "status": "REGISTERED",
            "email": self.rt.security.decrypt(account.email_encrypted) if account and account.email_encrypted else None,
            "nickname": user.nickname if user else None,
            "avatar_url": self.rt.storage.url(asset) if asset and asset.status == "READY" else None,
            "system_avatar_key": account.system_avatar_key if account else None,
        }

    def grant(self, uid, rewards, kind, key):
        amounts = {mode: count for mode, count in rewards.items() if count > 0}
        if not amounts:
            return None
        billing = BillingService(self.rt, self.db, uid)
        billing.lock_user()
        for mode, count in amounts.items():
            billing.change_balance(mode, count)
        return billing.ledger(kind, amounts, "GRANTED", key)

    def registration(self, invite_code=None, promotion_code=None):
        if invite_code:
            code = self.db.scalar(
                select(InvitationCode).where(InvitationCode.code == invite_code.strip().upper())
            )
            inviter = self.db.get(User, code.user_id) if code else None
            require(
                inviter and inviter.status == "ACTIVE" and inviter.id != self.uid,
                "INVITATION_INVALID",
                "邀请码无效，或邀请人账号不可用",
                400,
            )
            BillingService(self.rt, self.db, inviter.id).lock_user()
            config = self.config()
            self.db.add(
                ReferralRegistration(
                    invitee_id=self.uid,
                    inviter_id=inviter.id,
                    config_version=config["version"],
                    threshold=config["threshold"],
                    inviter_rewards=config["inviter_rewards"],
                    invitee_rewards=config["invitee_rewards"],
                )
            )
            self.db.flush()
            if config["enabled"]:
                self.grant(self.uid, config["invitee_rewards"], "INVITEE_REWARD", f"invitee:{self.uid}")
                count = self.db.scalar(
                    select(func.count())
                    .select_from(ReferralRegistration)
                    .where(
                        ReferralRegistration.inviter_id == inviter.id,
                        ReferralRegistration.config_version == config["version"],
                    )
                )
                milestone = count // config["threshold"]
                previous = self.db.scalar(
                    select(func.coalesce(func.max(InvitationReward.milestone), 0)).where(
                        InvitationReward.inviter_id == inviter.id,
                        InvitationReward.config_version == config["version"],
                    )
                )
                for index in range(previous + 1, milestone + 1):
                    entry = self.grant(
                        inviter.id,
                        config["inviter_rewards"],
                        "INVITER_REWARD",
                        f"inviter:{inviter.id}:{config['version']}:{index}",
                    )
                    if entry:
                        self.db.add(
                            InvitationReward(
                                inviter_id=inviter.id,
                                config_version=config["version"],
                                milestone=index,
                                usage_id=entry.id,
                            )
                        )
        if promotion_code:
            link = self.db.scalar(
                select(PromotionLink).where(
                    PromotionLink.code == promotion_code.strip().upper(), PromotionLink.status == "ACTIVE"
                )
            )
            require(link, "PROMOTION_INVALID", "推广链接已停用或不存在", 400)
            self.db.add(PromotionAttribution(user_id=self.uid, link_id=link.id))
        self.db.flush()

    def promotion_snapshot(self):
        attribution = self.db.scalar(
            select(PromotionAttribution).where(PromotionAttribution.user_id == self.uid)
        )
        if not attribution:
            return None
        link = self.db.get(PromotionLink, attribution.link_id)
        return {"link_id": link.id, "rate_bps": link.rate_bps}

    def on_paid(self, order):
        snapshot = self.db.scalar(select(OrderSnapshot).where(OrderSnapshot.order_id == order.id))
        promotion = snapshot.snapshot_json.get("promotion") if snapshot else None
        if not promotion:
            return
        if self.db.scalar(select(PromotionCommission.id).where(PromotionCommission.order_id == order.id)):
            return
        self.db.add(
            PromotionCommission(
                order_id=order.id,
                link_id=promotion["link_id"],
                rate_bps=promotion["rate_bps"],
                amount_minor=order.amount_minor * promotion["rate_bps"] // 10000,
                status="ACCRUED",
            )
        )

    def on_refund(self, order):
        commission = self.db.scalar(
            select(PromotionCommission).where(PromotionCommission.order_id == order.id).with_for_update()
        )
        if commission and commission.status == "ACCRUED":
            commission.status = "REVERSED"
            commission.reversed_at = utcnow()

    def promotion(self, data, admin_id, link_id=None):
        values = data.model_dump(exclude={"reason", "alipay_account"})
        encrypted_alipay = self.rt.security.encrypt(data.alipay_account.strip())
        if link_id:
            row = self.db.get(PromotionLink, link_id)
            require(row, "RESOURCE_NOT_FOUND", "推广链接不存在", 404)
            for key, value in values.items():
                setattr(row, key, value)
            row.alipay_account_encrypted = encrypted_alipay
        else:
            row = PromotionLink(**values, alipay_account_encrypted=encrypted_alipay, code=self.code(), created_by=admin_id)
            self.db.add(row)
        self.db.flush()
        return self.promotion_view(row)

    def promotion_view(self, row):
        counts = self.db.scalar(
            select(func.count())
            .select_from(PromotionAttribution)
            .where(PromotionAttribution.link_id == row.id)
        )
        amounts = dict(
            self.db.execute(
                select(
                    PromotionCommission.status, func.coalesce(func.sum(PromotionCommission.amount_minor), 0)
                )
                .where(PromotionCommission.link_id == row.id)
                .group_by(PromotionCommission.status)
            ).all()
        )
        return {
            **{
                name: getattr(row, name)
                for name in ("id", "code", "name", "promoter_name", "rate_bps", "status", "created_at")
            },
            "alipay_account": self.rt.security.decrypt(row.alipay_account_encrypted) if row.alipay_account_encrypted else "",
            "url": self.rt.settings.web_base_url.rstrip("/")
            + "/register?"
            + urlencode({"promotion": row.code}),
            "registrations": counts,
            "accrued_minor": int(amounts.get("ACCRUED", 0)),
            "reversed_minor": int(amounts.get("REVERSED", 0)),
        }

    def promotions(self, page=1, page_size=20):
        rows, total = page_query(
            self.db, select(PromotionLink).order_by(PromotionLink.created_at.desc()), page, page_size
        )
        return {
            "items": [self.promotion_view(row) for row in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    def commissions(self, page=1, page_size=20):
        rows, total = page_query(
            self.db,
            select(PromotionCommission).order_by(PromotionCommission.created_at.desc()),
            page,
            page_size,
        )
        return {
            "items": [
                {
                    **{
                        name: getattr(row, name)
                        for name in (
                            "id",
                            "order_id",
                            "link_id",
                            "rate_bps",
                            "amount_minor",
                            "status",
                            "created_at",
                            "reversed_at",
                            "settled_at",
                        )
                    },
                    "promoter_name": self.db.get(PromotionLink, row.link_id).promoter_name,
                    "alipay_account": self.rt.security.decrypt(self.db.get(PromotionLink, row.link_id).alipay_account_encrypted) if self.db.get(PromotionLink, row.link_id).alipay_account_encrypted else "",
                }
                for row in rows
            ],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    def settle_commissions(self, link_ids):
        links = list(dict.fromkeys(link_ids))
        rows = self.db.scalars(select(PromotionCommission).where(
            PromotionCommission.link_id.in_(links), PromotionCommission.status == "ACCRUED"
        ).with_for_update()).all()
        require(rows, "NO_SETTLEABLE_COMMISSIONS", "所选佣金账户没有待结算佣金", 409)
        now = utcnow()
        for row in rows:
            row.status = "SETTLED"
            row.settled_at = now
        self.db.flush()
        return {"settled_count": len(rows), "settled_minor": sum(row.amount_minor for row in rows), "settled_at": now}
