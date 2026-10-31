import base64
import os
import time
from datetime import timedelta

import jwt
from sqlalchemy import select

from app.core.errors import BusinessError, require
from app.core.security import new_id, utcnow
from app.db.admin_models import AdminAudit, AdminSession, AdminUser
from app.modules.admin.security import PERMISSIONS, password_hash, password_matches, valid_totp


class AdminAuth:
    def __init__(self, runtime, db, request=None):
        self.rt, self.db, self.request = runtime, db, request

    def audit(self, admin_id, action, resource="", reason="", result="SUCCESS"):
        self.db.add(
            AdminAudit(
                admin_id=admin_id,
                action=action,
                resource_id=resource,
                reason=reason,
                result=result,
                ip=self.request.client.host if self.request and self.request.client else "local",
                request_id=self.request.state.request_id if self.request else "cli",
            )
        )

    @staticmethod
    def view(admin):
        return {
            "id": admin.id,
            "login_name": admin.login_name,
            "display_name": admin.display_name,
            "role": admin.role,
            "status": admin.status,
            "permissions": sorted(PERMISSIONS.get(admin.role, [])),
            "mfa_enabled": bool(admin.mfa_secret_encrypted),
            "last_login_at": admin.last_login_at,
        }

    def login(self, data):
        ip = self.request.client.host if self.request and self.request.client else "local"
        # Limit IP and account independently; no account existence disclosure.
        digest = self.rt.security.identity_hash(data.login_name.lower())
        allowed = self.rt.cache.hit(f"admin:login:ip:{ip}:{int(time.time() // 60)}", 20)
        allowed = self.rt.cache.hit(f"admin:login:account:{digest}", 8, 300) and allowed
        require(allowed, "RATE_LIMITED", "登录尝试过多，请稍后重试", 429)
        row = self.db.scalar(select(AdminUser).where(AdminUser.login_name == data.login_name))
        fallback = "scrypt$" + "0" * 32 + "$" + "0" * 128
        matched = password_matches(data.password, row.password_hash if row else fallback)
        if not row or row.status != "ACTIVE" or not matched:
            # Separate transaction preserves failed-login audit when request transaction rolls back.
            with self.rt.db.session() as log_db:
                AdminAuth(self.rt, log_db, self.request).audit(None, "LOGIN_FAILED", digest, result="DENIED")
            raise BusinessError("ADMIN_LOGIN_FAILED", "账号、密码或账号状态无效", 401)
        if row.mfa_secret_encrypted:
            # Serialize TOTP consumption across concurrent login attempts.
            row = self.db.scalar(select(AdminUser).where(AdminUser.id == row.id).with_for_update())
            step = valid_totp(
                self.rt.security.decrypt(row.mfa_secret_encrypted), data.mfa_code, row.last_mfa_step
            )
            require(step is not None, "ADMIN_MFA_REQUIRED", "请输入有效且未使用的动态验证码", 401)
            row.last_mfa_step = step
        require(
            self.rt.settings.environment != "production" or row.mfa_secret_encrypted,
            "ADMIN_MFA_REQUIRED",
            "生产环境需先通过管理命令启用 MFA",
            403,
        )
        now, token_id = utcnow(), new_id()
        expires = now + timedelta(minutes=30)
        token = jwt.encode(
            {"sub": row.id, "jti": token_id, "aud": "ai-interview-admin", "iat": now, "exp": expires},
            self.rt.settings.secret_key,
            algorithm="HS256",
        )
        self.db.add(AdminSession(admin_id=row.id, token_id=token_id, expires_at=expires))
        row.last_login_at = now
        self.audit(row.id, "LOGIN", row.id)
        return {"access_token": token, "expires_in": 1800, "admin": self.view(row)}

    def authenticate(self, token):
        try:
            claims = jwt.decode(
                token,
                self.rt.settings.secret_key,
                algorithms=["HS256"],
                audience="ai-interview-admin",
                options={"require": ["sub", "jti", "exp", "iat"]},
            )
        except jwt.PyJWTError as exc:
            raise BusinessError("ADMIN_UNAUTHORIZED", "管理员登录已过期", 401) from exc
        session = self.db.scalar(select(AdminSession).where(AdminSession.token_id == claims["jti"]))
        row = self.db.get(AdminUser, claims["sub"])
        require(
            row
            and row.status == "ACTIVE"
            and session
            and session.admin_id == row.id
            and not session.revoked_at
            and session.expires_at > utcnow(),
            "ADMIN_UNAUTHORIZED",
            "管理员会话已失效",
            401,
        )
        return row, session

    def mfa_setup(self, admin):
        require(not admin.mfa_secret_encrypted, "ADMIN_MFA_ENABLED", "MFA 已启用", 409)
        secret = base64.b32encode(os.urandom(20)).decode()
        self.rt.cache.put(f"admin:mfa-setup:{admin.id}", secret, 300)
        return {
            "secret": secret,
            "uri": f"otpauth://totp/InterviewAdmin:{admin.login_name}?secret={secret}&issuer=InterviewAdmin",
        }

    def mfa_confirm(self, admin, data):
        require(
            not admin.mfa_secret_encrypted
            and self.rt.cache.get(f"admin:mfa-setup:{admin.id}") == data.secret,
            "ADMIN_MFA_SETUP_EXPIRED",
            "请重新生成 MFA 密钥",
            409,
        )
        step = valid_totp(data.secret, data.code)
        require(step is not None, "ADMIN_MFA_INVALID", "验证码无效", 400)
        admin.mfa_secret_encrypted = self.rt.security.encrypt(data.secret)
        admin.last_mfa_step = step
        self.audit(admin.id, "MFA_ENABLED", admin.id)
        return {"enabled": True}


def create_admin(db, login_name, display_name, password, role="SUPER_ADMIN", mfa_secret=None, security=None):
    require(len(password) >= 12, "ADMIN_WEAK_PASSWORD", "管理员密码至少 12 位")
    require(role in PERMISSIONS, "ADMIN_INVALID_ROLE", "角色不存在")
    require(
        not db.scalar(select(AdminUser).where(AdminUser.login_name == login_name)),
        "ADMIN_EXISTS",
        "管理员已存在",
        409,
    )
    row = AdminUser(
        login_name=login_name,
        display_name=display_name,
        password_hash=password_hash(password),
        role=role,
        status="ACTIVE",
    )
    if mfa_secret:
        row.mfa_secret_encrypted = security.encrypt(mfa_secret)
    db.add(row)
    db.flush()
    return row
