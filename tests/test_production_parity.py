import time

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models import Task
from app.providers.ai import MockProvider
from app.workers.processor import process_task, task_ids_for_runtime
from tests.conftest import data, login
from tests.test_admin import PASSWORD as ADMIN_PASSWORD
from tests.test_admin import admin_login
from tests.test_billing_tasks import FailingProvider, exhaust


def await_ready(client, path, headers):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        result = data(client.get(path, headers=headers))
        if result["status"] == "READY":
            return result
        assert result["status"] != "FAILED", result
        time.sleep(0.05)
    pytest.fail("内置任务执行器未在时限内推进任务")


@pytest.mark.parametrize("mode", ["VOICE", "VIDEO"])
def test_production_api_alone_can_buy_and_prepare_media_interviews(client, mode):
    rt = client.app.state.runtime
    rt.settings.environment = "production"
    rt.settings.task_mode = "worker"
    rt.settings.task_poll_seconds = 0.1
    assert rt.cache.redis is None
    with TestClient(client.app) as production:
        headers, _ = login(production, "production-" + mode)
        catalog = data(production.get("/api/v1/billing/products", headers=headers))
        assert catalog["mock_payment_enabled"] and catalog["development_mock_payment"]
        assert not catalog["payment_enabled"]  # Real payment channels are still reported accurately.
        order = data(
            production.post(
                "/api/v1/billing/orders",
                headers={**headers, "Idempotency-Key": "buy-media-pack"},
                json={"product_code": "PACK_10"},
            )
        )
        for key in ("pay", "pay-again"):
            data(
                production.post(
                    f"/api/v1/billing/orders/{order['id']}/dev-pay",
                    headers={**headers, "Idempotency-Key": key},
                )
            )
        entitlements = data(production.get("/api/v1/billing/entitlements", headers=headers))
        assert entitlements["remaining_interviews"] == 13
        job = data(
            production.post(
                "/api/v1/job-profiles",
                headers=headers,
                json={
                    "job_title": "后端工程师",
                    "jd_text": "负责 Python API 开发、SQL 数据库设计、事务和并发性能优化",
                },
            )
        )
        await_ready(production, f"/api/v1/job-profiles/{job['id']}", headers)
        created = data(
            production.post(
                "/api/v1/interviews",
                headers={**headers, "Idempotency-Key": "create-" + mode},
                json={
                    "job_profile_id": job["id"],
                    "config": {"mode": mode, "question_count": 1, "allow_follow_up": False},
                },
            )
        )
        interview = await_ready(production, f"/api/v1/interviews/{created['interview_id']}", headers)
        assert interview["mode"] == mode and interview["current_question"]
        assert production.get("/health/ready").json()["status"] == "ready"


def test_other_deployment_cannot_claim_or_fail_encrypted_tasks(client):
    headers, _ = login(client)
    rt = client.app.state.runtime
    rt.settings.task_mode = "worker"
    payload = {"job_title": "后端工程师", "jd_text": "设计 Python API 和 SQL 事务，优化接口并发性能"}
    foreign = data(client.post("/api/v1/job-profiles", headers=headers, json=payload))
    with rt.db.session() as db:
        db.get(Task, foreign["task_id"]).payload_encrypted = Fernet(Fernet.generate_key()).encrypt(b"{}").decode()
    current = data(client.post("/api/v1/job-profiles", headers=headers, json=payload))
    with rt.db.session() as db:
        assert task_ids_for_runtime(rt, db) == [current["task_id"]]
        # A live broker lease must also be recoverable when only the API is running.
        db.get(Task, current["task_id"]).status = "QUEUED"
    assert process_task(rt, foreign["task_id"]) is False
    from app.workers.background import BackgroundTaskRunner

    assert BackgroundTaskRunner(rt).run_once() == 1
    with rt.db.session() as db:
        wrong = db.get(Task, foreign["task_id"])
        assert wrong.status == "PENDING" and wrong.retry_count == 0 and wrong.error_code is None
        assert db.get(Task, current["task_id"]).status == "SUCCEEDED"


def test_failed_job_can_retry_without_creating_another_job(client):
    headers, _ = login(client)
    rt = client.app.state.runtime
    provider = FailingProvider()
    provider.failing_operation = "job"
    rt.provider = provider
    created = data(
        client.post(
            "/api/v1/job-profiles",
            headers=headers,
            json={"job_title": "Python 工程师", "jd_text": "负责 Python 后端服务开发和数据库设计与性能优化"},
        )
    )
    exhaust(rt, created["task_id"])
    path = f"/api/v1/job-profiles/{created['id']}"
    assert data(client.get(path, headers=headers))["status"] == "FAILED"
    rt.settings.task_mode = "worker"
    rt.provider = MockProvider()
    data(client.post(f"/api/v1/tasks/{created['task_id']}/retry", headers=headers))
    data(client.post(f"/api/v1/tasks/{created['task_id']}/retry", headers=headers))
    assert data(client.get(path, headers=headers))["status"] == "PREPARING"
    rt.drain()
    assert data(client.get(path, headers=headers))["status"] == "READY"
    data(client.post(f"/api/v1/tasks/{created['task_id']}/retry", headers=headers))
    with rt.db.session() as db:
        assert len(db.scalars(select(Task).where(Task.event_type == "PARSE_JOB")).all()) == 1


def test_production_mock_payment_can_be_disabled_explicitly(client):
    headers, _ = login(client)
    settings = client.app.state.runtime.settings
    settings.environment = "production"
    settings.mock_payment_enabled = False
    catalog = data(client.get("/api/v1/billing/products", headers=headers))
    assert not catalog["mock_payment_enabled"] and not catalog["development_mock_payment"]
    order = data(
        client.post(
            "/api/v1/billing/orders",
            headers={**headers, "Idempotency-Key": "disabled-pay"},
            json={"product_code": "PACK_10"},
        )
    )
    response = client.post(
        f"/api/v1/billing/orders/{order['id']}/dev-pay",
        headers={**headers, "Idempotency-Key": "disabled-pay"},
    )
    assert response.status_code == 503
    assert data(client.get("/api/v1/billing/entitlements", headers=headers))["remaining_interviews"] == 3


def test_admin_mfa_policy_is_configuration_based_in_production(client):
    settings = client.app.state.runtime.settings
    settings.environment = "production"
    headers, _ = admin_login(client)
    assert client.get("/api/v1/admin/roles", headers=headers).status_code == 200
    settings.admin_mfa_required = True
    response = client.post(
        "/api/v1/admin/auth/login", json={"login_name": "admin", "password": ADMIN_PASSWORD}
    )
    assert response.status_code == 403 and response.json()["code"] == "ADMIN_MFA_REQUIRED"
