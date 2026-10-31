from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import func, select

from app.core.errors import BusinessError
from app.core.security import utcnow
from app.db.models import AICallLog, Task, Usage, User
from app.modules.interview.service import InterviewService
from app.providers.ai import MockProvider
from app.schemas.contracts import InterviewInput
from tests.conftest import data, interview, job, login
from tests.test_interview import ANSWER


def test_order_payment_refund_idempotency(client):
    headers, _ = login(client)
    row = data(
        client.post(
            "/api/v1/billing/orders",
            headers={**headers, "Idempotency-Key": "order"},
            json={"product_code": "PACK_10"},
        )
    )
    path = "/api/v1/billing/orders/" + row["id"]
    for key in ("paid", "paid", "other-paid-key"):
        data(client.post(path + "/dev-pay", headers={**headers, "Idempotency-Key": key}))
    assert data(client.get("/api/v1/billing/entitlements", headers=headers))["remaining_interviews"] == 13
    for key in ("refund", "refund", "other-refund-key"):
        data(client.post(path + "/refund", headers={**headers, "Idempotency-Key": key}))
    assert data(client.get("/api/v1/billing/entitlements", headers=headers))["remaining_interviews"] == 3
    assert data(client.get("/api/v1/billing/usage", headers=headers))["total"] == 3


def test_atomic_quota_reservation(client):
    headers, uid = login(client)
    jid = job(client, headers)
    rt = client.app.state.runtime
    with rt.db.session() as db:
        db.get(User, uid).credits = 1

    def create_one(i):
        try:
            with rt.db.session() as db:
                return InterviewService(rt, db, uid).create(
                    InterviewInput(job_profile_id=jid, config={"mode": "TEXT"}), str(i)
                )
        except BusinessError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(create_one, range(4)))
    assert sum(isinstance(r, dict) for r in results) == 1
    assert results.count("INTERVIEW_QUOTA_NOT_ENOUGH") == 3
    with rt.db.session() as db:
        assert db.get(User, uid).credits == 0
        assert db.scalar(select(func.count()).select_from(Usage).where(Usage.status == "RESERVED")) == 1


class FailingProvider(MockProvider):
    failing_operation = "plan"

    def invoke(self, operation, payload, schema):
        if operation == self.failing_operation:
            raise BusinessError("AI_PROVIDER_BUSY", "simulated failure", 503)
        return super().invoke(operation, payload, schema)


def exhaust(rt, task_id):
    for _ in range(3):
        with rt.db.session() as db:
            db.get(Task, task_id).available_at = utcnow()
        rt.drain()


def test_preparation_failure_release_and_retry(client):
    headers, uid = login(client)
    jid = job(client, headers)
    rt = client.app.state.runtime
    rt.provider = FailingProvider()
    result = data(
        client.post(
            "/api/v1/interviews",
            headers={**headers, "Idempotency-Key": "failing"},
            json={"job_profile_id": jid, "config": {"mode": "TEXT", "question_count": 1}},
        )
    )
    exhaust(rt, result["task_id"])
    path = "/api/v1/interviews/" + result["interview_id"]
    assert data(client.get(path, headers=headers))["status"] == "FAILED"
    assert data(client.get("/api/v1/users/me", headers=headers))["remaining_interviews"] == 3
    rt.provider = MockProvider()
    data(client.post(path + "/retry", headers=headers))
    assert data(client.get(path, headers=headers))["status"] == "READY"
    assert data(client.get("/api/v1/users/me", headers=headers))["remaining_interviews"] == 2
    with rt.db.session() as db:
        assert db.scalar(select(func.count()).select_from(AICallLog).where(AICallLog.status == "FAILED")) == 3


def test_failed_answer_preserved_and_retry(client):
    headers, _ = login(client)
    row = interview(client, headers, config={"question_count": 1})
    path = "/api/v1/interviews/" + row["id"]
    data(client.post(path + "/start", headers=headers))
    rt = client.app.state.runtime
    provider = FailingProvider()
    provider.failing_operation = "decision"
    rt.provider = provider
    submitted = data(
        client.post(
            path + "/turns/" + row["current_question"]["id"] + "/submit",
            headers={**headers, "Idempotency-Key": "answer"},
            json={"text": ANSWER},
        )
    )
    exhaust(rt, submitted["task_id"])
    turn = data(client.get(path + "/turns", headers=headers))["items"][0]
    assert turn["status"] == "FAILED" and turn["transcript"] == ANSWER
    assert (
        client.post(
            path + "/finish", headers={**headers, "Idempotency-Key": "finish"}, json={"reason": "USER"}
        ).status_code
        == 409
    )
    rt.provider = MockProvider()
    data(client.post("/api/v1/tasks/" + submitted["task_id"] + "/retry", headers=headers))
    assert data(client.get(path, headers=headers))["status"] == "COMPLETED"


def test_report_failure_can_retry_and_task_is_deduplicated(client):
    headers, _ = login(client)
    row = interview(client, headers, config={"question_count": 1})
    path = "/api/v1/interviews/" + row["id"]
    data(client.post(path + "/start", headers=headers))
    rt = client.app.state.runtime
    provider = FailingProvider()
    provider.failing_operation = "report"
    rt.provider = provider
    result = data(
        client.post(
            path + "/finish", headers={**headers, "Idempotency-Key": "finish"}, json={"reason": "USER"}
        )
    )
    exhaust(rt, result["task_id"])
    assert data(client.get(path, headers=headers))["status"] == "REPORT_FAILED"
    rt.provider = MockProvider()
    data(client.post(path + "/retry", headers=headers))
    from app.workers.processor import process_task

    assert process_task(rt, result["task_id"]) is False
    assert data(client.get(path + "/report", headers=headers))["status"] == "COMPLETED"


def test_tts_failure_rolls_back_new_turn_and_keeps_cost_logs(client):
    headers, _ = login(client)
    jid = job(client, headers)
    rt = client.app.state.runtime
    provider = FailingProvider()
    provider.failing_operation = "tts"
    rt.provider = provider
    created = data(
        client.post(
            "/api/v1/interviews",
            headers={**headers, "Idempotency-Key": "tts-fail"},
            json={"job_profile_id": jid, "config": {"mode": "VOICE", "question_count": 1}},
        )
    )
    exhaust(rt, created["task_id"])
    assert (
        data(client.get("/api/v1/interviews/" + created["interview_id"], headers=headers))["status"]
        == "FAILED"
    )
    with rt.db.session() as db:
        logs = db.scalars(select(AICallLog).where(AICallLog.call_type == "tts")).all()
        assert len(logs) == 3
        assert all(log.turn_id is None for log in logs)


def test_concurrent_worker_delivery_advances_once(client):
    headers, _ = login(client)
    jid = job(client, headers)
    rt = client.app.state.runtime
    rt.settings.task_mode = "worker"
    created = data(
        client.post(
            "/api/v1/interviews",
            headers={**headers, "Idempotency-Key": "queued"},
            json={"job_profile_id": jid, "config": {"mode": "TEXT", "question_count": 1}},
        )
    )
    from app.workers.processor import process_task

    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda _: process_task(rt, created["task_id"]), range(3)))
    assert results.count(True) == 1
    row = data(client.get("/api/v1/interviews/" + created["interview_id"], headers=headers))
    assert row["status"] == "READY" and row["current_turn_no"] == 1
