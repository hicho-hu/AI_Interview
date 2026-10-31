import hashlib
import io
import time
from typing import Protocol

import boto3
from botocore.config import Config

from app.core.errors import BusinessError, require


class Storage(Protocol):
    def put(self, key: str, content: bytes, mime: str): ...
    def read(self, key: str) -> bytes: ...
    def delete(self, key: str): ...
    def head(self, key: str) -> dict: ...
    def url(self, asset, purpose: str = "download") -> str: ...


class LocalStorage:
    def __init__(self, settings, security):
        self.settings, self.security = settings, security
        self.root = settings.storage_path.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, key):
        path = (self.root / key).resolve()
        require(self.root in path.parents, "MEDIA_INVALID_KEY", "非法对象路径")
        return path

    def put(self, key, content, mime):
        path = self.path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    def read(self, key):
        try:
            return self.path(key).read_bytes()
        except FileNotFoundError as exc:
            raise BusinessError("MEDIA_NOT_UPLOADED", "对象尚未上传或已删除", 409) from exc

    def head(self, key):
        data = self.read(key)
        return {"size": len(data), "etag": hashlib.md5(data).hexdigest()}

    def delete(self, key):
        self.path(key).unlink(missing_ok=True)

    def url(self, asset, purpose="download"):
        expires = int(time.time()) + self.settings.upload_url_seconds
        signature = self.security.sign_object(asset.id, purpose, expires)
        return (
            f"{self.settings.public_base_url}/api/v1/local-objects/{asset.id}"
            f"?purpose={purpose}&expires={expires}&signature={signature}"
        )

    def finalize(self, asset, etag):
        return asset.object_key


class S3Storage:
    def __init__(self, settings, security):
        self.settings = settings
        kwargs = dict(
            region_name=settings.s3_region,
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                connect_timeout=5,
                read_timeout=30,
                retries={"max_attempts": 2},
            ),
        )
        if settings.s3_access_key and settings.s3_secret_key:
            kwargs.update(
                aws_access_key_id=settings.s3_access_key, aws_secret_access_key=settings.s3_secret_key
            )
        self.client = boto3.client("s3", endpoint_url=settings.s3_endpoint_url, **kwargs)
        self.signer = boto3.client(
            "s3", endpoint_url=settings.s3_public_endpoint_url or settings.s3_endpoint_url, **kwargs
        )

    def put(self, key, content, mime):
        self.client.put_object(Bucket=self.settings.s3_bucket, Key=key, Body=content, ContentType=mime)

    def read(self, key):
        response = self.client.get_object(Bucket=self.settings.s3_bucket, Key=key)
        return response["Body"].read(self.settings.max_upload_bytes + 1)

    def head(self, key):
        try:
            value = self.client.head_object(Bucket=self.settings.s3_bucket, Key=key)
            return {
                "size": value["ContentLength"],
                "mime": value.get("ContentType"),
                "etag": value["ETag"].strip('"'),
            }
        except self.client.exceptions.ClientError as exc:
            raise BusinessError("MEDIA_NOT_UPLOADED", "对象存储校验失败", 409) from exc

    def delete(self, key):
        self.client.delete_object(Bucket=self.settings.s3_bucket, Key=key)

    def finalize(self, asset, etag):
        # Download keys never receive upload grants. Conditional copy prevents upload URL reuse
        # or replacement between validation and finalization from changing accepted content.
        key = "accepted/" + asset.object_key
        self.client.copy_object(
            Bucket=self.settings.s3_bucket,
            Key=key,
            CopySource={"Bucket": self.settings.s3_bucket, "Key": asset.object_key},
            CopySourceIfMatch=etag,
        )
        self.client.delete_object(Bucket=self.settings.s3_bucket, Key=asset.object_key)
        return key

    def url(self, asset, purpose="download"):
        params = {"Bucket": self.settings.s3_bucket, "Key": asset.object_key}
        if purpose == "upload":
            params["ContentType"] = asset.mime_type
        return self.signer.generate_presigned_url(
            "put_object" if purpose == "upload" else "get_object",
            Params=params,
            ExpiresIn=self.settings.upload_url_seconds,
        )


def extract_resume(data, mime):
    require(len(data) <= 10 * 1024 * 1024, "MEDIA_TOO_LARGE", "简历最大 10MB", 413)
    if mime == "application/pdf":
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data), strict=True)
        require(
            not reader.is_encrypted and len(reader.pages) <= 50,
            "RESUME_UNSAFE",
            "请上传未加密且不超过 50 页的简历",
        )
        require(
            "/JavaScript" not in str(reader.trailer) and b"/EmbeddedFile" not in data,
            "RESUME_UNSAFE",
            "简历不允许脚本和嵌入附件",
        )
        text = "\n".join((p.extract_text() or "") for p in reader.pages)
    else:
        import zipfile

        from docx import Document

        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            require(
                sum(x.file_size for x in archive.infolist()) <= 20 * 1024 * 1024,
                "RESUME_UNSAFE",
                "文档解压后超出限制",
            )
            require(
                all(
                    "vba" not in x.filename.lower() and "embeddings/" not in x.filename.lower()
                    for x in archive.infolist()
                ),
                "RESUME_UNSAFE",
                "不支持宏或嵌入对象",
            )
            for name in archive.namelist():
                if name.endswith(".rels"):
                    require(
                        b'TargetMode="External"' not in archive.read(name),
                        "RESUME_UNSAFE",
                        "文档不允许外部资源",
                    )
        document = Document(io.BytesIO(data))
        text = "\n".join(
            [p.text for p in document.paragraphs]
            + [c.text for t in document.tables for r in t.rows for c in r.cells]
        )
    require(0 < len(text.strip()) <= 60000, "RESUME_TEXT_UNAVAILABLE", "无法提取简历文字或文本过长")
    return text.strip()
