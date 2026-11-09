import logging
from datetime import timedelta

from sqlalchemy import and_, or_, select, update

from app.core.errors import BusinessError, require
from app.core.security import new_id, utcnow
from app.core.state import transition
from app.db.models import Interview, JobProfile, MediaAsset, ResumeAsset, Task, Turn, User
from app.modules.ai_engine.service import EngineService
from app.modules.billing.service import BillingService
from app.modules.job_profile.service import JobService, ResumeService
from app.modules.privacy.service import PrivacyService
from app.modules.report.service import ReportService
from app.modules.shared import Service
from app.runtime import call_buffer, checkpoint_buffer

logger = logging.getLogger(__name__)


def eligible():
    return or_(
        and_(Task.status.in_(["PENDING", "RETRYING"]), Task.available_at <= utcnow()),
        and_(Task.status.in_(["RUNNING", "QUEUED"]), Task.lease_until < utcnow()),
    )


def process_available(runtime, limit=100):
    count = 0
    for _ in range(limit):
        with runtime.db.session() as db:
            task_id = db.scalar(select(Task.id).where(eligible()).order_by(Task.created_at).limit(1))
        if not task_id:
            break
        count += int(process_task(runtime, task_id))
    return count


def process_task(rt, task_id):
    token = new_id()
    with rt.db.session() as db:
        claimed = db.execute(
            update(Task)
            .where(Task.id == task_id, or_(eligible(), Task.status == "QUEUED"))
            .values(
                status="RUNNING",
                lease_token=token,
                lease_until=utcnow() + timedelta(seconds=rt.settings.task_lease_seconds),
            )
        )
        if not claimed.rowcount:
            return False
    logs = []
    checkpoints = []
    context_token = call_buffer.set(logs)
    checkpoint_token = checkpoint_buffer.set(checkpoints)
    try:
        with rt.db.session() as db:
            task = db.scalar(
                select(Task).where(Task.id == task_id, Task.lease_token == token).with_for_update()
            )
            if not task or task.status != "RUNNING":
                return False
            svc = Service(rt, db, task.user_id, task.request_id)
            user = db.get(User, task.user_id)
            if task.event_type not in ("DELETE_USER", "DELETE_MEDIA") and user.status != "ACTIVE":
                task.status = "CANCELLED"
                return True
            result = dispatch(rt, db, task, svc)
            task.result_encrypted = svc.encrypt_json(result)
            task.status, task.error_code, task.lease_until = "SUCCEEDED", None, None
            db.add_all(logs)
    except Exception as exc:
        code = exc.code if isinstance(exc, BusinessError) else "TASK_INTERNAL_ERROR"
        logger.warning("task_failed", extra={"task_id": task_id, "error_code": code})
        with rt.db.session() as db:
            task = db.scalar(
                select(Task).where(Task.id == task_id, Task.lease_token == token).with_for_update()
            )
            if task:
                task.retry_count += 1
                task.error_code = code
                task.lease_until = None
                task.status = "FAILED" if task.retry_count >= 3 else "RETRYING"
                task.available_at = utcnow() + timedelta(seconds=2**task.retry_count)
                if task.status == "FAILED":
                    mark_failed(rt, db, task)
                for turn_id, transcript, confidence in checkpoints:
                    turn = db.get(Turn, turn_id)
                    if turn and turn.status in ("SUBMITTED", "FAILED") and not turn.transcript_encrypted:
                        turn.transcript_encrypted, turn.confidence = transcript, confidence
            for log in logs:
                if log.turn_id and db.get(Turn, log.turn_id) is None:
                    log.turn_id = None  # A failed TTS/prepare transaction may roll back its new turn.
            db.add_all(logs)
    finally:
        call_buffer.reset(context_token)
        checkpoint_buffer.reset(checkpoint_token)
    return True


def dispatch(rt, db, task, svc):
    args = rt, db, task.user_id, task.request_id
    kind, entity_id = task.event_type, task.aggregate_id
    if kind == "PARSE_JOB":
        row = db.get(JobProfile, entity_id)
        return JobService(*args).parse(row) if row else {"deleted": True}
    if kind == "PARSE_RESUME":
        row = db.get(ResumeAsset, entity_id)
        return ResumeService(*args).parse(row) if row and row.parse_status != "DELETED" else {"deleted": True}
    if kind in ("PREPARE_INTERVIEW", "GENERATE_REPORT"):
        row = db.scalar(select(Interview).where(Interview.id == entity_id).with_for_update())
        if not row or row.deleted_at:
            return {"deleted": True}
        if kind == "PREPARE_INTERVIEW":
            return EngineService(*args).prepare(row)
        return ReportService(*args).generate(row, svc.decrypt_json(task.payload_encrypted, {}))
    if kind == "PROCESS_ANSWER":
        turn = db.get(Turn, entity_id)
        row = db.scalar(select(Interview).where(Interview.id == turn.interview_id).with_for_update())
        if row.deleted_at:
            return {"deleted": True}
        return EngineService(*args).answer(row, turn, svc.decrypt_json(task.payload_encrypted, {}))
    if kind == "DELETE_MEDIA":
        asset = db.get(MediaAsset, entity_id)
        if asset:
            rt.storage.delete(asset.object_key)
            asset.status, asset.deleted_at = "DELETED", utcnow()
        return {"deleted": True}
    if kind == "DELETE_USER":
        return PrivacyService(*args).erase_user(task.id)
    raise BusinessError("TASK_UNKNOWN_TYPE", "未知任务类型")


