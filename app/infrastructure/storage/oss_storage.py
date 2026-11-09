"""Private OSS assets, immutable accepted objects and compatibility with local archives."""

import oss2
from oss2.credentials import StaticCredentialsProvider

from app.core.errors import BusinessError, require
from app.infrastructure.storage.sts_service import STSService
from app.providers.storage import LocalStorage


class OSSStorage:
    def __init__(self, settings, security, cache):
        self.settings = settings
        self.sts = STSService(settings, security, cache)
        self.legacy = LocalStorage(settings, security)
        self.session = oss2.Session()

    @staticmethod
    def cloud_key(key):
        return key.startswith("ai-interview/user/")

    def bucket(self):
        credentials = self.sts.server()
        auth = oss2.ProviderAuthV4(
            StaticCredentialsProvider(
                credentials["access_key_id"], credentials["access_key_secret"], credentials["security_token"]
            )
        )
        return oss2.Bucket(
            auth,
            self.settings.oss_endpoint,
            self.settings.oss_bucket_name,
            region=self.settings.oss_region,
            session=self.session,
            connect_timeout=10,
        )

    @staticmethod
    def _run(action):
        try:
            return action()
        except oss2.exceptions.NoSuchKey as exc:
            raise BusinessError("MEDIA_NOT_UPLOADED", "文件尚未上传或已删除", 409) from exc
        except oss2.exceptions.ServerError as exc:
            if exc.status == 412:
                raise BusinessError("MEDIA_ETAG_MISMATCH", "上传对象已变化，请重新确认", 409) from exc
            if exc.status == 403:
                raise BusinessError("OSS_ACCESS_DENIED", "OSS 文件权限不足，请联系管理员", 503) from exc
            raise BusinessError("OSS_UNAVAILABLE", "文件存储服务暂不可用，请稍后重试", 503) from exc
        except oss2.exceptions.RequestError as exc:
            raise BusinessError("OSS_UNAVAILABLE", "无法连接文件存储服务，请稍后重试", 503) from exc

    def put(self, key, content, mime):
        require(self.cloud_key(key), "MEDIA_INVALID_KEY", "OSS 文件路径无效")
        self._run(lambda: self.bucket().put_object(key, content, headers={"Content-Type": mime}))

    def read(self, key):
        return self.read_version(key, None)

    def read_version(self, key, etag):
        if not self.cloud_key(key):
            return self.legacy.read(key)

        def fetch():
            result = self.bucket().get_object(key, headers={"If-Match": etag} if etag else None)
            return result.read(512 * 1024 * 1024 + 1)

        return self._run(fetch)

    def head(self, key):
        if not self.cloud_key(key):
            return self.legacy.head(key)
        result = self._run(lambda: self.bucket().head_object(key))
        return {
            "size": result.content_length,
            "etag": result.etag.strip('"'),
            "mime": result.headers.get("Content-Type"),
        }

    def delete(self, key):
        if not self.cloud_key(key):
            return self.legacy.delete(key)
        self._run(lambda: self.bucket().delete_object(key))

    def finalize(self, asset, etag):
        if not self.cloud_key(asset.object_key):
            return self.legacy.finalize(asset, etag)
        require("/incoming/" in asset.object_key, "MEDIA_INVALID_KEY", "待确认文件路径无效")
        key = asset.object_key.replace("/incoming/", "/accepted/", 1)
        self._run(
            lambda: self.bucket().copy_object(
                self.settings.oss_bucket_name,
                asset.object_key,
                key,
                headers={"x-oss-copy-source-if-match": etag},
            )
        )
        self.delete(asset.object_key)
        return key

    def url(self, asset, purpose="download", seconds=None):
        if not self.cloud_key(asset.object_key):
            return self.legacy.url(asset, purpose)
        require(purpose == "download", "MEDIA_INVALID_STATE", "OSS 上传请使用 STS 凭证")
        return self._run(
            lambda: self.bucket().sign_url(
                "GET", asset.object_key, seconds or self.settings.upload_url_seconds, slash_safe=True
            )
        )

    def path(self, key):
        require(not self.cloud_key(key), "MEDIA_INVALID_KEY", "OSS 文件不在本地")
        return self.legacy.path(key)

    def check_ready(self):
        result = self._run(lambda: self.bucket().get_bucket_acl())
        require(result.acl == "private", "OSS_BUCKET_NOT_PRIVATE", "OSS 存储空间必须设为私有", 503)
