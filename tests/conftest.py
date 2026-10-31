import os
import uuid
from pathlib import Path

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from alembic import command
from app.core.config import Settings
from app.main import create_app


@pytest.fixture
def client(tmp_path):
    database_url = f"sqlite:///{tmp_path}/test.db"
    mysql_url = os.environ.get("TEST_DATABASE_URL")
    admin_engine, database_name = None, None
    if mysql_url:
        target = make_url(mysql_url)
        if target.drivername != "mysql+pymysql":
            raise ValueError("TEST_DATABASE_URL 必须使用 mysql+pymysql 驱动")
        database_name = "ai_interview_test_" + uuid.uuid4().hex
        admin_engine = create_engine(target, isolation_level="AUTOCOMMIT")
        with admin_engine.connect() as connection:
            connection.execute(
                text(f"CREATE DATABASE `{database_name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin")
            )
        database_url = target.set(database=database_name).render_as_string(hide_password=False)
    settings = Settings(
        _env_file=None,
        environment="test",
        database_url=database_url,
        storage_path=tmp_path / "objects",
        rate_limit_per_minute=10000,
        provider_retry_count=0,
        redis_url=None,
        task_mode="eager",
    )
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    config.attributes["database_url"] = settings.database_url
    try:
        command.upgrade(config, "head")
        app = create_app(settings)
        with TestClient(app) as c:
            yield c
    finally:
        if admin_engine:
            # Only the fresh, randomly named database created by this fixture is removed.
            assert database_name.startswith("ai_interview_test_") and len(database_name) == 50
            with admin_engine.connect() as connection:
                connection.execute(text(f"DROP DATABASE `{database_name}`"))
            admin_engine.dispose()


def data(response, status=None):
    if status:
        assert response.status_code == status, response.text
    else:
        assert response.is_success, response.text
    result = response.json()
    assert result["request_id"]
    return result["data"]


def login(client, identity="alice", consents=True):
    result = data(client.post("/api/v1/auth/dev-login", json={"identity": identity}))
    headers = {"Authorization": "Bearer " + result["access_token"]}
    if consents:
        for kind in ("PRIVACY", "TERMS", "AI_PROCESSING", "MICROPHONE", "CAMERA"):
            data(
                client.post(
                    "/api/v1/users/me/consents",
                    json={"consent_type": kind, "policy_version": "1.0"},
                    headers=headers,
                )
            )
    return headers, result["user"]["id"]


def job(client, headers):
    created = data(
        client.post(
            "/api/v1/job-profiles",
            headers=headers,
            json={
                "job_title": "Python 后端工程师",
                "jd_text": "负责 Python API 服务开发；设计数据库与事务；优化并发性能并参与技术协作",
                "seniority": "SENIOR",
            },
        )
    )
    result = data(client.get("/api/v1/job-profiles/" + created["id"], headers=headers))
    assert result["status"] == "READY", result
    return result["id"]


def interview(client, headers, job_id=None, config=None, key="create-1"):
    job_id = job_id or job(client, headers)
    config = {"mode": "TEXT", "question_count": 2, "allow_follow_up": False, **(config or {})}
    result = data(
        client.post(
            "/api/v1/interviews",
            headers={**headers, "Idempotency-Key": key},
            json={"job_profile_id": job_id, "config": config},
        )
    )
    row = data(client.get("/api/v1/interviews/" + result["interview_id"], headers=headers))
    assert row["status"] == "READY", row
    return row
