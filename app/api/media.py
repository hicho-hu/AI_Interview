import hmac
import time
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy import select

from app.api.dependencies import current_user, db_session, ok, service
from app.core.errors import require
from app.core.security import utcnow
from app.db.models import MediaAsset, Task
from app.db.repository import owned, page_query
from app.modules.media.service import MediaService, aware
from app.modules.shared import Service
from app.schemas.contracts import Envelope, UploadComplete, UploadInput
from app.workers.processor import retry_task

router = APIRouter(tags=["媒体与异步任务"])


@router.get("/media", response_model=Envelope)
def list_media(
    request: Request,
    interview_id: Optional[str] = None,
    turn_id: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db=Depends(db_session, scope="function"),
    user=Depends(current_user),
):
    stmt = select(MediaAsset).where(MediaAsset.user_id == user.id, MediaAsset.status != "DELETED")
    if interview_id:
        stmt = stmt.where(MediaAsset.interview_id == interview_id)
    if turn_id:
        stmt = stmt.where(MediaAsset.turn_id == turn_id)
    rows, total = page_query(db, stmt.order_by(MediaAsset.created_at), page, page_size)
    svc = service(MediaService, request, db, user)
    return ok(
        request, {"items": [svc.view(r) for r in rows], "total": total, "page": page, "page_size": page_size}
    )


@router.post("/media/upload-url", response_model=Envelope, status_code=201)
def upload(
    data: UploadInput, request: Request, db=Depends(db_session, scope="function"), user=Depends(current_user)
):
    return ok(request, service(MediaService, request, db, user).upload(data))


@router.post("/media/{asset_id}/complete", response_model=Envelope)
def complete(
    asset_id: str,
    data: UploadComplete,
    request: Request,
    db=Depends(db_session, scope="function"),
    user=Depends(current_user),
):
    return ok(request, service(MediaService, request, db, user).complete(asset_id, data))


@router.get("/media/{asset_id}", response_model=Envelope)
def media(
    asset_id: str, request: Request, db=Depends(db_session, scope="function"), user=Depends(current_user)
):
    return ok(
        request, service(MediaService, request, db, user).view(owned(db, MediaAsset, asset_id, user.id))
    )


@router.delete("/media/{asset_id}", response_model=Envelope, status_code=202)
def delete_media(
    asset_id: str, request: Request, db=Depends(db_session, scope="function"), user=Depends(current_user)
):
    return ok(request, service(MediaService, request, db, user).delete(asset_id))


def signed_asset(request, db, asset_id, purpose, expires, signature):
    rt = request.app.state.runtime
    require(
        rt.settings.storage_backend in ("local", "oss") and rt.settings.environment != "production",
        "RESOURCE_NOT_FOUND",
        "不存在",
        404,
    )
    require(
        expires >= int(time.time())
        and hmac.compare_digest(rt.security.sign_object(asset_id, purpose, expires), signature),
        "MEDIA_SIGNATURE_INVALID",
        "签名失效",
        403,
    )
    asset = db.get(MediaAsset, asset_id)
    require(asset and aware(asset.expires_at) > utcnow(), "MEDIA_EXPIRED", "媒体不存在或已过期", 410)
    require(
        not (rt.settings.storage_backend == "oss" and asset.object_key.startswith("ai-interview/user/")),
        "RESOURCE_NOT_FOUND",
        "不存在",
        404,
    )
    return rt, asset


@router.put("/local-objects/{asset_id}", response_model=Envelope, include_in_schema=False)
async def local_put(
    asset_id: str,
    request: Request,
    purpose: str,
    expires: int,
    signature: str,
    db=Depends(db_session, scope="function"),
):
    rt, asset = signed_asset(request, db, asset_id, purpose, expires, signature)
    require(
        purpose == "upload" and asset.status == "PENDING_UPLOAD",
        "MEDIA_INVALID_STATE",
        "上传凭证已使用或用途错误",
        409,
    )
    require(
        request.headers.get("content-type") == asset.mime_type,
        "MEDIA_INVALID_FORMAT",
        "Content-Type 不一致",
        415,
    )
    content = bytearray()
    async for part in request.stream():
        content.extend(part)
        require(len(content) <= asset.size_bytes, "MEDIA_TOO_LARGE", "上传数据超过声明大小", 413)
    require(len(content) == asset.size_bytes, "MEDIA_SIZE_MISMATCH", "上传数据大小不一致")
    target = getattr(rt.storage, "legacy", rt.storage)
    target.put(asset.object_key, bytes(content), asset.mime_type)
    return ok(request, {"uploaded": True})


@router.get("/local-objects/{asset_id}", include_in_schema=False)
def local_get(
    asset_id: str,
    request: Request,
    purpose: str,
    expires: int,
    signature: str,
    db=Depends(db_session, scope="function"),
):
    rt, asset = signed_asset(request, db, asset_id, purpose, expires, signature)
    require(purpose == "download" and asset.status == "READY", "MEDIA_INVALID_STATE", "媒体不可读取", 410)
    return FileResponse(
        rt.storage.path(asset.object_key),
        media_type=asset.mime_type,
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.get("/tasks/{task_id}", response_model=Envelope)
def task(
    task_id: str, request: Request, db=Depends(db_session, scope="function"), user=Depends(current_user)
):
    row = owned(db, Task, task_id, user.id)
    svc = service(Service, request, db, user)
    return ok(
        request,
        {
            "id": row.id,
            "status": row.status,
            "retry_count": row.retry_count,
            "error_code": row.error_code,
            "result": svc.decrypt_json(row.result_encrypted),
            "created_at": str(row.created_at),
        },
    )


@router.post("/tasks/{task_id}/retry", response_model=Envelope, status_code=202)
def retry(
    task_id: str, request: Request, db=Depends(db_session, scope="function"), user=Depends(current_user)
):
    row = owned(db, Task, task_id, user.id)
    return ok(request, retry_task(request.app.state.runtime, db, row, user.id))
