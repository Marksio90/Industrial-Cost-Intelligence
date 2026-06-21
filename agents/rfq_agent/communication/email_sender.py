from __future__ import annotations

import asyncio
import email.utils
import smtplib
import ssl
import uuid
from dataclasses import dataclass
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate
from typing import Any

import structlog

from ..config import AgentSettings

log = structlog.get_logger(__name__)

_UNSUBSCRIBE_URL_PLACEHOLDER = "{UNSUBSCRIBE_URL}"


@dataclass
class SendResult:
    success: bool
    message_id: str | None = None
    error: str | None = None


class EmailSender:
    """
    Dual-backend email sender: SMTP (default) or SendGrid.
    Automatically injects unsubscribe URL and handles MIME encoding.
    """

    def __init__(self, settings: AgentSettings) -> None:
        self._settings = settings

    async def send(
        self,
        to_email: str,
        to_name: str,
        subject: str,
        body_html: str,
        body_text: str,
        unsubscribe_url: str | None = None,
    ) -> SendResult:
        body_html = _inject_unsubscribe(body_html, unsubscribe_url)
        body_text = _inject_unsubscribe(body_text, unsubscribe_url)

        if self._settings.email_backend == "sendgrid":
            return await self._send_sendgrid(to_email, to_name, subject, body_html, body_text)
        return await self._send_smtp(to_email, to_name, subject, body_html, body_text)

    # ── SMTP ──────────────────────────────────────────────────────────────────

    async def _send_smtp(
        self,
        to_email: str,
        to_name: str,
        subject: str,
        body_html: str,
        body_text: str,
    ) -> SendResult:
        msg = _build_mime(
            from_name=self._settings.smtp_from_name,
            from_email=self._settings.smtp_from_email,
            to_name=to_name,
            to_email=to_email,
            subject=subject,
            body_html=body_html,
            body_text=body_text,
        )
        message_id: str = msg["Message-ID"]

        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(None, self._smtp_send, msg, to_email)
            log.info("email_sent_smtp", to=to_email, subject=subject)
            return SendResult(success=True, message_id=message_id)
        except Exception as exc:
            log.error("smtp_send_failed", to=to_email, error=str(exc))
            return SendResult(success=False, error=str(exc))

    def _smtp_send(self, msg: MIMEMultipart, to_email: str) -> None:
        ctx = ssl.create_default_context()
        with smtplib.SMTP(self._settings.smtp_host, self._settings.smtp_port) as server:
            if self._settings.smtp_use_tls:
                server.starttls(context=ctx)
            if self._settings.smtp_username:
                server.login(
                    self._settings.smtp_username,
                    self._settings.smtp_password.get_secret_value(),
                )
            server.send_message(msg)

    # ── SendGrid ──────────────────────────────────────────────────────────────

    async def _send_sendgrid(
        self,
        to_email: str,
        to_name: str,
        subject: str,
        body_html: str,
        body_text: str,
    ) -> SendResult:
        if not self._settings.sendgrid_api_key:
            raise ValueError("SENDGRID_API_KEY not configured")

        import httpx

        message_id = f"<{uuid.uuid4()}@sendgrid.ici>"
        payload: dict[str, Any] = {
            "personalizations": [{"to": [{"email": to_email, "name": to_name}]}],
            "from": {
                "email": self._settings.smtp_from_email,
                "name": self._settings.smtp_from_name,
            },
            "subject": subject,
            "content": [
                {"type": "text/plain", "value": body_text},
                {"type": "text/html", "value": body_html},
            ],
            "headers": {"Message-ID": message_id},
            "tracking_settings": {
                "click_tracking": {"enable": False},
                "open_tracking": {"enable": False},
            },
        }

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.sendgrid.com/v3/mail/send",
                json=payload,
                headers={
                    "Authorization": f"Bearer {self._settings.sendgrid_api_key.get_secret_value()}"
                },
            )
            if resp.status_code in (200, 202):
                log.info("email_sent_sendgrid", to=to_email)
                return SendResult(success=True, message_id=message_id)
            log.error("sendgrid_error", status=resp.status_code, body=resp.text[:200])
            return SendResult(success=False, error=f"SendGrid {resp.status_code}: {resp.text[:100]}")


def _build_mime(
    from_name: str,
    from_email: str,
    to_name: str,
    to_email: str,
    subject: str,
    body_html: str,
    body_text: str,
) -> MIMEMultipart:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = email.utils.formataddr((from_name, from_email))
    msg["To"] = email.utils.formataddr((to_name, to_email))
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = f"<{uuid.uuid4()}@ici.procurement>"
    msg["X-Mailer"] = "ICI-RFQ-Agent/1.0"
    # RFC 2369 list-unsubscribe (needed for deliverability)
    msg["List-Unsubscribe"] = "<mailto:unsubscribe@ici.procurement>"
    msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"

    msg.attach(MIMEText(body_text, "plain", "utf-8"))
    msg.attach(MIMEText(body_html, "html", "utf-8"))
    return msg


def _inject_unsubscribe(text: str, url: str | None) -> str:
    replacement = url or "https://procurement.example.com/unsubscribe"
    return text.replace(_UNSUBSCRIBE_URL_PLACEHOLDER, replacement)
