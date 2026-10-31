from fastapi import APIRouter, Depends, Request
from sqlalchemy import select

from app.api.dependencies import current_user, db_session, ok, service
from app.db.models import Consent
from app.modules.identity.service import IdentityService
from app.modules.privacy.service import PrivacyService
from app.schemas.contracts import ConsentInput, DevLogin, Envelope, Login, UserPatch

router = APIRouter(tags=["身份与用户"])


@router.post("/auth/wechat-login", response_model=Envelope)
def wechat_login(data: Login, request: Request, db=Depends(db_session, scope="function")):
    return ok(request, IdentityService(request.app.state.runtime, db).login(data.code, data.nickname))


@router.post("/auth/dev-login", response_model=Envelope)
def dev_login(data: DevLogin, request: Request, db=Depends(db_session, scope="function")):
    return ok(
        request, IdentityService(request.app.state.runtime, db).login(data.identity, data.nickname, True)
    )


@router.post("/auth/logout", response_model=Envelope)
def logout(request: Request, db=Depends(db_session, scope="function"), user=Depends(current_user)):
    return ok(request, service(IdentityService, request, db, user).logout(request.state.claims))


@router.get("/users/me", response_model=Envelope)
def me(request: Request, db=Depends(db_session, scope="function"), user=Depends(current_user)):
    return ok(request, service(IdentityService, request, db, user).view(user))


@router.patch("/users/me", response_model=Envelope)
def patch_me(
    data: UserPatch, request: Request, db=Depends(db_session, scope="function"), user=Depends(current_user)
):
    user.nickname = data.nickname
    svc = service(IdentityService, request, db, user)
    svc.audit("PROFILE_UPDATED", user.id)
    return ok(request, svc.view(user))


@router.get("/users/me/consents", response_model=Envelope)
def consents(request: Request, db=Depends(db_session, scope="function"), user=Depends(current_user)):
    rows = db.scalars(select(Consent).where(Consent.user_id == user.id).order_by(Consent.created_at)).all()
    return ok(
        request,
        {
            "policy_version": request.app.state.runtime.settings.policy_version,
            "items": [
                {
                    "id": r.id,
                    "consent_type": r.consent_type,
                    "policy_version": r.policy_version,
                    "consented_at": r.created_at,
                    "revoked_at": r.revoked_at,
                }
                for r in rows
            ],
        },
    )


@router.post("/users/me/consents", response_model=Envelope)
def consent(
    data: ConsentInput, request: Request, db=Depends(db_session, scope="function"), user=Depends(current_user)
):
    return ok(
        request, service(IdentityService, request, db, user).consent(data.consent_type, data.policy_version)
    )


@router.delete("/users/me/consents/{kind}", response_model=Envelope)
def revoke(kind: str, request: Request, db=Depends(db_session, scope="function"), user=Depends(current_user)):
    return ok(request, service(IdentityService, request, db, user).revoke(kind))


@router.get("/users/me/data", response_model=Envelope)
def export_data(request: Request, db=Depends(db_session, scope="function"), user=Depends(current_user)):
    # Paginated domain endpoints expose full histories. This endpoint contains the profile/export index.
    svc = service(IdentityService, request, db, user)
    svc.audit("PERSONAL_DATA_QUERIED", user.id)
    return ok(
        request,
        {
            "user": svc.view(user),
            "exports": {
                "consents": "/api/v1/users/me/consents",
                "jobs": "/api/v1/job-profiles",
                "resumes": "/api/v1/resumes",
                "interviews": "/api/v1/interviews",
                "usage": "/api/v1/billing/usage",
                "orders": "/api/v1/billing/orders",
            },
        },
    )


@router.delete("/users/me/data", response_model=Envelope, status_code=202)
def erase_data(request: Request, db=Depends(db_session, scope="function"), user=Depends(current_user)):
    return ok(request, service(PrivacyService, request, db, user).request_deletion())
