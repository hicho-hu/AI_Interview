import json
import os
import re
import stat
from pathlib import Path

import pytest
from pymysql.constants import CLIENT
from sqlalchemy import create_engine, func, select

from app.core.config import Settings
from app.core.security import Security
from app.db.admin_models import AdminAudit, AdminUser, Product
from app.db.growth_models import InvitationConfig
from app.modules.admin.security import PERMISSIONS, password_hash, password_matches
from app.modules.billing.service import PRODUCTS
from scripts.export_initial_data_sql import (
    CREDENTIALS_FILENAME,
    SQL_FILENAME,
    export_initial_data,
    render_initial_data_sql,
)
from tests.conftest import data, login

ROOT = Path(__file__).resolve().parents[1]


def test_shared_initial_data_has_no_credentials_and_matches_exporter():
    sql = (ROOT / "docs/sql" / SQL_FILENAME).read_text(encoding="utf-8")
    assert sql == render_initial_data_sql()
    assert "SET @bootstrap_admin_password_hash = '" not in sql
    assert "scrypt$" not in sql and "otpauth://" not in sql
    for role, permissions in PERMISSIONS.items():
        assert role in sql
        assert all(f"--   └─ {permission}\n" in sql for permission in permissions)


def test_private_export_uses_application_password_and_mfa_formats(tmp_path, monkeypatch):
    monkeypatch.delenv("ADMIN_BOOTSTRAP_PASSWORD", raising=False)
    settings = Settings(_env_file=None, environment="test")
    sql_path, credentials_path = export_initial_data(tmp_path, settings, enable_mfa=True)
    credentials = json.loads(credentials_path.read_text())
    sql = sql_path.read_text()
    encoded = re.search(r"SET @bootstrap_admin_password_hash = '([^']+)';", sql)[1]
    encrypted_mfa = re.search(r"SET @bootstrap_admin_mfa_secret_encrypted = '([^']+)';", sql)[1]
    assert len(credentials["password"]) >= 12
    assert password_matches(credentials["password"], encoded)
    assert Security(settings).decrypt(encrypted_mfa) == credentials["mfa_secret"]
    assert credentials["password"] not in sql and credentials["mfa_secret"] not in sql
    assert credentials["mfa_uri"].startswith("otpauth://totp/InterviewAdmin:admin?")
    assert stat.S_IMODE(sql_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(credentials_path.stat().st_mode) == 0o600
    with pytest.raises(FileExistsError):
        export_initial_data(tmp_path, settings)
    assert credentials_path.read_text() == json.dumps(credentials, ensure_ascii=False, indent=2) + "\n"
    assert sql_path.read_text() == sql


def test_export_preserves_existing_sql_and_rejects_weak_password(tmp_path, monkeypatch):
    settings = Settings(_env_file=None, environment="test")
    monkeypatch.setenv("ADMIN_BOOTSTRAP_PASSWORD", "too-short")
    with pytest.raises(ValueError):
        export_initial_data(tmp_path, settings)
    assert not list(tmp_path.iterdir())
    monkeypatch.setenv("ADMIN_BOOTSTRAP_PASSWORD", "synthetic-bootstrap-password-2026")
    existing = tmp_path / SQL_FILENAME
    existing.write_text("existing SQL")
    with pytest.raises(FileExistsError):
        export_initial_data(tmp_path, settings)
    assert existing.read_text() == "existing SQL"
    assert not (tmp_path / CREDENTIALS_FILENAME).exists()


MYSQL = pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="requires MySQL test server")


def import_sql(settings, sql):
    engine = create_engine(settings.database_url, connect_args={"client_flag": CLIENT.MULTI_STATEMENTS})
    try:
        connection = engine.raw_connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute(sql)
                results = []
                while True:
                    if cursor.description:
                        results.append(cursor.fetchall())
                    if not cursor.nextset():
                        break
                return results
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()
    finally:
        engine.dispose()


@MYSQL
def test_mysql_initial_data_login_catalog_and_repeat_import_preserve_changes(client, tmp_path, monkeypatch):
    monkeypatch.delenv("ADMIN_BOOTSTRAP_PASSWORD", raising=False)
    settings = client.app.state.runtime.settings
    display_name = "首次'管理员\\演示"
    sql_path, credentials_path = export_initial_data(tmp_path, settings, display_name=display_name)
    credentials = json.loads(credentials_path.read_text())
    sql = sql_path.read_text()
    first = import_sql(settings, sql)[0][0]
    assert first[2:] == (1, len(PRODUCTS), 1)
    result = data(client.post("/api/v1/admin/auth/login", json={
        "login_name": credentials["login_name"], "password": credentials["password"],
    }))
    assert result["admin"]["display_name"] == display_name
    assert result["admin"]["role"] == "SUPER_ADMIN"
    admin_headers = {"Authorization": "Bearer " + result["access_token"]}
    roles = data(client.get("/api/v1/admin/roles", headers=admin_headers))["items"]
    assert {row["role"]: set(row["permissions"]) for row in roles} == PERMISSIONS
    user_headers, _ = login(client)
    catalog = data(client.get("/api/v1/billing/products", headers=user_headers))["items"]
    assert {item["code"] for item in catalog} == set(PRODUCTS)
    for item in catalog:
        assert item["version"] == 1
        for key in ("amount_minor", "credits", "interview_credits"):
            assert item[key] == PRODUCTS[item["code"]][key]
    new_password = "synthetic-changed-admin-password"
    rt = client.app.state.runtime
    with rt.db.session() as db:
        admin = db.scalar(select(AdminUser))
        admin.password_hash = password_hash(new_password)
        product = db.scalar(select(Product).where(Product.code == "SINGLE"))
        product.status, product.amount_minor = "RETIRED", 1234
        db.add(InvitationConfig(
            version=2, enabled=False, threshold=5, inviter_rewards={"TEXT": 2},
            invitee_rewards={"TEXT": 0}, created_by=admin.id,
        ))
    second = import_sql(settings, sql)[0][0]
    assert second[2:] == (0, 0, 0)
    with rt.db.session() as db:
        assert db.scalar(select(func.count()).select_from(AdminUser)) == 1
        assert password_matches(new_password, db.scalar(select(AdminUser)).password_hash)
        assert db.scalar(select(func.count()).select_from(Product)) == len(PRODUCTS)
        product = db.scalar(select(Product).where(Product.code == "SINGLE"))
        assert (product.status, product.amount_minor) == ("RETIRED", 1234)
        assert db.scalar(select(func.count()).select_from(InvitationConfig)) == 2
        assert db.scalar(select(func.count()).select_from(AdminAudit).where(
            AdminAudit.action == "SYSTEM_BOOTSTRAP"
        )) == 1


@MYSQL
def test_mysql_initial_data_rolls_back_instead_of_promoting_existing_account(client):
    rt = client.app.state.runtime
    with rt.db.session() as db:
        db.add(AdminUser(
            login_name="admin", display_name="已有客服", password_hash=password_hash("existing-test-password"),
            role="SUPPORT", status="ACTIVE",
        ))
    sql = render_initial_data_sql(encoded_password=password_hash("new-bootstrap-test-password"))
    with pytest.raises(Exception) as error:
        import_sql(rt.settings, sql)
    assert "created_by" in str(error.value)
    with rt.db.session() as db:
        admin = db.scalar(select(AdminUser))
        assert admin.role == "SUPPORT" and password_matches("existing-test-password", admin.password_hash)
        assert db.scalar(select(func.count()).select_from(Product)) == 0
        assert db.scalar(select(func.count()).select_from(InvitationConfig)) == 0
