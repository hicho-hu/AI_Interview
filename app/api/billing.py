from fastapi import APIRouter, Depends, Header, Query, Request
from sqlalchemy import func, select

from app.api.dependencies import current_user, db_session, ok, service
from app.db.models import AICallLog, Order, Usage
from app.db.repository import owned, page_query
from app.modules.billing.service import PRODUCTS, BillingService
from app.schemas.contracts import Envelope, OrderInput

router = APIRouter(prefix="/billing", tags=["权益与订单"])


def catalog(db):
    from app.db.admin_models import Product
    rows = db.scalars(select(Product).where(Product.status == "PUBLISHED")).all()
    configured = {r.code: {"code": r.code, "name": r.name, "amount_minor": r.amount_minor,
                          "credits": r.credits, "currency": r.currency, "version": r.version} for r in rows}
    return list(configured.values()) + [{"code": k, **v, "currency": "CNY"} for k, v in PRODUCTS.items() if k not in configured]


@router.get("/products", response_model=Envelope)
def products(request: Request, db=Depends(db_session, scope="function"), user=Depends(current_user)):
    return ok(
        request,
        {
            "items": catalog(db),
            "payment_enabled": False,
            "development_mock_payment": request.app.state.runtime.settings.environment != "production",
        },
    )


@router.get("/entitlements", response_model=Envelope)
def entitlements(request: Request, db=Depends(db_session, scope="function"), user=Depends(current_user)):
    reserved = db.scalar(
        select(func.count()).select_from(Usage).where(Usage.user_id == user.id, Usage.status == "RESERVED")
    )
    return ok(request, {"remaining_interviews": user.credits, "reserved_interviews": reserved})


@router.get("/usage", response_model=Envelope)
def usage(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db=Depends(db_session, scope="function"),
    user=Depends(current_user),
):
    rows, total = page_query(
        db, select(Usage).where(Usage.user_id == user.id).order_by(Usage.created_at.desc()), page, page_size
    )
    cost = db.scalar(
        select(func.coalesce(func.sum(AICallLog.cost_microunits), 0)).where(AICallLog.user_id == user.id)
    )
    return ok(
        request,
        {
            "items": [
                {
                    "id": r.id,
                    "interview_id": r.interview_id,
                    "usage_type": r.usage_type,
                    "quantity": r.quantity,
                    "status": r.status,
                    "amount_minor": r.amount_minor,
                    "created_at": str(r.created_at),
                }
                for r in rows
            ],
            "total": total,
            "page": page,
            "page_size": page_size,
            "ai_cost_microunits": cost,
        },
    )


@router.post("/orders", response_model=Envelope, status_code=201)
def create_order(
    data: OrderInput,
    request: Request,
    idempotency_key: str = Header(...),
    db=Depends(db_session, scope="function"),
    user=Depends(current_user),
):
    return ok(request, service(BillingService, request, db, user).order(data, idempotency_key))


@router.get("/orders", response_model=Envelope)
def orders(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db=Depends(db_session, scope="function"),
    user=Depends(current_user),
):
    rows, total = page_query(
        db, select(Order).where(Order.user_id == user.id).order_by(Order.created_at.desc()), page, page_size
    )
    return ok(
        request,
        {
            "items": [BillingService.order_view(r) for r in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
        },
    )


@router.get("/orders/{order_id}", response_model=Envelope)
def order(
    order_id: str, request: Request, db=Depends(db_session, scope="function"), user=Depends(current_user)
):
    return ok(request, BillingService.order_view(owned(db, Order, order_id, user.id)))


@router.post("/orders/{order_id}/dev-pay", response_model=Envelope)
def dev_pay(
    order_id: str,
    request: Request,
    idempotency_key: str = Header(...),
    db=Depends(db_session, scope="function"),
    user=Depends(current_user),
):
    return ok(request, service(BillingService, request, db, user).pay(order_id, idempotency_key))


@router.post("/orders/{order_id}/refund", response_model=Envelope)
def refund(
    order_id: str,
    request: Request,
    idempotency_key: str = Header(...),
    db=Depends(db_session, scope="function"),
    user=Depends(current_user),
):
    return ok(request, service(BillingService, request, db, user).refund(order_id, idempotency_key))
