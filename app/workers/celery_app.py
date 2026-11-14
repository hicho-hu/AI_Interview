from celery import Celery

from app.core.config import Settings

settings = Settings()
celery_app = Celery("ai_interview", broker=settings.redis_url or "redis://localhost:6379/0")
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_reject_on_worker_lost=True,
    beat_schedule={
        "outbox-pump": {"task": "outbox.pump", "schedule": 2.0},
        "media-retention": {"task": "media.cleanup", "schedule": 3600.0},
        "interview-timeout": {"task": "interview.timeout", "schedule": 30.0},
    },
)


@celery_app.task(name="outbox.pump", ignore_result=True)
def pump():
    from datetime import timedelta

    from sqlalchemy import select

    from app.core.security import utcnow
    from app.db.models import Task
    from app.runtime import Runtime
    from app.workers.processor import eligible, task_ids_for_runtime

    rt = Runtime(settings)
    with rt.db.session() as db:
        task_ids = task_ids_for_runtime(rt, db)
        tasks = db.scalars(
            select(Task)
            .where(Task.id.in_(task_ids), eligible())
            .with_for_update(skip_locked=True)
        ).all()
        for task in tasks:
            task.status = "QUEUED"
            task.lease_until = utcnow() + timedelta(seconds=settings.task_lease_seconds)
            execute.delay(task.id)
    rt.db.engine.dispose()


@celery_app.task(name="outbox.execute", ignore_result=True)
def execute(task_id):
    from app.runtime import Runtime
    from app.workers.processor import process_task

    rt = Runtime(settings)
    try:
        process_task(rt, task_id)
    finally:
        rt.db.engine.dispose()


@celery_app.task(name="media.cleanup", ignore_result=True)
def retention():
    from app.runtime import Runtime
    from app.workers.processor import cleanup

    rt = Runtime(settings)
    try:
        cleanup(rt)
    finally:
        rt.db.engine.dispose()


@celery_app.task(name="interview.timeout", ignore_result=True)
def expire():
    from app.runtime import Runtime
    from app.workers.processor import expire_interviews

    rt = Runtime(settings)
    try:
        expire_interviews(rt)
    finally:
        rt.db.engine.dispose()
