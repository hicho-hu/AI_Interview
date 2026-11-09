"""Issue scoped, short-lived OSS credentials. Long-term RAM keys never leave the server."""

import json
import re
from datetime import datetime, timezone

from aliyunsdkcore.client import AcsClient
from aliyunsdkcore.request import CommonRequest

from app.core.errors import BusinessError, require


class STSService:
    def __init__(self, settings, security, cache):
        self.settings, self.security, self.cache = settings, security, cache

    def _issue(self, scope, session_name, policy):
        settings = self.settings
        require(
            settings.aliyun_access_key_id
            and settings.aliyun_access_key_secret
            and settings.aliyun_ram_role_arn,
            "OSS_NOT_CONFIGURED",
            "OSS 授权服务未配置",
            503,
        )
        cache_key = "oss:sts:" + self.security.identity_hash(
            settings.aliyun_ram_role_arn
            + ":"
            + settings.oss_bucket_name
            + ":"
            + scope
            + ":"
            + json.dumps(policy, sort_keys=True)
        )
        cached = self.cache.get(cache_key)
        if cached:
            try:
                data = json.loads(self.security.decrypt(cached))
                if self._remaining(data["expiration"]) > 600:
                    return data
            except (ValueError, KeyError, TypeError):
                pass
        try:
            client = AcsClient(
                settings.aliyun_access_key_id,
                settings.aliyun_access_key_secret,
                settings.oss_region,
                connect_timeout=5,
                timeout=15,
            )
            request = CommonRequest(product="Sts", version="2015-04-01", action_name="AssumeRole")
            request.set_domain(settings.oss_sts_endpoint)
            request.set_protocol_type("https")
            request.set_method("POST")
            request.set_accept_format("JSON")
            for name, value in {
                "RoleArn": settings.aliyun_ram_role_arn,
                "RoleSessionName": session_name,
                "DurationSeconds": str(settings.oss_sts_duration_seconds),
                "Policy": json.dumps(policy, separators=(",", ":")),
            }.items():
                request.add_query_param(name, value)
            response = json.loads(client.do_action_with_exception(request))["Credentials"]
            data = {
                "access_key_id": response["AccessKeyId"],
                "access_key_secret": response["AccessKeySecret"],
                "security_token": response["SecurityToken"],
                "expiration": response["Expiration"],
            }
            require(
                all(isinstance(value, str) and value for value in data.values())
                and self._remaining(data["expiration"]) > 600,
                "OSS_AUTH_UNAVAILABLE",
                "OSS 临时授权返回无效",
                503,
            )
        except BusinessError:
            raise
        except Exception as exc:
            raise BusinessError("OSS_AUTH_UNAVAILABLE", "OSS 临时授权暂不可用，请稍后重试", 503) from exc
        self.cache.put(
            cache_key,
            self.security.encrypt(json.dumps(data)),
            max(1, int(self._remaining(data["expiration"])) - 600),
        )
        return data

    @staticmethod
    def _remaining(expiration):
        return (
            datetime.fromisoformat(expiration.replace("Z", "+00:00")) - datetime.now(timezone.utc)
        ).total_seconds()

    def upload(self, user_id, object_key):
        require(re.fullmatch(r"[a-zA-Z0-9-]{1,64}", user_id), "MEDIA_INVALID_KEY", "用户存储范围无效")
        prefix = f"ai-interview/user/{user_id}/incoming/"
        require(
            object_key.startswith(prefix) and ".." not in object_key.split("/") and "*" not in object_key,
            "MEDIA_INVALID_KEY",
            "上传对象不在当前用户的授权范围",
            403,
        )
        require(self.cache.hit(f"oss:grant:{user_id}", 20, 60), "RATE_LIMITED", "文件授权请求过于频繁", 429)
        policy = {
            "Version": "1",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": ["oss:PutObject", "oss:AbortMultipartUpload", "oss:ListParts"],
                    "Resource": [f"acs:oss:*:*:{self.settings.oss_bucket_name}/{object_key}"],
                }
            ],
        }
        credentials = self._issue(f"upload:{user_id}:{object_key}", f"interview_{user_id}", policy)
        return {
            **credentials,
            "bucket": self.settings.oss_bucket_name,
            "endpoint": self.settings.oss_endpoint,
            "region": self.settings.oss_region,
            "object_key": object_key,
            "prefix": prefix,
        }

    def server(self):
        # Kept inside the backend. Browser sessions never receive this policy or its credentials.
        bucket = self.settings.oss_bucket_name
        policy = {
            "Version": "1",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": ["oss:GetObject", "oss:PutObject", "oss:DeleteObject"],
                    "Resource": [f"acs:oss:*:*:{bucket}/ai-interview/user/*"],
                },
                {"Effect": "Allow", "Action": ["oss:GetBucketAcl"], "Resource": [f"acs:oss:*:*:{bucket}"]},
            ],
        }
        return self._issue("backend", "ai_interview_backend", policy)


def generate_sts_token(user_id, object_key, runtime):
    return STSService(runtime.settings, runtime.security, runtime.cache).upload(user_id, object_key)
