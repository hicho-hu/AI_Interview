import pytest
from cryptography.fernet import Fernet

from app.core.config import Settings


@pytest.fixture
def production_config():
    return {
        "environment": "production",
        "database_url": "mysql+pymysql://test:test@localhost/test?charset=utf8mb4",
        "redis_url": "redis://localhost:6379/0",
        "storage_backend": "s3",
        "task_mode": "worker",
        "provider_mode": "http",
        "provider_url": "https://gateway.example.test",
        "provider_api_key": "test-only-key",
        "secret_key": "test-production-mail-secret-32-characters",
        "encryption_key": Fernet.generate_key().decode(),
        "public_base_url": "https://api.example.test",
        "web_base_url": "https://web.example.test",
        "mail_mode": "smtp",
        "smtp_host": "smtp.example.test",
        "smtp_from": "noreply@example.test",
    }


def test_production_can_disable_mail_without_smtp(production_config):
    production_config.update(
        mail_mode="disabled",
        smtp_host="",
        smtp_from="",
        smtp_username="",
        smtp_password="",
        smtp_use_ssl=False,
        smtp_starttls=False,
    )
    settings = Settings(_env_file=None, **production_config)
    assert settings.mail_mode == "disabled"


def test_production_rejects_preview_mail(production_config):
    production_config["mail_mode"] = "preview"
    with pytest.raises(ValueError, match="生产环境禁止使用邮件预览"):
        Settings(_env_file=None, **production_config)


@pytest.mark.parametrize(
    "missing",
    [
        {"smtp_host": ""},
        {"smtp_from": ""},
        {"smtp_use_ssl": False, "smtp_starttls": False},
    ],
)
def test_production_smtp_requires_address_and_encryption(production_config, missing):
    production_config.update(missing)
    with pytest.raises(ValueError, match="生产环境启用 SMTP"):
        Settings(_env_file=None, **production_config)


@pytest.mark.parametrize("use_ssl,starttls", [(True, False), (False, True)])
def test_production_accepts_encrypted_smtp(production_config, use_ssl, starttls):
    production_config.update(smtp_use_ssl=use_ssl, smtp_starttls=starttls)
    assert Settings(_env_file=None, **production_config).mail_mode == "smtp"
