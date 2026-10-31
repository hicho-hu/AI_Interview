from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Header, Query, Request, WebSocket
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from app.api.dependencies import current_user, db_session, ok, service
from app.db.models import Interview, Task
from app.modules.interview.service import InterviewService
from app.modules.privacy.service import PrivacyService
from app.schemas.contracts import Answer, Envelope, Finish, InterviewInput
from app.workers.processor import retry_task

router = APIRouter(tags=["面试与报告"])


@router.websocket("/interviews/{interview_id}/live-transcription")
async def live_transcription(websocket: WebSocket, interview_id: str):
    from app.modules.interview.transcription import transcribe_socket

    await transcribe_socket(websocket, interview_id)


@router.post("/interviews", response_model=Envelope, status_code=201)
def create(
    data: InterviewInput,
    request: Request,
    idempotency_key: str = Header(...),
    db=Depends(db_session, scope="function"),
    user=Depends(current_user),
):
    return ok(request, service(InterviewService, request, db, user).create(data, idempotency_key))


@router.get("/interviews", response_model=Envelope)
def history(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    job_profile_id: Optional[str] = None,
    status: Optional[str] = None,
    from_time: Optional[datetime] = None,
    to_time: Optional[datetime] = None,
    db=Depends(db_session, scope="function"),
    user=Depends(current_user),
):
    from app.db.repository import page_query

    stmt = select(Interview).where(Interview.user_id == user.id, Interview.deleted_at.is_(None))
    if job_profile_id:
        stmt = stmt.where(Interview.job_profile_id == job_profile_id)
    if status:
        stmt = stmt.where(Interview.status == status)
    if from_time:
        stmt = stmt.where(Interview.created_at >= from_time)
    if to_time:
        stmt = stmt.where(Interview.created_at <= to_time)
    rows, total = page_query(db, stmt.order_by(Interview.created_at.desc()), page, page_size)
    svc = service(InterviewService, request, db, user)
    return ok(
        request, {"items": [svc.view(r) for r in rows], "total": total, "page": page, "page_size": page_size}
    )


@router.get("/interviews/{interview_id}", response_model=Envelope)
def detail(
    interview_id: str, request: Request, db=Depends(db_session, scope="function"), user=Depends(current_user)
):
    svc = service(InterviewService, request, db, user)
    return ok(request, svc.view(svc.get(interview_id)))


@router.post("/interviews/{interview_id}/start", response_model=Envelope)
def start(
    interview_id: str, request: Request, db=Depends(db_session, scope="function"), user=Depends(current_user)
):
    svc = service(InterviewService, request, db, user)
    return ok(request, svc.start(svc.get(interview_id)))


@router.post("/interviews/{interview_id}/pause", response_model=Envelope)
def pause(
    interview_id: str, request: Request, db=Depends(db_session, scope="function"), user=Depends(current_user)
):
    svc = service(InterviewService, request, db, user)
    return ok(request, svc.pause(svc.get(interview_id)))


@router.post("/interviews/{interview_id}/resume", response_model=Envelope)
def resume(
    interview_id: str, request: Request, db=Depends(db_session, scope="function"), user=Depends(current_user)
):
    svc = service(InterviewService, request, db, user)
    return ok(request, svc.resume(svc.get(interview_id)))


@router.post("/interviews/{interview_id}/cancel", response_model=Envelope)
def cancel(
    interview_id: str, request: Request, db=Depends(db_session, scope="function"), user=Depends(current_user)
):
    svc = service(InterviewService, request, db, user)
    return ok(request, svc.cancel(svc.get(interview_id)))


@router.post("/interviews/{interview_id}/retry", response_model=Envelope)
def retry(
    interview_id: str, request: Request, db=Depends(db_session, scope="function"), user=Depends(current_user)
):
    svc = service(InterviewService, request, db, user)
    row = svc.get(interview_id)
    task = db.get(Task, row.report_task_id if row.status == "REPORT_FAILED" else row.prepare_task_id)
    from app.core.errors import require

    require(task is not None, "TASK_NOT_FOUND", "没有可重试任务", 404)
    return ok(request, retry_task(svc.rt, db, task, user.id))


@router.post("/interviews/{interview_id}/finish", response_model=Envelope, status_code=202)
def finish(
    interview_id: str,
    data: Finish,
    request: Request,
    idempotency_key: str = Header(...),
    db=Depends(db_session, scope="function"),
    user=Depends(current_user),
):
    svc = service(InterviewService, request, db, user)
    return ok(request, svc.finish(svc.get(interview_id), data.reason, idempotency_key))


@router.get("/interviews/{interview_id}/turns", response_model=Envelope)
def turns(
    interview_id: str, request: Request, db=Depends(db_session, scope="function"), user=Depends(current_user)
):
    svc = service(InterviewService, request, db, user)
    return ok(request, {"items": [svc.turn_view(t) for t in svc.turns(svc.get(interview_id))]})


@router.post("/interviews/{interview_id}/turns/{turn_id}/submit", response_model=Envelope, status_code=202)
def answer(
    interview_id: str,
    turn_id: str,
    data: Answer,
    request: Request,
    idempotency_key: str = Header(...),
    db=Depends(db_session, scope="function"),
    user=Depends(current_user),
):
    svc = service(InterviewService, request, db, user)
    return ok(request, svc.submit(svc.get(interview_id), turn_id, data, idempotency_key))


@router.get("/interviews/{interview_id}/report", response_model=Envelope)
def report(
    interview_id: str,
    request: Request,
    version: Optional[int] = Query(None, ge=1),
    db=Depends(db_session, scope="function"),
    user=Depends(current_user),
):
    svc = service(InterviewService, request, db, user)
    return ok(request, svc.report(svc.get(interview_id), version))


@router.post("/interviews/{interview_id}/turns/{turn_id}/submit-stream")
def answer_stream(
    interview_id: str,
    turn_id: str,
    data: Answer,
    request: Request,
    idempotency_key: str = Header(...),
    db=Depends(db_session, scope="function"),
    user=Depends(current_user),
):
    from app.modules.interview.live import answer_events

    svc = service(InterviewService, request, db, user)
    result = svc.submit(svc.get(interview_id), turn_id, data, idempotency_key)
    # Reserve this task for the immediate stream runner. If the API dies before
    # starting it, the normal outbox recovers it after the short lease expires.
    from datetime import timedelta

    from app.core.security import utcnow

    task = db.get(Task, result["task_id"])
    if task.status == "PENDING":
        task.status = "QUEUED"
        task.lease_until = utcnow() + timedelta(seconds=30)
    return StreamingResponse(
        answer_events(svc.rt, user.id, interview_id, result["task_id"], request.state.request_id),
        media_type="text/event-stream",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-store"},
    )


@router.post("/interviews/{interview_id}/report/regenerate", response_model=Envelope, status_code=202)
def regenerate(
    interview_id: str,
    request: Request,
    idempotency_key: str = Header(...),
    db=Depends(db_session, scope="function"),
    user=Depends(current_user),
):
    svc = service(InterviewService, request, db, user)
    return ok(request, svc.regenerate(svc.get(interview_id), idempotency_key))


@router.delete("/interviews/{interview_id}", response_model=Envelope, status_code=202)
def delete_interview(
    interview_id: str, request: Request, db=Depends(db_session, scope="function"), user=Depends(current_user)
):
    svc = service(InterviewService, request, db, user)
    row = svc.get(interview_id)
    if row.status not in ("CANCELLED", "COMPLETED", "REPORT_FAILED", "FINISHING"):
        svc.cancel(row)
    service(PrivacyService, request, db, user).erase_interview(row)
    return ok(request, {"deleted": True})
