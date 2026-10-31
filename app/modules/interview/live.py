"""Run a durable answer task immediately and stream its progress without polling gaps."""

import json
import queue
import threading
import time

from app.db.models import Interview, Task
from app.modules.interview.service import InterviewService
from app.providers.live import live_sink
from app.workers.processor import process_task

slots = threading.BoundedSemaphore(8)


def answer_events(rt, uid, interview_id, task_id, request_id):
    events = queue.Queue(maxsize=128)
    detached, finished = threading.Event(), threading.Event()
    started = time.monotonic()

    def emit(value):
        while not detached.is_set():
            try:
                events.put({**value, "elapsed_ms": round((time.monotonic() - started) * 1000)}, timeout=0.2)
                return
            except queue.Full:
                continue

    def work():
        token = live_sink.set(emit)
        try:
            process_task(rt, task_id)
        finally:
            live_sink.reset(token)
            finished.set()
            slots.release()
        if rt.settings.task_mode == "eager":
            rt.drain()

    def encode(event):
        return "data: " + json.dumps(event, ensure_ascii=False, default=str) + "\n\n"

    yield encode({"type": "accepted", "task_id": task_id})
    if not slots.acquire(blocking=False):
        with rt.db.session() as db:
            task = db.get(Task, task_id)
            if task.status == "QUEUED":
                task.status, task.lease_until = "PENDING", None
        yield encode({"type": "deferred", "task_id": task_id})
        return
    threading.Thread(target=work, daemon=True, name="live-interview-answer").start()
    try:
        while not finished.is_set() or not events.empty():
            try:
                yield encode(events.get(timeout=2))
            except queue.Empty:
                yield ": heartbeat\n\n"
        # Another worker can have claimed this task first. Its normal outbox retry
        # and the client's fallback polling remain available after a disconnection.
        with rt.db.session() as db:
            task = db.get(Task, task_id)
            row = db.get(Interview, interview_id)
            if row and row.user_id == uid and not row.deleted_at:
                snapshot = InterviewService(rt, db, uid, request_id).view(row)
                yield encode(
                    {
                        "type": "complete" if task.status == "SUCCEEDED" else "deferred",
                        "task_id": task_id,
                        "task_status": task.status,
                        "interview": snapshot,
                    }
                )
    finally:
        detached.set()
