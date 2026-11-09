import hashlib
import io
import json
from datetime import timedelta
from types import SimpleNamespace

import oss2
import pytest

from app.core.errors import BusinessError
from app.core.security import utcnow
from app.db.models import MediaAsset
from app.infrastructure.storage.oss_storage import OSSStorage
from tests.conftest import data, login
from tests.test_admin import admin_login

IMAGE = b"\x89PNG\r\n\x1a\nsynthetic-test-content"


def enable_oss(client, monkeypatch):
    runtime = client.app.state.runtime
    settings = runtime.settings
    settings.storage_backend = "oss"
    settings.aliyun_access_key_id = "test-long-term-id"
    settings.aliyun_access_key_secret = "test-long-term-secret"
    settings.aliyun_ram_role_arn = "acs:ram::1234567890123456:role/test-oss"
    calls, objects = [], {}

    class STSClient:
        def do_action_with_exception(self, request):
            calls.append(request.get_query_params())
            return json.dumps(
                {
                    "Credentials": {
                        "AccessKeyId": "STS.test-only-id",
                        "AccessKeySecret": "test-temporary-secret",
                        "SecurityToken": "test-security-token",
                        "Expiration": (utcnow() + timedelta(hours=1)).isoformat(),
                    }
                }
            ).encode()

    class Bucket:
        acl = "private"

        def put_object(self, key, content, headers):
            objects[key] = (content, headers["Content-Type"])

        def head_object(self, key):
            if key not in objects:
                raise oss2.exceptions.NoSuchKey(404, {}, b"", {"Code": "NoSuchKey"})
            content, mime = objects[key]
            return SimpleNamespace(
                content_length=len(content),
                etag=hashlib.md5(content).hexdigest(),
                headers={"Content-Type": mime},
            )

        def get_object(self, key, headers=None):
            if headers:
                assert headers["If-Match"] == self.head_object(key).etag
            return io.BytesIO(objects[key][0])

        def copy_object(self, bucket, source, destination, headers):
            assert headers["x-oss-copy-source-if-match"] == self.head_object(source).etag
            objects[destination] = objects[source]

        def delete_object(self, key):
            objects.pop(key, None)

        def sign_url(self, method, key, seconds, **kwargs):
            return f"https://test-bucket.oss-cn-hangzhou.aliyuncs.com/{key}?expires={seconds}&signature=test"

        def get_bucket_acl(self):
            return SimpleNamespace(acl=self.acl)

    bucket = Bucket()
    monkeypatch.setattr("app.infrastructure.storage.sts_service.AcsClient", lambda *a, **kw: STSClient())
    monkeypatch.setattr("app.infrastructure.storage.oss_storage.oss2.Bucket", lambda *a, **kw: bucket)
    runtime.storage = OSSStorage(settings, runtime.security, runtime.cache)
    return runtime, calls, objects, bucket


def ticket(client, headers, size=len(IMAGE)):
    return data(
        client.post(
            "/api/v1/media/upload-url",
            headers=headers,
            json={
                "filename": "sample.png",
                "content_type": "image/png",
                "size": size,
                "media_type": "JD_IMAGE",
            },
        )
    )


def test_sts_requires_jwt_owner_and_consents_and_has_exact_asset_policy(client, monkeypatch):
    owner, uid = login(client)
    other, _ = login(client, "other")
    runtime, calls, _, _ = enable_oss(client, monkeypatch)
    upload = ticket(client, owner)
    path = "/api/v1/storage/sts-token?asset_id=" + upload["asset_id"]
    assert client.get(path).status_code == 401
    assert client.get(path, headers=other).status_code == 404
    assert not calls
    response = client.get(path, headers=owner)
    grant = data(response)
    assert "no-store" in response.headers["Cache-Control"]
    assert grant["object_key"] == upload["object_key"]
    assert grant["prefix"] == f"ai-interview/user/{uid}/incoming/"
    statement = json.loads(calls[0]["Policy"])["Statement"][0]
    assert statement["Resource"] == [f"acs:oss:*:*:ai-interview-storage/{upload['object_key']}"]
    assert set(statement["Action"]) == {"oss:PutObject", "oss:AbortMultipartUpload", "oss:ListParts"}
    assert "test-long-term" not in response.text
    assert data(client.get(path, headers=owner)) == grant
    assert len(calls) == 1
    assert all("test-temporary-secret" not in str(v) for v in runtime.cache.local.values())
    data(client.delete("/api/v1/users/me/consents/PRIVACY", headers=owner))
    assert client.get(path, headers=owner).status_code == 403


