from fastapi import APIRouter, Depends, Header, Query, Request

from app.api.admin import access, svc
from app.api.dependencies import current_user, db_session, ok, service
from app.modules.growth.service import GrowthService
from app.schemas import growth as schema
from app.schemas.contracts import Envelope

router = APIRouter(tags=["邀请与推广"])


@router.get("/invitations/me", response_model=Envelope)
def invitation(request: Request, db=Depends(db_session, scope="function"), user=Depends(current_user)):
    return ok(request, service(GrowthService, request, db, user).invitation_view())


@router.get(
    "/admin/invitations/config", response_model=Envelope, dependencies=[Depends(access("growth:read"))]
)
def invitation_config(request: Request, admin=Depends(svc)):
    return ok(request, GrowthService(admin.rt, admin.db).config())


@router.put(
    "/admin/invitations/config", response_model=Envelope, dependencies=[Depends(access("growth:write"))]
)
def update_config(
    data: schema.InvitationConfigInput,
    request: Request,
    idempotency_key: str = Header(...),
    admin=Depends(svc),
):
    def change():
        result = GrowthService(admin.rt, admin.db).configure(data, admin.admin.id)
        admin.audit("INVITATION_CONFIG_UPDATED", result["id"], data.reason)
        return result

    return ok(request, admin.idem("invitation:config", idempotency_key, data.model_dump(), change))


@router.get("/admin/promotions", response_model=Envelope, dependencies=[Depends(access("growth:read"))])
def promotions(
    request: Request, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), admin=Depends(svc)
):
    return ok(request, GrowthService(admin.rt, admin.db).promotions(page, page_size))


@router.post(
    "/admin/promotions",
    response_model=Envelope,
    status_code=201,
    dependencies=[Depends(access("growth:write"))],
)
def create_promotion(
    data: schema.PromotionInput, request: Request, idempotency_key: str = Header(...), admin=Depends(svc)
):
    def change():
        result = GrowthService(admin.rt, admin.db).promotion(data, admin.admin.id)
        admin.audit("PROMOTION_CREATED", result["id"], data.reason)
        return result

    return ok(request, admin.idem("promotion:create", idempotency_key, data.model_dump(), change))


@router.put(
    "/admin/promotions/{link_id}", response_model=Envelope, dependencies=[Depends(access("growth:write"))]
)
def update_promotion(
    link_id: str,
    data: schema.PromotionInput,
    request: Request,
    idempotency_key: str = Header(...),
    admin=Depends(svc),
):
    def change():
        result = GrowthService(admin.rt, admin.db).promotion(data, admin.admin.id, link_id)
        admin.audit("PROMOTION_UPDATED", result["id"], data.reason)
        return result

    return ok(request, admin.idem("promotion:update:" + link_id, idempotency_key, data.model_dump(), change))


@router.get(
    "/admin/promotions/commissions/records",
    response_model=Envelope,
    dependencies=[Depends(access("growth:read"))],
)
def commissions(
    request: Request, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), admin=Depends(svc)
):
    return ok(request, GrowthService(admin.rt, admin.db).commissions(page, page_size))


@router.post("/admin/promotions/commissions/settle", response_model=Envelope, dependencies=[Depends(access("growth:write"))])
def settle_commissions(data: schema.CommissionSettlementInput, request: Request, idempotency_key: str = Header(...), admin=Depends(svc)):
    def change():
        result = GrowthService(admin.rt, admin.db).settle_commissions(data.link_ids)
        admin.audit("PROMOTION_COMMISSIONS_SETTLED", data.link_ids[0] if len(data.link_ids) == 1 else "multiple", data.reason)
        return result
    return ok(request, admin.idem("promotion:commissions:settle", idempotency_key, data.model_dump(), change))
