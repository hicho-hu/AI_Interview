"""Executed by CI against MySQL 8.4; skipped when no explicit test server is configured."""

import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import inspect, select, text

from alembic import command
from app.db.models import Base, Report, User
from tests.conftest import data, interview, job, login

pytestmark = pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="requires MySQL test server")


def config_for(client):
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    config.attributes["database_url"] = client.app.state.runtime.settings.database_url
    return config


def test_mysql_real_migration_round_trip_and_constraints(client):
    rt = client.app.state.runtime
    config = config_for(client)
    command.check(config)
    with rt.db.engine.connect() as connection:
        count = connection.scalar(
            text(
                "SELECT COUNT(*) FROM information_schema.REFERENTIAL_CONSTRAINTS "
                "WHERE CONSTRAINT_SCHEMA = DATABASE() AND CONSTRAINT_NAME IN "
                "('fk_interview_resume', 'fk_turn_answer', 'fk_turn_tts', 'fk_media_turn')"
            )
        )
        assert count == 4
    command.downgrade(config, "base")
    assert inspect(rt.db.engine).get_table_names() == ["alembic_version"]
    command.upgrade(config, "head")
    command.check(config)
    assert len(inspect(rt.db.engine).get_table_names()) == len(Base.metadata.tables) + 1


def test_mysql_accepts_long_encrypted_reports_and_utc_microseconds(client):
    headers, uid = login(client)
    row = interview(client, headers)
    rt = client.app.state.runtime
    original = datetime(2026, 11, 1, 16, 1, 2, 345678, tzinfo=timezone(timedelta(hours=8)))
    encrypted = rt.security.encrypt("x" * 100000)
    with rt.db.session() as db:
        db.get(User, uid).created_at = original
        db.add(
            Report(
                interview_id=row["id"],
                report_version=1,
                overall_score=None,
                content_encrypted=encrypted,
                model_name="mock",
            )
        )
    with rt.db.session() as db:
        result = db.scalar(select(Report).where(Report.interview_id == row["id"]))
        assert rt.security.decrypt(result.content_encrypted) == "x" * 100000
        created = db.get(User, uid).created_at
        assert created == original and created.tzinfo == timezone.utc
        raw = db.scalar(text("SELECT created_at FROM users WHERE id = :id"), {"id": uid})
        assert raw == datetime(2026, 11, 1, 8, 1, 2, 345678)


def test_mysql_idempotency_keys_are_case_and_trailing_space_sensitive(client):
    headers, _ = login(client)
    jid = job(client, headers)
    payload = {"job_profile_id": jid, "config": {"mode": "TEXT", "question_count": 1}}
    ids = [
        data(client.post("/api/v1/interviews", headers={**headers, "Idempotency-Key": key}, json=payload))[
            "interview_id"
        ]
        for key in ("Key", "key", "key ")
    ]
    assert len(set(ids)) == 3


def test_mysql_export_import_matches_migration_and_app(client):
    rt = client.app.state.runtime
    command.downgrade(config_for(client), "base")
    # The fixture owns this temporary database; drop only its empty Alembic marker before full import.
    with rt.db.engine.begin() as connection:
        connection.execute(text("DROP TABLE alembic_version"))
        sql = (Path(__file__).parents[1] / "docs/sql/ai_interview_mysql.sql").read_text(encoding="utf-8")
        sql = re.sub(r"^\s*--[^\n]*", "", sql, flags=re.M)
        for statement in sql.split(";"):
            if statement.strip():
                connection.execute(text(statement))
    command.check(config_for(client))
    assert client.get("/health/ready").is_success
    login(client)
