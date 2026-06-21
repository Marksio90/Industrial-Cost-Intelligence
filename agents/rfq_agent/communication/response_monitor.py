from __future__ import annotations

import asyncio
import email as email_lib
import imaplib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from email.header import decode_header
from typing import Any

import structlog

from ..config import AgentSettings

log = structlog.get_logger(__name__)


@dataclass
class InboundEmail:
    uid: str
    from_email: str
    from_name: str | None
    subject: str
    body_text: str
    body_html: str
    received_at: datetime
    in_reply_to: str | None
    references: list[str]
    raw: str


class ResponseMonitor:
    """
    Polls an IMAP mailbox for supplier responses to sent RFQs.
    Runs in an asyncio background task.
    """

    def __init__(
        self,
        settings: AgentSettings,
        on_message: Any,  # async callable(InboundEmail)
    ) -> None:
        self._settings = settings
        self._on_message = on_message
        self._running = False
        self._seen_uids: set[str] = set()

    async def start(self) -> None:
        self._running = True
        log.info("response_monitor_start", interval_s=self._settings.imap_poll_interval_s)
        while self._running:
            try:
                await self._poll()
            except Exception as exc:
                log.error("imap_poll_error", error=str(exc))
            await asyncio.sleep(self._settings.imap_poll_interval_s)

    async def stop(self) -> None:
        self._running = False

    async def _poll(self) -> None:
        loop = asyncio.get_event_loop()
        messages = await loop.run_in_executor(None, self._fetch_new_messages)
        for msg in messages:
            if msg.uid not in self._seen_uids:
                self._seen_uids.add(msg.uid)
                try:
                    await self._on_message(msg)
                except Exception as exc:
                    log.error("message_handler_error", uid=msg.uid, error=str(exc))

    def _fetch_new_messages(self) -> list[InboundEmail]:
        try:
            with imaplib.IMAP4_SSL(
                self._settings.imap_host, self._settings.imap_port
            ) as conn:
                conn.login(
                    self._settings.imap_username,
                    self._settings.imap_password.get_secret_value(),
                )
                conn.select(self._settings.imap_folder)

                # Search for unseen messages
                _, data = conn.search(None, "UNSEEN")
                uids = data[0].split() if data[0] else []
                log.debug("imap_unseen", count=len(uids))

                messages = []
                for uid_bytes in uids:
                    uid = uid_bytes.decode()
                    try:
                        msg = self._fetch_message(conn, uid)
                        if msg:
                            messages.append(msg)
                    except Exception as exc:
                        log.warning("imap_fetch_failed", uid=uid, error=str(exc))
                return messages
        except Exception as exc:
            log.error("imap_connect_failed", error=str(exc))
            return []

    def _fetch_message(self, conn: imaplib.IMAP4_SSL, uid: str) -> InboundEmail | None:
        _, msg_data = conn.fetch(uid, "(RFC822)")
        if not msg_data or msg_data[0] is None:
            return None

        raw_bytes = msg_data[0][1]
        if not isinstance(raw_bytes, bytes):
            return None
        raw = raw_bytes.decode("utf-8", errors="replace")
        msg = email_lib.message_from_bytes(raw_bytes)

        from_header = msg.get("From", "")
        from_name, from_email = _parse_address(from_header)
        subject = _decode_header_value(msg.get("Subject", ""))
        body_text, body_html = _extract_body(msg)
        date_str = msg.get("Date", "")
        received_at = _parse_date(date_str)
        in_reply_to = msg.get("In-Reply-To")
        references_raw = msg.get("References", "")
        references = [r.strip() for r in references_raw.split() if r.strip()]

        return InboundEmail(
            uid=uid,
            from_email=from_email,
            from_name=from_name,
            subject=subject,
            body_text=body_text,
            body_html=body_html,
            received_at=received_at,
            in_reply_to=in_reply_to,
            references=references,
            raw=raw,
        )


def _decode_header_value(raw: str) -> str:
    parts = decode_header(raw)
    decoded = []
    for part, enc in parts:
        if isinstance(part, bytes):
            decoded.append(part.decode(enc or "utf-8", errors="replace"))
        else:
            decoded.append(part)
    return "".join(decoded)


def _parse_address(raw: str) -> tuple[str | None, str]:
    name_part, addr = email_lib.utils.parseaddr(raw)
    return (name_part or None, addr.lower())


def _extract_body(msg: email_lib.message.Message) -> tuple[str, str]:
    text_parts: list[str] = []
    html_parts: list[str] = []

    for part in msg.walk():
        ct = part.get_content_type()
        if ct == "text/plain":
            payload = part.get_payload(decode=True)
            if payload:
                charset = part.get_content_charset() or "utf-8"
                text_parts.append(payload.decode(charset, errors="replace"))
        elif ct == "text/html":
            payload = part.get_payload(decode=True)
            if payload:
                charset = part.get_content_charset() or "utf-8"
                html_parts.append(payload.decode(charset, errors="replace"))

    return "\n".join(text_parts), "\n".join(html_parts)


def _parse_date(date_str: str) -> datetime:
    try:
        import email.utils as eutils
        parsed = eutils.parsedate_to_datetime(date_str)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return datetime.now(timezone.utc)
