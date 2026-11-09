import base64
import hashlib
import hmac
import uuid
from datetime import datetime, timedelta, timezone

import jwt
from cryptography.fernet import Fernet

from app.core.errors import BusinessError


def utcnow():
    return datetime.now(timezone.utc)


def new_id():
    return str(uuid.uuid4())


class Security:
    def __init__(self, settings):
        self.settings = settings
        key = (
            settings.encryption_key
            or base64.urlsafe_b64encode(
                hashlib.sha256((settings.secret_key + ":encryption").encode()).digest()
            ).decode()
        )
        self.fernet = Fernet(key)

    def encrypt(self, text: str):
        return self.fernet.encrypt(text.encode()).decode()

    def decrypt(self, text):
        return self.fernet.decrypt(text.encode()).decode() if text else ""

    def identity_hash(self, identity: str):
        return hmac.new(self.settings.secret_key.encode(), identity.encode(), hashlib.sha256).hexdigest()

    def token(self, user_id: str, auth_version: int = 0):
        expires = utcnow() + timedelta(minutes=self.settings.access_token_minutes)
        claims = {
            "sub": user_id,
            "jti": new_id(),
            "exp": expires,
            "iat": utcnow(),
            "aud": "ai-interview",
            "ver": auth_version,
        }
        return jwt.encode(claims, self.settings.secret_key, algorithm="HS256")

    def decode(self, token: str):
        try:
            return jwt.decode(token, self.settings.secret_key, algorithms=["HS256"], audience="ai-interview")
        except jwt.PyJWTError as exc:
            raise BusinessError("AUTH_UNAUTHORIZED", "登录已过期或无效", 401) from exc

    def sign_object(self, asset_id: str, purpose: str, expires: int):
        return self.identity_hash(f"{asset_id}:{purpose}:{expires}")
