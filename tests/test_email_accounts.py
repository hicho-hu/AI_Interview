from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.security import utcnow
from app.db.growth_models import EmailChallenge, UserAccount
from tests.conftest import data, login

PASSWORD = "Strong-email-password-2026"


def email_code(client, email, purpose="REGISTER", headers=None):
    response = client.post(
        "/api/v1/users/me/email-code" if headers else "/api/v1/auth/email-code",
        headers=headers,
        json={"email": email, **({} if headers else {"purpose": purpose})},
    )
    return data(response)


def register_email(client, email="person@example.com", **extra):
    code = email_code(client, email)
    body = {
        "email": email,
        "password": PASSWORD,
        "challenge_id": code["challenge_id"],
        "code": code["development_code"],
        "nickname": "邮箱练习者",
        "agreed": True,
        **extra,
    }
    result = data(client.post("/api/v1/auth/email-register", json=body), 201)
    return {"Authorization": "Bearer " + result["access_token"]}, result, body


def test_email_registration_login_and_password_session_revocation(client):
    headers, result, body = register_email(client)
    assert result["user"]["email"] == "person@example.com"
    assert result["user"]["has_password"]
    assert data(client.get("/api/v1/users/me", headers=headers))["id"] == result["user"]["id"]
    assert client.post("/api/v1/auth/email-register", json=body).status_code == 409
    signed = data(
        client.post("/api/v1/auth/email-login", json={"email": "PERSON@EXAMPLE.COM", "password": PASSWORD})
    )
    assert signed["user"]["id"] == result["user"]["id"]
    assert (
        client.post(
            "/api/v1/auth/email-login", json={"email": body["email"], "password": "wrong"}
        ).status_code
        == 401
    )
    changed = data(
        client.post(
            "/api/v1/users/me/password",
            headers=headers,
            json={"current_password": PASSWORD, "new_password": "New-secure-password-2026"},
        )
    )
    assert client.get("/api/v1/users/me", headers=headers).status_code == 401
    assert (
        client.get(
            "/api/v1/users/me", headers={"Authorization": "Bearer " + changed["access_token"]}
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/v1/auth/email-login", json={"email": body["email"], "password": PASSWORD}
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/api/v1/auth/email-login", json={"email": body["email"], "password": "New-secure-password-2026"}
        ).status_code
        == 200
    )
    with client.app.state.runtime.db.session() as db:
        account = db.scalar(select(UserAccount).where(UserAccount.user_id == result["user"]["id"]))
        assert PASSWORD not in account.password_hash and body["email"] not in account.email_encrypted


def test_codes_cannot_cross_email_or_purpose_and_failures_remain_limited(client):
    code = email_code(client, "one@example.com")
    body = {
        "email": "two@example.com",
        "password": PASSWORD,
        "challenge_id": code["challenge_id"],
        "code": code["development_code"],
        "agreed": True,
    }
    assert client.post("/api/v1/auth/email-register", json=body).status_code == 400
    body["email"] = "one@example.com"
    body["code"] = "000000" if code["development_code"] != "000000" else "111111"
    for _ in range(4):
        assert client.post("/api/v1/auth/email-register", json=body).status_code == 400
    body["code"] = code["development_code"]
    assert client.post("/api/v1/auth/email-register", json=body).status_code == 429
    assert client.post("/api/v1/auth/email-code", json={"email": "one@example.com"}).status_code == 429
    other = email_code(client, "other@example.com", "RESET_PASSWORD")
    body.update(email="other@example.com", challenge_id=other["challenge_id"], code=other["development_code"])
    assert client.post("/api/v1/auth/email-register", json=body).status_code == 400


def test_expired_code_and_weak_password_are_rejected(client):
    code = email_code(client, "expired@example.com")
    with client.app.state.runtime.db.session() as db:
        db.get(EmailChallenge, code["challenge_id"]).expires_at = utcnow() - timedelta(seconds=1)
    payload = {
        "email": "expired@example.com",
        "password": PASSWORD,
        "code": code["development_code"],
        "challenge_id": code["challenge_id"],
        "agreed": True,
    }
    assert client.post("/api/v1/auth/email-register", json=payload).status_code == 400
    assert (
        client.post("/api/v1/auth/email-register", json={**payload, "password": "short"}).status_code == 422
    )


