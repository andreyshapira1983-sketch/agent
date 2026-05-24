"""
runtime/email_sender.py — Send chat-style replies over SMTP.

`EmailIntakeChannel` already polls IMAP. `EmailTool` already speaks SMTP
but it is shaped as a workflow tool (returns `ToolResult`, has a spec,
honours dry-run). This module is its tiny outbound twin — a `send_text`
that returns a plain `bool`, matching `TelegramSender`'s API so the
live loop can reply to email conversations without conditional code.

We deliberately keep this thin: no attachments here, no MIME juggling.
Outbound emails with deliverables go through the workflow's `email`
tool as before. This sender exists for the *chat* path only:

    client emails "Hey Anya, when do you usually deliver?"
        → ChatHandler.handle (no attachment → conversational reply)
        → live_loop calls EmailSender.send_text(...)
        → client gets a human-tone email back, threaded by subject.

Failure mode: SMTP errors return False instead of raising. The live
loop logs and moves on — a stuck reply must never crash the agent.
"""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from typing import Any

from brain.secrets import SecretsVault

logger = logging.getLogger(__name__)


class EmailSender:
    """Outbound side of the email channel — plain SMTP, plain text only."""

    def __init__(
        self,
        *,
        vault: SecretsVault,
        smtp_host: str = "smtp.gmail.com",
        smtp_port: int = 587,
        timeout: float = 15.0,
        smtp_factory=None,
        dry_run: bool = False,
    ) -> None:
        """
        Args:
            vault:       Must hold EMAIL_USERNAME + EMAIL_PASSWORD.
            smtp_host:   Mail server. Default gmail.
            smtp_port:   SMTP submission port. Default 587 (STARTTLS).
            timeout:     Hard cap per send.
            smtp_factory: Override for tests; default uses `smtplib.SMTP`.
            dry_run:     When True, the sender pretends to send and
                         returns True without touching the network.
                         Pair with runtime's AGENT_DRY_RUN so the same
                         flag governs every outbound message.
        """
        self._vault = vault
        self._smtp_host = smtp_host
        self._smtp_port = int(smtp_port)
        self._timeout = float(timeout)
        self._smtp_factory = smtp_factory
        self._dry_run = bool(dry_run)

    # ────────────────────────────────────────────────────────────────

    def send_text(
        self,
        *,
        to: str,
        subject: str,
        body: str,
        in_reply_to: str | None = None,
    ) -> bool:
        """Send one plain-text email. Returns True on success, False on error.

        Args:
            to:          Recipient address.
            subject:     Subject line. Empty strings get a safe fallback.
            body:        Message body. Empty body is treated as no-op.
            in_reply_to: Optional Message-ID to thread the reply.
        """
        recipient = (to or "").strip()
        body = (body or "").strip()
        if not recipient or not body:
            return False

        creds = self._credentials()
        if creds is None:
            logger.warning("[EmailSender] credentials missing — skipping reply")
            return False
        username, password = creds

        msg = EmailMessage()
        msg["From"]       = username
        msg["To"]         = recipient
        msg["Subject"]    = subject.strip() or "Re:"
        msg["Date"]       = formatdate(localtime=True)
        msg["Message-ID"] = make_msgid()
        if in_reply_to:
            msg["In-Reply-To"] = in_reply_to
            msg["References"]  = in_reply_to
        msg.set_content(body)

        if self._dry_run:
            logger.info(
                "[EmailSender] DRY-RUN reply to=%s subject=%r bytes=%d",
                recipient, msg["Subject"], len(body),
            )
            return True

        factory = self._smtp_factory or smtplib.SMTP
        try:
            with factory(self._smtp_host, self._smtp_port, timeout=self._timeout) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                server.login(username, password)
                server.send_message(msg)
        except smtplib.SMTPAuthenticationError as exc:
            logger.warning("[EmailSender] auth failed (%s): %s", username, exc)
            return False
        except smtplib.SMTPException as exc:
            logger.warning("[EmailSender] SMTP error: %s", exc)
            return False
        except OSError as exc:
            logger.warning("[EmailSender] network error: %s", exc)
            return False

        logger.info("[EmailSender] sent to=%s subject=%r", recipient, msg["Subject"])
        return True

    # ────────────────────────────────────────────────────────────────

    def _credentials(self) -> tuple[str, str] | None:
        if not self._vault.has("EMAIL_USERNAME"):
            return None
        if not self._vault.has("EMAIL_PASSWORD"):
            return None
        user = self._vault.reveal("EMAIL_USERNAME").strip()
        pwd = self._vault.reveal("EMAIL_PASSWORD").replace(" ", "").strip()
        if not user or not pwd:
            return None
        return user, pwd
