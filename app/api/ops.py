from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select

from app.api.dependencies import admin_user, current_user, db_session, ok, service
from app.db.models import AICallLog, AuditLog, Feedback, Interview, User
from app.db.repository import owned, page_query
from app.modules.shared import Service
from app.schemas.contracts import Envelope, FeedbackInput, FeedbackReply

router = APIRouter(tags=["运营与反馈"])


@router.post("/feedback", response_model=Envelope, status_code=201)
def feedback(
    data: FeedbackInput,
    request: Request,
    db=Depends(db_session, scope="function"),
    user=Depends(current_user),
):
    if data.interview_id:
        owned(db, Interview, data.interview_id, user.id)
    svc = service(Service, request, db, user)
    row = Feedback(
        user_id=user.id,
        interview_id=data.interview_id,
        category=data.category,
        content_encrypted=svc.rt.security.encrypt(data.content),
    )
    db.add(row)
    db.flush()
    svc.audit("FEEDBACK_CREATED", row.id)
    return ok(request, {"id": row.id, "status": row.status})


@router.get("/feedback", response_model=Envelope)
def my_feedback(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db=Depends(db_session, scope="function"),
    user=Depends(current_user),
):
    rows, total = page_query(
        db,
        select(Feedback).where(Feedback.user_id == user.id).order_by(Feedback.created_at.desc()),
        page,
        page_size,
    )
    sec = request.app.state.runtime.security
    return ok(
        request,
        {
            "items": [
                {
                    "id": r.id,
                    "category": r.category,
                    "status": r.status,
                    "content": sec.decrypt(r.content_encrypted),
                    "reply": sec.decrypt(r.reply_encrypted),
                }
                for r in rows
            ],
            "total": total,
        },
    )


@router.patch("/admin/feedback/{feedback_id}", response_model=Envelope)
def reply(
    feedback_id: str,
    data: FeedbackReply,
    request: Request,
    db=Depends(db_session, scope="function"),
    user=Depends(admin_user),
):
    from app.core.errors import require

    row = db.get(Feedback, feedback_id)
    require(row, "RESOURCE_NOT_FOUND", "反馈不存在", 404)
    row.status = data.status
    row.reply_encrypted = request.app.state.runtime.security.encrypt(data.reply)
    service(Service, request, db, user).audit("FEEDBACK_REPLIED", row.id)
    return ok(request, {"id": row.id, "status": row.status})


@router.get("/admin/stats", response_model=Envelope)
def stats(request: Request, db=Depends(db_session, scope="function"), user=Depends(admin_user)):
    counts = dict(db.execute(select(Interview.status, func.count()).group_by(Interview.status)).all())
    return ok(
        request,
        {
            "users": db.scalar(select(func.count()).select_from(User)),
            "interviews": counts,
            "ai_calls": db.scalar(select(func.count()).select_from(AICallLog)),
            "ai_cost_microunits": db.scalar(select(func.coalesce(func.sum(AICallLog.cost_microunits), 0))),
        },
    )


@router.get("/admin/ai-calls", response_model=Envelope)
def ai_calls(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db=Depends(db_session, scope="function"),
    user=Depends(admin_user),
):
    rows, total = page_query(db, select(AICallLog).order_by(AICallLog.created_at.desc()), page, page_size)
    return ok(
        request,
        {
            "items": [
                {
                    k: getattr(r, k)
                    for k in (
                        "id",
                        "user_id",
                        "interview_id",
                        "turn_id",
                        "provider",
                        "model",
                        "call_type",
                        "input_tokens",
                        "output_tokens",
                        "latency_ms",
                        "cost_microunits",
                        "units",
                        "status",
                        "request_id",
                    )
                }
                for r in rows
            ],
            "total": total,
        },
    )


@router.get("/admin/audit-logs", response_model=Envelope)
def audits(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db=Depends(db_session, scope="function"),
    user=Depends(admin_user),
):
    rows, total = page_query(db, select(AuditLog).order_by(AuditLog.created_at.desc()), page, page_size)
    return ok(
        request,
        {
            "items": [
                {
                    k: getattr(r, k)
                    for k in ("id", "user_id", "action", "resource_id", "request_id", "created_at")
                }
                for r in rows
            ],
            "total": total,
        },
    )