def test_legacy_account_binding_email_and_changing_email_keeps_business_owner(client):
    headers, uid = login(client)
    code = email_code(client, "bound@example.com", headers=headers)
    result = data(
        client.post(
            "/api/v1/users/me/email",
            headers=headers,
            json={
                "email": "bound@example.com",
                "code": code["development_code"],
                "challenge_id": code["challenge_id"],
                "new_password": PASSWORD,
            },
        )
    )
    assert result["user"]["id"] == uid
    updated = {"Authorization": "Bearer " + result["access_token"]}
    assert client.get("/api/v1/users/me", headers=headers).status_code == 401
    assert data(client.get("/api/v1/users/me", headers=updated))["email"] == "bound@example.com"
    next_code = email_code(client, "new@example.com", headers=updated)
    payload = {
        "email": "new@example.com",
        "code": next_code["development_code"],
        "challenge_id": next_code["challenge_id"],
        "current_password": "bad",
    }
    assert client.post("/api/v1/users/me/email", headers=updated, json=payload).status_code == 401
    payload["current_password"] = PASSWORD
    next_result = data(client.post("/api/v1/users/me/email", headers=updated, json=payload))
    assert next_result["user"]["email"] == "new@example.com" and next_result["user"]["id"] == uid
    assert (
        client.post(
            "/api/v1/auth/email-login", json={"email": "bound@example.com", "password": PASSWORD}
        ).status_code
        == 401
    )


def test_password_reset_requires_email_proof_and_revokes_previous_sessions(client):
    headers, result, _ = register_email(client)
    # Registration and recovery have a shared resend cooldown; advance this isolated test past it.
    client.app.state.runtime.cache.local.clear()
    code = email_code(client, "person@example.com", "RESET_PASSWORD")
    response = data(
        client.post(
            "/api/v1/auth/password-reset",
            json={
                "email": "person@example.com",
                "code": code["development_code"],
                "challenge_id": code["challenge_id"],
                "new_password": "Reset-password-2026",
            },
        )
    )
    assert response["user"]["id"] == result["user"]["id"]
    assert client.get("/api/v1/users/me", headers=headers).status_code == 401
    assert (
        client.post(
            "/api/v1/auth/email-login",
            json={"email": "person@example.com", "password": "Reset-password-2026"},
        ).status_code
        == 200
    )


@pytest.mark.parametrize(
    "purpose,mock_enabled",
    [("REGISTER", False), ("RESET_PASSWORD", True), ("BIND_EMAIL", True)],
)
def test_smtp_delivery_returns_no_preview_code(client, monkeypatch, purpose, mock_enabled):
    headers, _ = login(client) if purpose == "BIND_EMAIL" else ({}, None)
    messages = []

    class SMTP:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def starttls(self, **kwargs):
            pass

        def send_message(self, message):
            messages.append(message)

    monkeypatch.setattr("app.providers.mail.smtplib.SMTP", SMTP)
    settings = client.app.state.runtime.settings
    settings.environment = "production"
    settings.mail_mode = "smtp"
    settings.registration_email_mock_enabled = mock_enabled
    settings.smtp_host = "smtp.example.com"
    settings.smtp_from = "noreply@example.com"
    result = email_code(client, "recipient@example.com", purpose, headers or None)
    assert result["delivery"] == "smtp"
    assert "development_code" not in result and "mock_code" not in result
    assert len(messages) == 1 and messages[0]["To"] == "recipient@example.com"


def test_production_refuses_preview_email_codes(client):
    client.app.state.runtime.settings.environment = "production"
    response = client.post("/api/v1/auth/email-code", json={"email": "recipient@example.com"})
    assert response.status_code == 503
    assert "development_code" not in response.text


def test_production_registration_mock_creates_real_account_without_smtp(client, monkeypatch):
    settings = client.app.state.runtime.settings
    settings.environment = "production"
    settings.mail_mode = "disabled"
    settings.registration_email_mock_enabled = True

    def unexpected_delivery(*args, **kwargs):
        pytest.fail("注册模拟模式不应连接 SMTP")

    monkeypatch.setattr("app.providers.mail.smtplib.SMTP", unexpected_delivery)
    monkeypatch.setattr("app.providers.mail.smtplib.SMTP_SSL", unexpected_delivery)
    code = email_code(client, "mock-register@example.com")
    assert code["delivery"] == "registration_mock"
    assert len(code["mock_code"]) == 6 and code["mock_code"].isdigit()
    assert "development_code" not in code
    payload = {
        "email": "mock-register@example.com",
        "password": PASSWORD,
        "code": code["mock_code"],
        "challenge_id": code["challenge_id"],
        "agreed": True,
    }
    wrong = "000000" if code["mock_code"] != "000000" else "111111"
    invalid = client.post("/api/v1/auth/email-register", json={**payload, "code": wrong})
    assert invalid.status_code == 400 and invalid.json()["code"] == "EMAIL_CODE_INVALID"
    assert client.post(
        "/api/v1/auth/email-register", json={**payload, "email": "other@example.com"}
    ).status_code == 400
    registered = data(client.post("/api/v1/auth/email-register", json=payload), 201)
    signed = data(
        client.post("/api/v1/auth/email-login", json={"email": payload["email"], "password": PASSWORD})
    )
    assert registered["user"]["id"] == signed["user"]["id"]
    assert client.post("/api/v1/auth/email-register", json=payload).status_code == 409
    with client.app.state.runtime.db.session() as db:
        challenge = db.get(EmailChallenge, code["challenge_id"])
        assert challenge.used_at is not None
        assert challenge.code_hash != code["mock_code"]
        assert db.scalar(select(UserAccount).where(UserAccount.user_id == registered["user"]["id"]))


