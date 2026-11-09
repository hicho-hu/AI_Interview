"""Independent admin credentials, TOTP and a fixed, reviewable least-privilege matrix."""

import base64
import hashlib
import hmac
import os
import struct
import time

from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

PERMISSIONS = {
    "SUPER_ADMIN": {"*"},
    "OPERATIONS": {
        "dashboard:read",
        "user:read",
        "interview:read",
        "quality:read",
        "quality:assign",
        "product:read",
        "product:write",
        "growth:read",
        "growth:write",
        "ai:read",
    },
    "SUPPORT": {
        "dashboard:read",
        "user:read",
        "user:update",
        "interview:read",
        "report:retry",
        "entitlement:grant",
    },
    "QUALITY": {
        "interview:read",
        "interview:content",
        "media:play",
        "quality:read",
        "quality:update",
        "ai:read",
    },
    "FINANCE": {
        "finance:read",
        "order:refund",
        "product:read",
        "product:write",
        "growth:read",
        "growth:write",
        "payment:read",
        "payment:write",
        "reconciliation:write",
    },
    "AUDITOR": {"system:read", "audit:read"},
}
ROLE_LABELS = {
    "SUPER_ADMIN": "超级管理员",
    "OPERATIONS": "运营管理员",
    "SUPPORT": "客服人员",
    "QUALITY": "质检专员",
    "FINANCE": "财务人员",
    "AUDITOR": "审计安全",
}


def permits(admin, permission):
    allowed = PERMISSIONS.get(admin.role, set())
    return "*" in allowed or permission in allowed


def password_hash(password, salt=None):
    salt = salt or os.urandom(16).hex()
    digest = Scrypt(salt=bytes.fromhex(salt), length=64, n=16384, r=8, p=1).derive(password.encode()).hex()
    return "scrypt$" + salt + "$" + digest


def password_matches(password, encoded):
    try:
        _, salt, _ = encoded.split("$")
        return hmac.compare_digest(password_hash(password, salt), encoded)
    except (ValueError, TypeError):
        return False


def totp(secret, step=None):
    counter = int(time.time() // 30) if step is None else step
    key = base64.b32decode(secret.upper() + "=" * (-len(secret) % 8))
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 15
    return str((struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF) % 1000000).zfill(6)


def valid_totp(secret, code, last_step=None):
    for step in range(int(time.time() // 30) - 1, int(time.time() // 30) + 2):
        if (last_step is None or step > last_step) and hmac.compare_digest(totp(secret, step), code or ""):
            return step
    return None
