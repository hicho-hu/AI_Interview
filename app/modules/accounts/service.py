import hmac
import secrets
import time
from datetime import timedelta

from sqlalchemy import select

from app.core.errors import require
from app.core.security import new_id, utcnow
from app.db.growth_models import EmailChallenge, UserAccount
from app.db.models import MediaAsset, User
from app.db.repository import owned
from app.modules.admin.security import password_hash, password_matches
from app.modules.billing.service import BillingService
from app.modules.shared import Service
from app.providers.mail import deliver_code


class AccountService(Service):
    def account(self, uid=None, lock=False):
        statement = (
            select(UserAccount)
            .where(UserAccount.user_id == (uid or self.uid))
            .execution_options(populate_existing=True)
        )
        return self.db.scalar(statement.with_for_update() if lock else statement)

    def digest(self, email):
        return self.rt.security.identity_hash("email:" + email)

    def send_code(self, email, purpose, ip="local"):
        digest = self.digest(email)
        require(
            self.rt.cache.hit(f"email:send:address:{digest}", 1, 60)
            and self.rt.cache.hit(f"email:send:hour:{digest}", 5, 3600)
            and self.rt.cache.hit(f"email:send:ip:{ip}:{int(time.time() // 3600)}", 20, 3600),
            "RATE_LIMITED",
            "验证码请求过于频繁，请稍后重试",
            429,
        )
        code = f"{secrets.randbelow(1000000):06d}"
        challenge = EmailChallenge(
            user_id=self.uid if purpose == "BIND_EMAIL" else None,
            email_hash=digest,
            purpose=purpose,
            code_hash="",
            expires_at=utcnow() + timedelta(minutes=10),
        )
        self.db.add(challenge)
        self.db.flush()
        challenge.code_hash = self.rt.security.identity_hash(f"email-code:{challenge.id}:{code}")
        delivery = deliver_code(self.rt.settings, email, code, purpose)
        return {"challenge_id": challenge.id, "expires_in": 600, "resend_after": 60, **delivery}

    def verify(self, data, purpose):
        challenge = self.db.scalar(
            select(EmailChallenge).where(EmailChallenge.id == data.challenge_id).with_for_update()
        )
        require(
            self.rt.cache.hit(f"email:verify:{data.challenge_id}", 5, 600),
            "EMAIL_CODE_LOCKED",
            "验证码尝试次数过多，请重新获取",
            429,
        )
        require(
            challenge
            and challenge.purpose == purpose
            and challenge.email_hash == self.digest(data.email)
            and challenge.user_id == (self.uid if purpose == "BIND_EMAIL" else None)
            and not challenge.used_at
            and challenge.expires_at > utcnow()
            and hmac.compare_digest(
                challenge.code_hash,
                self.rt.security.identity_hash(f"email-code:{data.challenge_id}:{data.code}"),
            ),
            "EMAIL_CODE_INVALID",
            "验证码错误、已使用或已过期",
            400,
        )
        challenge.used_at = utcnow()

    def result(self, user):
        from app.modules.identity.service import IdentityService

        account = self.account(user.id)
        return {
            "access_token": self.rt.security.token(user.id, account.auth_version if account else 0),
            "expires_in": self.rt.settings.access_token_minutes * 60,
            "user": IdentityService(self.rt, self.db, user.id).view(user),
        }

    def register(self, data):
        require(
            not self.db.scalar(
                select(UserAccount.id).where(UserAccount.email_hash == self.digest(data.email))
            ),
            "EMAIL_ALREADY_REGISTERED",
            "该邮箱已注册，请使用邮箱和密码登录",
            409,
        )
        self.verify(data, "REGISTER")
        user = User(
            openid_hash=self.rt.security.identity_hash("account:" + new_id()),
            nickname=data.nickname.strip() or "面试练习者",
            credits=self.rt.settings.initial_credits,
        )
        self.db.add(user)
        self.db.flush()
        self.db.add(
            UserAccount(
                user_id=user.id,
                email_hash=self.digest(data.email),
                email_encrypted=self.rt.security.encrypt(data.email),
                password_hash=password_hash(data.password),
                auth_version=1,
            )
        )
        self.db.flush()
        billing = BillingService(self.rt, self.db, user.id)
        billing.ledger("WELCOME", {"UNIVERSAL": user.credits}, "GRANTED", f"welcome:{user.id}")
        from app.modules.growth.service import GrowthService

        GrowthService(self.rt, self.db, user.id).registration(data.invite_code, data.promotion_code)
        self.uid = user.id
        self.audit("EMAIL_REGISTERED", user.id)
        return self.result(user)

    def login(self, data, ip="local"):
        digest = self.digest(data.email)
        require(
            self.rt.cache.hit(f"email:login:{digest}", 8, 300)
            and self.rt.cache.hit(f"email:login:ip:{ip}", 30, 300),
            "RATE_LIMITED",
            "登录尝试过多，请稍后重试",
            429,
        )
        account = self.db.scalar(select(UserAccount).where(UserAccount.email_hash == digest))
        user = None
        if account:
            user = BillingService(self.rt, self.db, account.user_id).lock_user()
            account = self.account(account.user_id, lock=True)
            if account.email_hash != digest:
                account = None
        fallback = "scrypt$" + "0" * 32 + "$" + "0" * 128
        matched = password_matches(
            data.password, account.password_hash if account and account.password_hash else fallback
        )
        require(
            matched and user and user.status == "ACTIVE", "AUTH_LOGIN_FAILED", "邮箱、密码或账号状态无效", 401
        )
        self.uid = user.id
        self.audit("EMAIL_LOGIN", user.id)
        return self.result(user)

    def check_password(self, account, password):
        require(
            self.rt.cache.hit(f"account:security:{self.uid}", 8, 300),
            "RATE_LIMITED",
            "安全操作尝试过多，请稍后重试",
            429,
        )
        require(
            account and account.password_hash and password_matches(password or "", account.password_hash),
            "PASSWORD_INVALID",
            "当前密码不正确，或请先绑定邮箱并设置密码",
            401,
        )

    def bind_email(self, data):
        BillingService(self.rt, self.db, self.uid).lock_user()
        account = self.account(lock=True)
        existing = self.db.scalar(
            select(UserAccount).where(UserAccount.email_hash == self.digest(data.email))
        )
        require(
            not existing or existing.user_id == self.uid,
            "EMAIL_ALREADY_REGISTERED",
            "该邮箱已被其他账号绑定",
            409,
        )
        if account and account.password_hash:
            self.check_password(account, data.current_password)
        else:
            require(data.new_password, "PASSWORD_REQUIRED", "首次绑定邮箱时请设置至少 10 位的登录密码")
        self.verify(data, "BIND_EMAIL")
        if not account:
            account = UserAccount(user_id=self.uid, auth_version=0)
            self.db.add(account)
        account.email_hash = self.digest(data.email)
        account.email_encrypted = self.rt.security.encrypt(data.email)
        if data.new_password:
            account.password_hash = password_hash(data.new_password)
        account.auth_version += 1
        self.db.flush()
        self.audit("EMAIL_BOUND", self.uid)
        return self.result(self.db.get(User, self.uid))

    def change_password(self, data):
        BillingService(self.rt, self.db, self.uid).lock_user()
        account = self.account(lock=True)
        self.check_password(account, data.current_password)
        account.password_hash = password_hash(data.new_password)
        account.auth_version += 1
        self.db.flush()
        self.audit("PASSWORD_CHANGED", self.uid)
        return self.result(self.db.get(User, self.uid))

    def reset_password(self, data):
        self.verify(data, "RESET_PASSWORD")
        account = self.db.scalar(select(UserAccount).where(UserAccount.email_hash == self.digest(data.email)))
        require(account, "AUTH_LOGIN_FAILED", "请先注册邮箱账号", 400)
        user = BillingService(self.rt, self.db, account.user_id).lock_user()
        account = self.account(account.user_id, lock=True)
        require(user.status == "ACTIVE", "AUTH_ACCOUNT_DISABLED", "账号不可用", 403)
        account.password_hash = password_hash(data.new_password)
        account.auth_version += 1
        self.db.flush()
        self.uid = user.id
        self.audit("PASSWORD_RESET", user.id)
        return self.result(user)

    def avatar(self, asset_id=None, system_avatar_key=None):
        BillingService(self.rt, self.db, self.uid).lock_user()
        asset = None
        if asset_id:
            from app.modules.media.service import aware

            asset = owned(self.db, MediaAsset, asset_id, self.uid)
            require(
                asset.media_type == "AVATAR"
                and asset.status == "READY"
                and not asset.deleted_at
                and aware(asset.expires_at) > utcnow(),
                "AVATAR_NOT_READY",
                "请上传并确认有效头像后再保存",
                409,
            )
        else:
            require(
                system_avatar_key in {f"avatar-{index:02d}" for index in range(1, 17)},
                "AVATAR_INVALID",
                "系统头像不存在",
                422,
            )
        account = self.account(lock=True)
        if not account:
            account = UserAccount(user_id=self.uid, auth_version=0)
            self.db.add(account)
        previous_avatar = account.avatar_asset_id
        account.avatar_asset_id = asset.id if asset else None
        account.system_avatar_key = system_avatar_key if not asset else None
        if asset:
            asset.expires_at = utcnow() + timedelta(days=3650)
        self.db.flush()
        if previous_avatar and previous_avatar != (asset.id if asset else None):
            from app.modules.media.service import MediaService

            MediaService(self.rt, self.db, self.uid).delete(previous_avatar)
        self.audit("AVATAR_UPDATED", self.uid)
        from app.modules.identity.service import IdentityService

        return IdentityService(self.rt, self.db, self.uid).view(self.db.get(User, self.uid))

    def contact(self, phone):
        BillingService(self.rt, self.db, self.uid).lock_user()
        account = self.account(lock=True)
        if not account:
            account = UserAccount(user_id=self.uid, auth_version=0)
            self.db.add(account)
        account.phone_encrypted = self.rt.security.encrypt(phone) if phone else None
        self.db.flush()
        self.audit("CONTACT_UPDATED", self.uid)
        from app.modules.identity.service import IdentityService

        return IdentityService(self.rt, self.db, self.uid).view(self.db.get(User, self.uid))