@pytest.mark.parametrize("purpose", ["RESET_PASSWORD", "BIND_EMAIL"])
def test_registration_mock_does_not_expose_recovery_or_binding_codes(client, monkeypatch, purpose):
    headers, _ = login(client) if purpose == "BIND_EMAIL" else ({}, None)
    settings = client.app.state.runtime.settings
    settings.environment = "production"
    settings.mail_mode = "disabled"
    settings.registration_email_mock_enabled = True

    def unexpected_delivery(*args, **kwargs):
        pytest.fail("禁用邮件时不应连接 SMTP")

    monkeypatch.setattr("app.providers.mail.smtplib.SMTP", unexpected_delivery)
    monkeypatch.setattr("app.providers.mail.smtplib.SMTP_SSL", unexpected_delivery)
    path = "/api/v1/users/me/email-code" if headers else "/api/v1/auth/email-code"
    response = client.post(
        path,
        headers=headers,
        json={"email": "recipient@example.com", **({} if headers else {"purpose": purpose})},
    )
    assert response.status_code == 503
    assert response.json()["code"] == "EMAIL_NOT_CONFIGURED"
    assert "mock_code" not in response.text and "development_code" not in response.text
    with client.app.state.runtime.db.session() as db:
        assert db.scalar(select(EmailChallenge.id)) is None


@pytest.mark.parametrize("purpose", ["REGISTER", "RESET_PASSWORD", "BIND_EMAIL"])
def test_disabled_email_returns_unavailable_without_delivery_or_saved_code(client, monkeypatch, purpose):
    headers, _ = login(client) if purpose == "BIND_EMAIL" else ({}, None)
    settings = client.app.state.runtime.settings
    settings.environment = "production"
    settings.mail_mode = "disabled"

    def unexpected_delivery(*args, **kwargs):
        pytest.fail("禁用邮件时不应连接 SMTP")

    monkeypatch.setattr("app.providers.mail.smtplib.SMTP", unexpected_delivery)
    monkeypatch.setattr("app.providers.mail.smtplib.SMTP_SSL", unexpected_delivery)
    path = "/api/v1/users/me/email-code" if purpose == "BIND_EMAIL" else "/api/v1/auth/email-code"
    response = client.post(
        path,
        headers=headers,
        json={"email": "recipient@example.com", **({} if headers else {"purpose": purpose})},
    )
    assert response.status_code == 503
    assert response.json()["code"] == "EMAIL_NOT_CONFIGURED"
    assert "development_code" not in response.text
    with client.app.state.runtime.db.session() as db:
        assert db.scalar(select(EmailChallenge.id)) is None
    assert client.get("/health/live").status_code == 200


def test_existing_email_account_can_login_when_mail_is_disabled(client):
    _, registered, _ = register_email(client)
    settings = client.app.state.runtime.settings
    settings.environment = "production"
    settings.mail_mode = "disabled"
    result = data(
        client.post(
            "/api/v1/auth/email-login", json={"email": "person@example.com", "password": PASSWORD}
        )
    )
    assert result["user"]["id"] == registered["user"]["id"]


def test_account_deletion_erases_email_credentials_and_contact_details(client):
    headers, result, _ = register_email(client)
    data(client.patch("/api/v1/users/me/contact", headers=headers, json={"phone": "13800138000"}))
    data(client.delete("/api/v1/users/me/data", headers=headers), 202)
    assert client.get("/api/v1/users/me", headers=headers).status_code == 401
    with client.app.state.runtime.db.session() as db:
        account = db.scalar(select(UserAccount).where(UserAccount.user_id == result["user"]["id"]))
        assert account.email_hash is None and account.email_encrypted is None
        assert account.password_hash is None and account.phone_encrypted is None
