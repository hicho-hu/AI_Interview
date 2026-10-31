import threading
import time
from collections import OrderedDict
from typing import Any, cast

from redis import Redis
from redis.exceptions import RedisError

from app.core.errors import BusinessError


class Cache:
    def __init__(self, settings):
        self.settings = settings
        self.redis = Redis.from_url(settings.redis_url, decode_responses=True) if settings.redis_url else None
        self.local = OrderedDict()
        self.mutex = threading.RLock()

    def key(self, suffix):
        return f"aim:{self.settings.environment}:{suffix}"

    def hit(self, suffix, limit, seconds=60):
        key = self.key(suffix)
        if self.redis:
            try:
                count = self.redis.eval(
                    "local n=redis.call('INCR',KEYS[1]); if n==1 then redis.call('EXPIRE',KEYS[1],ARGV[1]) end; return n",
                    1,
                    key,
                    seconds,
                )
                return int(cast(Any, count)) <= limit
            except RedisError as exc:
                if self.settings.environment == "production":
                    raise BusinessError("DEPENDENCY_UNAVAILABLE", "限流服务暂不可用", 503) from exc
        with self.mutex:
            value, expires = self.local.get(key, (0, 0))
            if expires < time.time():
                value, expires = 0, time.time() + seconds
            self.local[key] = (value + 1, expires)
            if len(self.local) > 10000:
                self.local.popitem(last=False)
            return value + 1 <= limit

    def put(self, suffix, value, ttl=900):
        if self.redis:
            try:
                self.redis.setex(self.key(suffix), ttl, value)
            except RedisError:
                pass  # 业务事实始终在数据库，可重建
        with self.mutex:
            self.local[self.key(suffix)] = (value, time.time() + ttl)

    def get(self, suffix):
        if self.redis:
            try:
                return self.redis.get(self.key(suffix))
            except RedisError:
                pass
        with self.mutex:
            value, expires = self.local.get(self.key(suffix), (None, 0))
            return value if expires > time.time() else None
