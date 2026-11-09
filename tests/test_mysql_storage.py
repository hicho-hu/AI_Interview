from datetime import datetime, timedelta, timezone

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.dialects import mysql, sqlite
from sqlalchemy.schema import CreateTable

from app.core.config import Settings
from app.db.models import Base, Interview, Report
from app.db.repository import Database
from app.db.types import UTCDateTime


def test_mysql_is_the_default_storage():
    settings = Settings(_env_file=None)
    assert settings.database_url.startswith("mysql+pymysql://")
    assert settings.redis_url.startswith("redis://")
    assert settings.task_mode == "worker"
    database = Database(settings)
    try:
        assert database.engine.dialect.name == "mysql"
        assert database.engine.url.query["charset"] == "utf8mb4"
    finally:
        database.engine.dispose()


def test_mysql_ddl_supports_large_encrypted_reports_and_microseconds():
    dialect = mysql.dialect()
    for table in Base.metadata.sorted_tables:
        sql = str(CreateTable(table).compile(dialect=dialect))
        assert "ENGINE=InnoDB" in sql
        assert "CHARSET=utf8mb4" in sql
        assert "COLLATE utf8mb4_0900_bin" in sql
        assert "DATETIME(6)" in sql
    assert "content_encrypted LONGTEXT" in str(CreateTable(Report.__table__).compile(dialect=dialect))
    assert "config_json JSON" in str(CreateTable(Interview.__table__).compile(dialect=dialect))


def test_mysql_timestamps_preserve_utc_and_microseconds():
    field = UTCDateTime()
    source = datetime(2026, 11, 1, 16, 1, 2, 345678, tzinfo=timezone(timedelta(hours=8)))
    stored = field.process_bind_param(source, mysql.dialect())
    assert stored == datetime(2026, 11, 1, 8, 1, 2, 345678)
    assert stored.tzinfo is None
    restored = field.process_result_value(stored, mysql.dialect())
    assert restored == source and restored.tzinfo == timezone.utc
    assert field.process_bind_param(None, mysql.dialect()) is None
    assert field.process_result_value(None, mysql.dialect()) is None
    assert field.process_result_value(stored, sqlite.dialect()) == restored


def test_production_database_guard_accepts_mysql_and_rejects_postgres():
    values = dict(
        environment="production",
        storage_backend="s3",
        task_mode="worker",
        provider_mode="http",
        provider_url="https://gateway.example.test",
        provider_api_key="test-only-key",
        encryption_key=Fernet.generate_key().decode(),
        secret_key="test-production-guard-secret-32-characters",
        mail_mode="smtp",
        smtp_host="smtp.example.test",
        smtp_from="noreply@example.test",
        web_base_url="https://web.example.test",
        public_base_url="https://app.example.test",
    )
    assert Settings(_env_file=None, **values).database_url.startswith("mysql+pymysql://")
    with pytest.raises(ValueError, match="MySQL"):
        Settings(_env_file=None, database_url="postgresql://test:test@localhost/test", **values)
