from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Header, Query, Request
from sqlalchemy import select

from app.api.dependencies import db_session, ok
from app.core.errors import BusinessError, require
from app.core.security import utcnow
from app.db.admin_models import (
    AdminAudit,
    AdminUser,
    Product,
    QualityReview,
    Reconciliation,
    ReconciliationItem,
    RefundRequest,
)
from app.db.admin_repository import fields, interview_list, listing, user_list
from app.db.models import Order
from app.modules.admin.auth import AdminAuth
from app.modules.admin.metrics import Metrics
from app.modules.admin.security import PERMISSIONS, ROLE_LABELS, permits
from app.modules.admin.service import (
    ORDER_FIELDS,
    PRODUCT_FIELDS,
    QUALITY_FIELDS,
    AdminService,
)
from app.schemas import admin as schema
from app.schemas.contracts import Envelope

router = APIRouter(prefix="/admin", tags=["运营管理后台"])


def current_admin(
    request: Request, authorization: str = Header(""), db=Depends(db_session, scope="function")
):
    require(authorization.startswith("Bearer "), "ADMIN_UNAUTHORIZED", "请登录管理后台", 401)
    auth = AdminAuth(request.app.state.runtime, db, request)
    admin, session = auth.authenticate(authorization[7:])
    request.state.admin_session = session
    return admin


def access(permission):
    def dependency(request: Request, admin=Depends(current_admin)):
        if not permits(admin, permission):
            with request.app.state.runtime.db.session() as log_db:
                AdminAuth(request.app.state.runtime, log_db, request).audit(
                    admin.id, "ACCESS_DENIED", permission, result="DENIED"
                )
            require(False, "ADMIN_FORBIDDEN", "当前角色没有该操作权限", 403)
        return admin

    return dependency


def svc(request: Request, db=Depends(db_session, scope="function"), admin=Depends(current_admin)):
    return AdminService(request.app.state.runtime, db, admin, request)


def sensitive_review(service, resource_id, operation):
    try:
        return operation()
    except BusinessError as exc:
        with service.rt.db.session() as log_db:
            AdminAuth(service.rt, log_db, service.request).audit(
                service.admin.id, "SENSITIVE_REVIEW_DENIED", resource_id, exc.code, result="DENIED"
            )
        raise