def test_completion_validates_content_and_moves_to_immutable_accepted_key(client, monkeypatch):
    owner, _ = login(client)
    runtime, _, objects, bucket = enable_oss(client, monkeypatch)
    upload = ticket(client, owner)
    key, asset_id = upload["object_key"], upload["asset_id"]
    complete = f"/api/v1/media/{asset_id}/complete"
    assert client.post(complete, headers=owner, json={}).status_code == 409
    objects[key] = (b"wrong", "image/png")
    assert client.post(complete, headers=owner, json={}).status_code == 400
    objects[key] = (IMAGE, "image/png")
    assert client.post(complete, headers=owner, json={"etag": "incorrect"}).status_code == 400
    ready = data(client.post(complete, headers=owner, json={"etag": bucket.head_object(key).etag}))
    assert ready["status"] == "READY" and "/accepted/" in ready["download_url"]
    assert key not in objects
    with runtime.db.session() as db:
        accepted = db.get(MediaAsset, asset_id).object_key
    # Reusing an unexpired browser credential cannot replace the accepted download.
    objects[key] = (b"replacement", "image/png")
    assert runtime.storage.read(accepted) == IMAGE
    assert (
        data(client.post(complete, headers=owner, json={}))["checksum"] == hashlib.sha256(IMAGE).hexdigest()
    )
    assert client.get(f"/api/v1/storage/sts-token?asset_id={asset_id}", headers=owner).status_code == 409
    assert client.get("/api/v1/media/" + asset_id, headers=owner).status_code == 200


def test_expired_pending_assets_and_invalid_user_paths_cannot_get_sts(client, monkeypatch):
    owner, uid = login(client)
    runtime, calls, _, _ = enable_oss(client, monkeypatch)
    upload = ticket(client, owner)
    with runtime.db.session() as db:
        db.get(MediaAsset, upload["asset_id"]).expires_at = utcnow() - timedelta(seconds=1)
    assert client.get(upload["upload_url"], headers=owner).status_code == 409
    for key in (
        f"ai-interview/user/{uid}/accepted/file.png",
        "ai-interview/user/other/incoming/x.png",
        f"ai-interview/user/{uid}/incoming/../x",
    ):
        with pytest.raises(BusinessError):
            runtime.storage.sts.upload(uid, key)
    assert not calls


def test_oss_keeps_preexisting_local_assets_readable_and_deletable(client, monkeypatch):
    owner, _ = login(client)
    runtime = client.app.state.runtime
    old_key = "test/users/old/jd.png"
    runtime.storage.put(old_key, IMAGE, "image/png")
    runtime, _, _, _ = enable_oss(client, monkeypatch)
    assert runtime.storage.read(old_key) == IMAGE
    assert runtime.storage.head(old_key)["size"] == len(IMAGE)
    runtime.storage.delete(old_key)
    with pytest.raises(BusinessError, match="对象尚未上传"):
        runtime.storage.read(old_key)


def test_private_bucket_readiness_and_admin_diagnostics_do_not_expose_secrets(client, monkeypatch):
    root, _ = admin_login(client)
    finance, _ = admin_login(client, "finance", "FINANCE")
    runtime, calls, _, bucket = enable_oss(client, monkeypatch)
    endpoint = "/api/v1/admin/storage/status"
    assert client.get(endpoint).status_code == 401
    assert client.get(endpoint, headers=finance).status_code == 403
    assert data(client.get(endpoint, headers=root))["sts"] == "not_checked"
    assert not calls
    result = client.get(endpoint + "?probe=true", headers=root)
    assert data(result)["private_bucket"] == "ok"
    assert "test-temporary-secret" not in result.text and "test-long-term" not in result.text
    bucket.acl = "public-read"
    assert client.get("/health/ready").status_code == 503
    assert data(client.get(endpoint + "?probe=true", headers=root))["error_code"] == "OSS_BUCKET_NOT_PRIVATE"
    bucket.acl = "private"
    assert client.get("/health/ready").status_code == 200
    with pytest.raises(BusinessError) as error:
        runtime.storage._run(
            lambda: (_ for _ in ()).throw(oss2.exceptions.ServerError(403, {}, b"", {"Code": "AccessDenied"}))
        )
    assert error.value.code == "OSS_ACCESS_DENIED"
