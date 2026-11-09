from fastapi import APIRouter, Depends, Query, Request, Response

from app.api.dependencies import current_user, db_session, ok
from app.core.errors import require
from app.core.security import utcnow
from app.db.models import MediaAsset
from app.db.repository import owned
from app.infrastructure.storage.sts_service import generate_sts_token
from app.modules.identity.service import IdentityService
from app.modules.media.service import aware
from app.schemas.contracts import Envelope

router = APIRouter(prefix="/storage", tags=["文件存储"])


@router.get("/sts-token", response_model=Envelope)
def sts_token(
    request: Request,
    response: Response,
    asset_id: str = Query(..., min_length=1, max_length=36),
    db=Depends(db_session, scope="function"),
    user=Depends(current_user),
):
    rt = request.app.state.runtime
    require(rt.settings.storage_backend == "oss", "OSS_NOT_CONFIGURED", "当前服务未启用 OSS 上传", 503)
    asset = owned(db, MediaAsset, asset_id, user.id)
    require(
        asset.status == "PENDING_UPLOAD" and aware(asset.expires_at) > utcnow(),
        "MEDIA_INVALID_STATE",
        "文件上传凭证已完成或过期",
        409,
    )
    identity = IdentityService(rt, db, user.id)
    identity.require_consents("PRIVACY", "TERMS")
    if asset.media_type in ("AUDIO", "VIDEO", "SESSION_AUDIO", "SESSION_VIDEO"):
        identity.require_consents(
            "MICROPHONE", *(["CAMERA"] if asset.media_type in ("VIDEO", "SESSION_VIDEO") else [])
        )
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Pragma"] = "no-cache"
    return ok(request, {"asset_id": asset.id, **generate_sts_token(user.id, asset.object_key, rt)})
