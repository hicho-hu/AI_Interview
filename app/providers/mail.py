"""SMTP delivery, development previews, and opt-in registration-only test codes."""

import smtplib
import ssl
from email.message import EmailMessage

from app.core.errors import BusinessError, require


def deliver_code(settings, email, code, purpose):
    if purpose == "REGISTER" and settings.registration_email_mock_enabled:
        return {"delivery": "registration_mock", "mock_code": code}
    require(settings.mail_mode != "disabled", "EMAIL_NOT_CONFIGURED", "邮件服务未配置", 503)
    if settings.mail_mode == "preview":
        require(
            settings.environment != "production",
            "EMAIL_NOT_CONFIGURED",
            "生产环境禁止使用邮件预览",
            503,
        )
        return {"delivery": "development_preview", "development_code": code}
    require(settings.smtp_host and settings.smtp_from, "EMAIL_NOT_CONFIGURED", "SMTP 邮件服务未配置", 503)
    message = EmailMessage()
    message["From"] = settings.smtp_from
    message["To"] = email
    message["Subject"] = "面试序章 · 邮箱验证码"
    label = {"REGISTER": "注册", "RESET_PASSWORD": "重置密码", "BIND_EMAIL": "绑定或换绑邮箱"}[purpose]
    message.set_content(
        f"你正在{label}，验证码为 {code}，10 分钟内有效。请勿将验证码提供给他人。如非本人操作，请忽略此邮件。"
    )
    try:
        connection = (
            smtplib.SMTP_SSL(
                settings.smtp_host, settings.smtp_port, timeout=12, context=ssl.create_default_context()
            )
            if settings.smtp_use_ssl
            else smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=12)
        )
        with connection:
            if not settings.smtp_use_ssl and settings.smtp_starttls:
                connection.starttls(context=ssl.create_default_context())
            if settings.smtp_username:
                connection.login(settings.smtp_username, settings.smtp_password)
            connection.send_message(message)
    except (OSError, smtplib.SMTPException) as error:
        raise BusinessError("EMAIL_SEND_FAILED", "邮件暂时发送失败，请稍后重试", 503) from error
    return {"delivery": "smtp"}
