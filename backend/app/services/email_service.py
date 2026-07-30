"""系统邮件发送服务。"""

from __future__ import annotations

import html
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr

from email_validator import EmailNotValidError, validate_email

from app.core.config import Settings, get_settings


class EmailConfigurationError(RuntimeError):
    """邮件服务配置不完整或互相冲突。"""


class EmailDeliveryError(RuntimeError):
    """邮件发送失败。"""


class EmailService:
    """通过 SMTP 发送系统邮件。"""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    @property
    def from_email(self) -> str:
        """优先使用显式发件地址，否则回退到 SMTP 用户名。"""

        return self.settings.smtp_from_email.strip() or self.settings.smtp_username.strip()

    def ensure_configured(self) -> None:
        """在查询用户前校验配置，避免配置错误暴露邮箱是否已注册。"""

        if not self.settings.smtp_host.strip() or not self.from_email:
            raise EmailConfigurationError("SMTP_HOST 与 SMTP_FROM_EMAIL（或 SMTP_USERNAME）必须配置。")
        if self.settings.smtp_use_tls and self.settings.smtp_use_ssl:
            raise EmailConfigurationError("SMTP_USE_TLS 与 SMTP_USE_SSL 不能同时启用。")
        if bool(self.settings.smtp_username.strip()) != bool(self.settings.smtp_password):
            raise EmailConfigurationError("SMTP_USERNAME 与 SMTP_PASSWORD 必须同时配置或同时留空。")
        try:
            validate_email(self.from_email, check_deliverability=False)
        except EmailNotValidError as exc:
            raise EmailConfigurationError("SMTP 发件地址格式无效。") from exc

    def send_password_reset_code(self, recipient: str, code: str) -> None:
        """发送密码重置验证码邮件。"""

        self.ensure_configured()
        message = EmailMessage()
        message["Subject"] = f"{self.settings.smtp_from_name} 密码重置验证码"
        message["From"] = formataddr((self.settings.smtp_from_name, self.from_email))
        message["To"] = recipient
        message.set_content(
            "\n".join(
                [
                    "你正在重置 ACGAgent 账号密码。",
                    f"验证码：{code}",
                    f"验证码将在 {self.settings.password_reset_code_expire_minutes} 分钟后失效。",
                    "如果不是你本人操作，请忽略此邮件。",
                ]
            )
        )

        escaped_code = html.escape(code)
        expire_minutes = self.settings.password_reset_code_expire_minutes
        message.add_alternative(
            f"""
            <!doctype html>
            <html lang="zh-CN">
              <body style="margin:0;background:#f5f8fc;font-family:Arial,'PingFang SC',sans-serif;color:#31456f">
                <div style="max-width:520px;margin:32px auto;padding:32px;background:#fff;border-radius:16px">
                  <h2 style="margin:0 0 16px">重置账号密码</h2>
                  <p>你正在重置 ACGAgent 账号密码，本次验证码为：</p>
                  <p style="font-size:32px;font-weight:700;letter-spacing:8px;color:#2f6fed">{escaped_code}</p>
                  <p>验证码将在 {expire_minutes} 分钟后失效，请勿转发给他人。</p>
                  <p style="color:#7b8ba9">如果不是你本人操作，请忽略此邮件。</p>
                </div>
              </body>
            </html>
            """,
            subtype="html",
        )

        context = ssl.create_default_context()
        try:
            if self.settings.smtp_use_ssl:
                smtp: smtplib.SMTP = smtplib.SMTP_SSL(
                    self.settings.smtp_host.strip(),
                    self.settings.smtp_port,
                    timeout=self.settings.smtp_timeout_seconds,
                    context=context,
                )
            else:
                smtp = smtplib.SMTP(
                    self.settings.smtp_host.strip(),
                    self.settings.smtp_port,
                    timeout=self.settings.smtp_timeout_seconds,
                )

            with smtp:
                smtp.ehlo()
                if self.settings.smtp_use_tls:
                    smtp.starttls(context=context)
                    smtp.ehlo()
                if self.settings.smtp_username:
                    smtp.login(self.settings.smtp_username.strip(), self.settings.smtp_password)
                smtp.send_message(message)
        except (OSError, smtplib.SMTPException) as exc:
            raise EmailDeliveryError("密码重置验证码邮件发送失败。") from exc
