import hashlib
import json

from sqlalchemy import select

from app.core.errors import require
from app.db.models import AuditLog, Idempotency, Task


def dump(value):
    return json.dumps(value, ensure_ascii=False, default=str, sort_keys=True)


class Service:
    def __init__(self, runtime, session, user_id=None, request_id=None):
        self.rt, self.db, self.uid, self.rid = runtime, session, user_id, request_id

    def encrypt_json(self, value):
        return self.rt.security.encrypt(dump(value))

    def decrypt_json(self, value, default=None):
        return json.loads(self.rt.security.decrypt(value)) if value else default

    def audit(self, action, resource_id):
        self.db.add(AuditLog(user_id=self.uid, action=action, resource_id=resource_id, request_id=self.rid))

    def task(self, event, aggregate_id, payload=None):
        task = Task(
            user_id=self.uid,
            event_type=event,
            aggregate_id=aggregate_id,
            payload_encrypted=self.encrypt_json(payload or {}),
            request_id=self.rid,
        )
        self.db.add(task)
        self.db.flush()
        return task

    def idem(self, scope, key, payload, operation):
        require(key and 1 <= len(key) <= 120, "IDEMPOTENCY_REQUIRED", "请提供 1~120 字符 Idempotency-Key")
        digest = hashlib.sha256(dump(payload).encode()).hexdigest()
        row = self.db.scalar(
            select(Idempotency).where(
                Idempotency.user_id == self.uid, Idempotency.scope == scope, Idempotency.key == key
            )
        )
        if row:
            require(row.request_hash == digest, "IDEMPOTENCY_CONFLICT", "幂等键已用于不同请求", 409)
            return self.decrypt_json(row.response_encrypted)
        result = operation()
        self.db.add(
            Idempotency(
                user_id=self.uid,
                scope=scope,
                key=key,
                request_hash=digest,
                response_encrypted=self.encrypt_json(result),
            )
        )
        self.db.flush()
        return result