def mark_failed(rt, db, task):
    row = None
    if task.event_type == "PREPARE_INTERVIEW":
        row = db.get(Interview, task.aggregate_id)
        if row and row.status == "PREPARING":
            transition(row, "FAILED")
            BillingService(rt, db, task.user_id).settle(row.id, False)
    elif task.event_type == "GENERATE_REPORT":
        row = db.get(Interview, task.aggregate_id)
        if row and row.status == "FINISHING":
            transition(row, "REPORT_FAILED")
    elif task.event_type == "PROCESS_ANSWER":
        turn = db.get(Turn, task.aggregate_id)
        if turn and turn.status == "SUBMITTED":
            turn.status = "FAILED"
    elif task.event_type == "PARSE_JOB":
        row = db.get(JobProfile, task.aggregate_id)
        if row:
            row.status = "FAILED"
    elif task.event_type == "PARSE_RESUME":
        row = db.get(ResumeAsset, task.aggregate_id)
        if row:
            row.parse_status = "FAILED"
    if isinstance(row, Interview):
        row.failure_code = task.error_code


def retry_task(rt, db, task, user_id):
    require(task.user_id == user_id, "RESOURCE_NOT_FOUND", "任务不存在", 404)
    require(task.status == "FAILED", "TASK_INVALID_STATE", "仅失败任务可重试", 409)
    if task.event_type == "PREPARE_INTERVIEW":
        row = db.get(Interview, task.aggregate_id)
        require(not row.deleted_at and row.status == "FAILED", "INTERVIEW_INVALID_STATE", "面试不可重试", 409)
        BillingService(rt, db, user_id).reserve(row.id, retry=True)
        transition(row, "PREPARING")
    elif task.event_type == "GENERATE_REPORT":
        row = db.get(Interview, task.aggregate_id)
        require(
            not row.deleted_at and row.status == "REPORT_FAILED",
            "INTERVIEW_INVALID_STATE",
            "报告不可重试",
            409,
        )
        transition(row, "FINISHING")
    elif task.event_type == "PROCESS_ANSWER":
        turn = db.get(Turn, task.aggregate_id)
        row = db.get(Interview, turn.interview_id)
        require(
            not row.deleted_at and row.status in ("IN_PROGRESS", "PAUSED"),
            "INTERVIEW_INVALID_STATE",
            "回答不可重试",
            409,
        )
        turn.status = "SUBMITTED"
    task.status, task.retry_count, task.error_code, task.available_at = "PENDING", 0, None, utcnow()
    return {"task_id": task.id, "status": task.status}


def cleanup(rt):
    with rt.db.session() as db:
        for asset in db.scalars(
            select(MediaAsset)
            .where(MediaAsset.expires_at <= utcnow(), MediaAsset.status.notin_(["DELETED", "DELETING"]))
            .limit(500)
        ):
            asset.status = "DELETING"
            Service(rt, db, asset.user_id).task("DELETE_MEDIA", asset.id)
        # Abandoned reservations expire without charging users.
        for row in db.scalars(
            select(Interview)
            .where(
                Interview.status.in_(["READY", "PREPARING"]),
                Interview.created_at < utcnow() - timedelta(days=1),
            )
            .limit(500)
        ):
            transition(row, "CANCELLED")
            BillingService(rt, db, row.user_id).settle(row.id, False)


def expire_interviews(rt):
    from app.modules.interview.service import InterviewService

    with rt.db.session() as db:
        rows = db.scalars(
            select(Interview)
            .where(
                Interview.status == "IN_PROGRESS",
                Interview.deleted_at.is_(None),
                Interview.started_at < utcnow() - timedelta(minutes=15),
            )
            .with_for_update(skip_locked=True)
            .limit(200)
        ).all()
        for row in rows:
            svc = InterviewService(rt, db, row.user_id)
            if svc.elapsed(row) < row.config_json["duration_minutes"] * 60:
                continue
            turns = svc.turns(row)
            if not any(t.status in ("SUBMITTED", "TRANSCRIBING", "ANALYZING", "FAILED") for t in turns):
                svc.schedule_report(row)
