from datetime import datetime, timezone

import httpx
from sqlalchemy import select

from app.core.errors import BusinessError, require
from app.core.security import utcnow
from app.db.models import Consent, RevokedToken, Usage, User
from app.modules.shared import Service


class IdentityService(Service):
    def login(self, identity, nickname, dev=False):
        if dev:
            require(self.rt.settings.dev_login_enabled, "AUTH_DISABLED", "体验登录已禁用", 404)
            identity = "dev:" + identity
        else:
            settings = self.rt.settings
            require(
                settings.wechat_app_id and settings.wechat_app_secret,
                "AUTH_WECHAT_NOT_CONFIGURED",
                "尚未配置微信登录",
                503,
            )
            try:
                result = httpx.get(
                    "https://api.weixin.qq.com/sns/jscode2session",
                    params={
                        "appid": settings.wechat_app_id,
                        "secret": settings.wechat_app_secret,
                        "js_code": identity,
                        "grant_type": "authorization_code",
                    },
                    timeout=10,
                ).json()
            except (httpx.HTTPError, ValueError) as exc:
                raise BusinessError("AUTH_PROVIDER_UNAVAILABLE", "微信登录暂不可用", 503) from exc
            require(
                result.get("openid") and not result.get("errcode"),
                "AUTH_WECHAT_REJECTED",
                "微信 code 无效或已使用",
                401,
            )
            identity = "wechat:" + result["openid"]
        digest = self.rt.security.identity_hash(identity)
        user = self.db.scalar(select(User).where(User.openid_hash == digest))
        if not user:
            user = User(openid_hash=digest, nickname=nickname, credits=self.rt.settings.initial_credits)
            self.db.add(user)
            self.db.flush()
            self.db.add(
                Usage(
                    user_id=user.id,
                    usage_type="WELCOME",
                    quantity=user.credits,
                    status="GRANTED",
                    idempotency_key=f"welcome:{user.id}",
                )
            )
        require(user.status == "ACTIVE", "AUTH_ACCOUNT_DISABLED", "账号不可用", 403)
        from app.modules.accounts.service import AccountService

        account = AccountService(self.rt, self.db, user.id).account()
        return {
            "access_token": self.rt.security.token(user.id, account.auth_version if account else 0),
            "token_type": "bearer",
            "expires_in": self.rt.settings.access_token_minutes * 60,
            "user": self.view(user),
        }

    def authenticate(self, token):
        claims = self.rt.security.decode(token)
        revoked = self.db.scalar(select(RevokedToken).where(RevokedToken.jti == claims["jti"]))
        user = self.db.get(User, claims["sub"])
        require(
            not revoked and user is not None and user.status in ("ACTIVE", "DELETING"),
            "AUTH_UNAUTHORIZED",
            "登录状态已失效",
            401,
        )
        from app.modules.accounts.service import AccountService

        account = AccountService(self.rt, self.db, user.id).account()
        require(
            not account or claims.get("ver", 0) == account.auth_version,
            "AUTH_UNAUTHORIZED",
            "账号安全信息已更新，请重新登录",
            401,
        )
        self.uid = user.id
        return user, claims

    def logout(self, claims):
        if not self.db.scalar(select(RevokedToken).where(RevokedToken.jti == claims["jti"])):
            self.db.add(
                RevokedToken(
                    jti=claims["jti"], expires_at=datetime.fromtimestamp(claims["exp"], timezone.utc)
                )
            )
        self.rt.cache.put(
            f"auth:revoked:{claims['jti']}", "1", max(1, int(claims["exp"] - utcnow().timestamp()))
        )
        self.audit("LOGOUT", self.uid)
        return {"logged_out": True}

    def view(self, user):
        from app.modules.accounts.service import AccountService
        from app.modules.billing.service import credit_balances

        account = AccountService(self.rt, self.db, user.id).account()
        avatar_url = None
        if account and account.avatar_asset_id:
            from app.db.models import MediaAsset
            from app.modules.media.service import MediaService

            asset = self.db.get(MediaAsset, account.avatar_asset_id)
            if asset and asset.status == "READY" and not asset.deleted_at:
                avatar_url = MediaService(self.rt, self.db, user.id).view(asset)["download_url"]
        return {
            "id": user.id,
            "email": self.rt.security.decrypt(account.email_encrypted)
            if account and account.email_encrypted
            else None,
            "has_password": bool(account and account.password_hash),
            "avatar_url": avatar_url,
            "system_avatar_key": account.system_avatar_key if account else None,
            "contact_phone": self.rt.security.decrypt(account.phone_encrypted)
            if account and account.phone_encrypted
            else None,
            "phone_verified": False,
            "nickname": user.nickname,
            "status": user.status,
            **credit_balances(self.db, user),
            "created_at": str(user.created_at),
        }

    def consent(self, kind, version):
        require(version == self.rt.settings.policy_version, "CONSENT_VERSION_MISMATCH", "请阅读最新版本协议")
        row = self.db.scalar(
            select(Consent).where(
                Consent.user_id == self.uid,
                Consent.consent_type == kind,
                Consent.policy_version == version,
                Consent.revoked_at.is_(None),
            )
        )
        if not row:
            row = Consent(user_id=self.uid, consent_type=kind, policy_version=version)
            self.db.add(row)
            self.db.flush()
        self.audit("CONSENT_GRANTED", row.id)
        return {"id": row.id, "consent_type": row.consent_type, "policy_version": version}

    def require_consents(self, *kinds):
        rows = self.db.scalars(
            select(Consent.consent_type).where(
                Consent.user_id == self.uid,
                Consent.policy_version == self.rt.settings.policy_version,
                Consent.revoked_at.is_(None),
            )
        ).all()
        require(all(k in rows for k in kinds), "CONSENT_REQUIRED", "请先授权：" + ", ".join(kinds), 403)

    def revoke(self, kind):
        for row in self.db.scalars(
            select(Consent).where(
                Consent.user_id == self.uid, Consent.consent_type == kind, Consent.revoked_at.is_(None)
            )
        ):
            row.revoked_at = utcnow()
        self.audit("CONSENT_REVOKED", kind)
        return {"revoked": True}