def paging(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    return {"page": page, "page_size": page_size}


def metrics(
    start: Optional[date] = None,
    end: Optional[date] = None,
    timezone: str = "Asia/Shanghai",
    db=Depends(db_session, scope="function"),
):
    return Metrics(db, start, end, timezone)


@router.post("/auth/login", response_model=Envelope)
def login(data: schema.AdminLogin, request: Request, db=Depends(db_session, scope="function")):
    return ok(request, AdminAuth(request.app.state.runtime, db, request).login(data))


@router.get("/auth/me", response_model=Envelope)
def me(request: Request, admin=Depends(current_admin)):
    return ok(
        request, {**AdminAuth.view(admin), "environment": request.app.state.runtime.settings.environment}
    )


@router.post("/auth/logout", response_model=Envelope)
def logout(request: Request, service=Depends(svc)):
    request.state.admin_session.revoked_at = utcnow()
    service.audit("LOGOUT", service.admin.id)
    return ok(request, {"logged_out": True})


@router.post("/auth/mfa/setup", response_model=Envelope)
def setup(request: Request, service=Depends(svc)):
    return ok(request, service.auth.mfa_setup(service.admin))


@router.post("/auth/mfa/confirm", response_model=Envelope)
def confirm(data: schema.MFAConfirm, request: Request, service=Depends(svc)):
    return ok(request, service.auth.mfa_confirm(service.admin, data))


@router.get("/dashboard/overview", response_model=Envelope, dependencies=[Depends(access("dashboard:read"))])
def overview(request: Request, m=Depends(metrics)):
    return ok(request, m.overview())


@router.get("/dashboard/trends", response_model=Envelope, dependencies=[Depends(access("dashboard:read"))])
def trends(request: Request, m=Depends(metrics)):
    return ok(request, m.trends())


@router.get("/users", response_model=Envelope, dependencies=[Depends(access("user:read"))])
def users(
    request: Request,
    q: str = Query("", max_length=120),
    status: str = "",
    p=Depends(paging),
    service=Depends(svc),
):
    return ok(request, user_list(service.db, q, status, **p))


@router.get("/users/{uid}", response_model=Envelope, dependencies=[Depends(access("user:read"))])
def user(request: Request, uid: str, service=Depends(svc)):
    return ok(request, service.user_detail(uid))


@router.patch("/users/{uid}/status", response_model=Envelope, dependencies=[Depends(access("user:update"))])
def status(
    request: Request,
    uid: str,
    data: schema.UserStatus,
    idempotency_key: str = Header(...),
    service=Depends(svc),
):
    return ok(
        request,
        service.idem(
            "user:status:" + uid, idempotency_key, data.model_dump(), lambda: service.user_status(uid, data)
        ),
    )


@router.post(
    "/users/{uid}/entitlements", response_model=Envelope, dependencies=[Depends(access("entitlement:grant"))]
)
def grant(
    request: Request,
    uid: str,
    data: schema.EntitlementGrant,
    idempotency_key: str = Header(...),
    service=Depends(svc),
):
    return ok(
        request,
        service.idem(
            "user:grant:direct:" + uid, idempotency_key, data.model_dump(), lambda: service.grant(uid, data)
        ),
    )


@router.get("/interviews", response_model=Envelope, dependencies=[Depends(access("interview:read"))])
def interviews(
    request: Request,
    q: str = Query("", max_length=120),
    status: str = "",
    mode: str = "",
    p=Depends(paging),
    service=Depends(svc),
):
    return ok(request, interview_list(service.db, q, status, mode, **p))


@router.get("/interviews/{iid}", response_model=Envelope, dependencies=[Depends(access("interview:read"))])
def interview(request: Request, iid: str, service=Depends(svc)):
    return ok(request, service.interview_detail(iid))


@router.post(
    "/interviews/{iid}/review", response_model=Envelope, dependencies=[Depends(access("interview:content"))]
)
def review_content(request: Request, iid: str, data: schema.Reason, service=Depends(svc)):
    return ok(request, sensitive_review(service, iid, lambda: service.content(iid, data.reason)))


@router.post(
    "/interviews/{iid}/retry-report", response_model=Envelope, dependencies=[Depends(access("report:retry"))]
)
def retry_report(
    request: Request, iid: str, data: schema.Reason, idempotency_key: str = Header(...), service=Depends(svc)
):
    return ok(
        request,
        service.idem(
            "report:retry:" + iid,
            idempotency_key,
            data.model_dump(),
            lambda: service.retry_report(iid, data.reason, idempotency_key),
        ),
    )


@router.post(
    "/media/{mid}/playback-grant", response_model=Envelope, dependencies=[Depends(access("media:play"))]
)
def playback(request: Request, mid: str, data: schema.Reason, service=Depends(svc)):
    return ok(request, sensitive_review(service, mid, lambda: service.playback(mid, data.reason)))


@router.get("/quality-tasks", response_model=Envelope, dependencies=[Depends(access("quality:read"))])
def quality(request: Request, p=Depends(paging), service=Depends(svc)):
    conditions = (QualityReview.assignee_id == service.admin.id,) if service.admin.role == "QUALITY" else ()
    return ok(
        request,
        listing(service.db, QualityReview, lambda r: fields(r, QUALITY_FIELDS), **p, conditions=conditions),
    )


@router.get("/quality-assignees", response_model=Envelope, dependencies=[Depends(access("quality:assign"))])
def assignees(request: Request, service=Depends(svc)):
    rows = service.db.scalars(
        select(AdminUser).where(AdminUser.status == "ACTIVE", AdminUser.role.in_(["QUALITY", "SUPER_ADMIN"]))
    ).all()
    return ok(request, {"items": [fields(r, "id display_name role") for r in rows]})


@router.post("/quality-tasks", response_model=Envelope, dependencies=[Depends(access("quality:assign"))])
def quality_create(
    request: Request, data: schema.QualityInput, idempotency_key: str = Header(...), service=Depends(svc)
):
    return ok(
        request,
        service.idem(
            "quality:create", idempotency_key, data.model_dump(), lambda: service.quality_create(data)
        ),
    )


@router.patch(
    "/quality-tasks/{qid}", response_model=Envelope, dependencies=[Depends(access("quality:update"))]
)
def quality_update(
    request: Request,
    qid: str,
    data: schema.QualityUpdate,
    idempotency_key: str = Header(...),
    service=Depends(svc),
):
    return ok(
        request,
        service.idem(
            "quality:update:" + qid,
            idempotency_key,
            data.model_dump(),
            lambda: service.quality_update(qid, data),
        ),
    )


@router.get("/finance/summary", response_model=Envelope, dependencies=[Depends(access("finance:read"))])
def finance(request: Request, m=Depends(metrics)):
    return ok(request, m.finance())


@router.get("/orders", response_model=Envelope, dependencies=[Depends(access("finance:read"))])
def orders(
    request: Request,
    q: str = Query("", max_length=120),
    status: str = "",
    p=Depends(paging),
    service=Depends(svc),
):
    conditions = []
    if q:
        conditions.append(Order.id.contains(q, autoescape=True))
    if status:
        conditions.append(Order.status == status)
    return ok(
        request, listing(service.db, Order, lambda r: fields(r, ORDER_FIELDS), **p, conditions=conditions)
    )


@router.get("/refunds", response_model=Envelope, dependencies=[Depends(access("finance:read"))])
def refunds(request: Request, p=Depends(paging), service=Depends(svc)):
    return ok(
        request,
        listing(
            service.db,
            RefundRequest,
            lambda r: fields(r, "id order_id amount_minor reason status created_at"),
            **p,
        ),
    )


@router.post("/refunds", response_model=Envelope, dependencies=[Depends(access("order:refund"))])
def refund(
    request: Request, data: schema.RefundInput, idempotency_key: str = Header(...), service=Depends(svc)
):
    return ok(
        request,
        service.idem("refund:direct", idempotency_key, data.model_dump(), lambda: service.refund(data)),
    )


@router.get("/products", response_model=Envelope, dependencies=[Depends(access("product:read"))])
def products(request: Request, p=Depends(paging), service=Depends(svc)):
    return ok(request, listing(service.db, Product, lambda r: fields(r, PRODUCT_FIELDS), **p))


@router.post("/products", response_model=Envelope, dependencies=[Depends(access("product:write"))])
def product(
    request: Request, data: schema.ProductInput, idempotency_key: str = Header(...), service=Depends(svc)
):
    return ok(
        request,
        service.idem("product:create", idempotency_key, data.model_dump(), lambda: service.product(data)),
    )


@router.post(
    "/products/{pid}/publish", response_model=Envelope, dependencies=[Depends(access("product:write"))]
)
def publish(
    request: Request, pid: str, data: schema.Reason, idempotency_key: str = Header(...), service=Depends(svc)
):
    return ok(
        request,
        service.idem(
            "product:publish:direct:" + pid,
            idempotency_key,
            data.model_dump(),
            lambda: service.publish(pid, data.reason),
        ),
    )


@router.get("/payment-channels", response_model=Envelope, dependencies=[Depends(access("payment:read"))])
def payments(request: Request, service=Depends(svc)):
    return ok(request, service.payments())


@router.put(
    "/payment-channels/{channel}", response_model=Envelope, dependencies=[Depends(access("payment:write"))]
)
def payment(
    request: Request,
    channel: str,
    data: schema.PaymentInput,
    idempotency_key: str = Header(...),
    service=Depends(svc),
):
    return ok(
        request,
        service.idem(
            "payment:direct:" + channel,
            idempotency_key,
            data.model_dump(),
            lambda: service.payment(channel, data),
        ),
    )


@router.post(
    "/payment-channels/{channel}/validate",
    response_model=Envelope,
    dependencies=[Depends(access("payment:write"))],
)
def validate_payment(request: Request, channel: str, service=Depends(svc)):
    require(channel in ("WECHAT", "ALIPAY"), "INVALID_CHANNEL", "渠道不存在", 404)
    configured = any(p["channel"] == channel for p in service.payments()["items"])
    service.audit("PAYMENT_VALIDATED", channel)
    return ok(
        request,
        {
            "configured": configured,
            "adapter_ready": False,
            "message": "真实渠道适配器未配置；当前仅能校验配置是否存在",
        },
    )


@router.get("/reconciliations", response_model=Envelope, dependencies=[Depends(access("finance:read"))])
def reconciliations(request: Request, p=Depends(paging), service=Depends(svc)):
    return ok(
        request,
        listing(
            service.db,
            Reconciliation,
            lambda r: fields(r, "id channel bill_date status total_rows difference_count created_at"),
            **p,
        ),
    )


@router.get(
    "/reconciliations/{bid}/items", response_model=Envelope, dependencies=[Depends(access("finance:read"))]
)
def reconciliation_items(request: Request, bid: str, p=Depends(paging), service=Depends(svc)):
    return ok(
        request,
        listing(
            service.db,
            ReconciliationItem,
            lambda r: fields(r, "id order_id difference_type amount_minor status resolution created_at"),
            **p,
            conditions=(ReconciliationItem.batch_id == bid,),
        ),
    )


@router.post(
    "/reconciliations/run", response_model=Envelope, dependencies=[Depends(access("reconciliation:write"))]
)
def reconcile(
    request: Request,
    data: schema.ReconciliationInput,
    idempotency_key: str = Header(...),
    service=Depends(svc),
):
    return ok(
        request,
        service.idem(
            "reconciliation:run", idempotency_key, data.model_dump(), lambda: service.reconciliation(data)
        ),
    )


@router.patch(
    "/reconciliation-items/{rid}",
    response_model=Envelope,
    dependencies=[Depends(access("reconciliation:write"))],
)
def resolve(
    request: Request, rid: str, data: schema.Reason, idempotency_key: str = Header(...), service=Depends(svc)
):
    def operation():
        row = service.get(ReconciliationItem, rid)
        row.status, row.resolution = "RESOLVED", data.reason
        service.audit("RECONCILIATION_RESOLVED", rid, data.reason)
        return {"id": rid, "status": row.status}

    return ok(
        request, service.idem("reconciliation:resolve:" + rid, idempotency_key, data.model_dump(), operation)
    )


@router.get("/ai/costs", response_model=Envelope, dependencies=[Depends(access("ai:read"))])
def ai(request: Request, m=Depends(metrics)):
    return ok(request, m.ai(request.app.state.runtime.settings))


@router.get("/admin-users", response_model=Envelope, dependencies=[Depends(access("system:read"))])
def admins(request: Request, p=Depends(paging), service=Depends(svc)):
    return ok(request, listing(service.db, AdminUser, AdminAuth.view, **p))


@router.post("/admin-users", response_model=Envelope, dependencies=[Depends(access("admin:write"))])
def create(
    request: Request, data: schema.AdminCreate, idempotency_key: str = Header(...), service=Depends(svc)
):
    return ok(
        request,
        service.idem("admin:create", idempotency_key, data.model_dump(), lambda: service.admin_create(data)),
    )


@router.patch("/admin-users/{aid}", response_model=Envelope, dependencies=[Depends(access("admin:write"))])
def change(
    request: Request,
    aid: str,
    data: schema.AdminUpdate,
    idempotency_key: str = Header(...),
    service=Depends(svc),
):
    return ok(
        request,
        service.idem(
            "admin:update:" + aid, idempotency_key, data.model_dump(), lambda: service.admin_update(aid, data)
        ),
    )


@router.get("/roles", response_model=Envelope, dependencies=[Depends(access("system:read"))])
def roles(request: Request):
    return ok(
        request,
        {
            "items": [
                {"role": r, "label": ROLE_LABELS[r], "permissions": sorted(p)} for r, p in PERMISSIONS.items()
            ]
        },
    )


@router.get("/audit-logs", response_model=Envelope, dependencies=[Depends(access("audit:read"))])
def audits(request: Request, q: str = Query("", max_length=120), p=Depends(paging), service=Depends(svc)):
    conditions = (AdminAudit.action.contains(q, autoescape=True),) if q else ()
    return ok(
        request,
        listing(
            service.db,
            AdminAudit,
            lambda r: fields(r, "id admin_id action resource_id reason result ip request_id created_at"),
            **p,
            conditions=conditions,
        ),
    )
