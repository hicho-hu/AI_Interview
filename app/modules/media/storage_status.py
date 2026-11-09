"""Storage configuration and opt-in connectivity checks without exposing credentials."""

from app.core.errors import BusinessError
from app.infrastructure.storage.oss_storage import OSSStorage


def storage_status(runtime, probe=False):
    settings = runtime.settings
    configured = bool(
        settings.aliyun_access_key_id and settings.aliyun_access_key_secret and settings.aliyun_ram_role_arn
    )
    result = {
        "active_backend": settings.storage_backend,
        "oss_configured": configured,
        "bucket": settings.oss_bucket_name,
        "region": settings.oss_region,
        "endpoint": settings.oss_endpoint,
        "role_arn": settings.aliyun_ram_role_arn,
        "sts": "not_checked",
        "private_bucket": "not_checked",
    }
    if not probe:
        return result
    if not configured:
        return {
            **result,
            "error_code": "OSS_NOT_CONFIGURED",
            "message": "请在后端环境配置 RAM 密钥和角色 ARN",
        }
    cloud = OSSStorage(settings, runtime.security, runtime.cache)
    try:
        cloud.sts.server()
        result["sts"] = "ok"
        cloud.check_ready()
        result["private_bucket"] = "ok"
        result["message"] = "STS 签发和私有 Bucket 检查通过；浏览器上传仍需配置 CORS"
    except BusinessError as exc:
        result.update(error_code=exc.code, message=exc.message)
        result["private_bucket" if result["sts"] == "ok" else "sts"] = "error"
    return result
