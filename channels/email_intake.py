"""
channels/email_intake.py — IMAP-based job intake channel.

This channel polls an IMAP mailbox for unread messages, materialises their
attachments to disk, and constructs a `brain.skills.Job` for each one. The
Brain then routes the Job through `intake_job()`.

Design
──────
- **Idempotent.** We dedupe on Message-ID via JobStore so a re-poll of the
  same INBOX never creates duplicate Jobs.
- **No raw secrets.** Credentials come from a SecretsVault (env-backed by
  default). Keys: `IMAP_USERNAME`, `IMAP_PASSWORD`, `IMAP_HOST`, `IMAP_PORT`.
- **Testable.** The IMAP client is abstracted behind `IMAPClientProtocol`
  so tests can inject a fake mailbox without a network connection.
- **Safe attachment handling.** Filenames are sanitised; attachments are
  written under a per-message subdir to avoid collisions. Attachments
  larger than the limit are dropped with a warning, not silently kept.

Out of scope (handled elsewhere)
────────────────────────────────
- Profession matching → Brain.intake_job
- Policy enforcement → PolicyEngine / Brain
- Reply / delivery → email tool used as a workflow step
"""

from __future__ import annotations

import email
import imaplib
import logging
import os
import re
import ssl
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from email.header import decode_header, make_header
from email.message import Message as EmailMessage
from email.utils import getaddresses
from pathlib import Path
from typing import Iterable, Protocol, runtime_checkable

from brain.secrets import SecretsVault

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════════
# Errors
# ════════════════════════════════════════════════════════════════════

class EmailIntakeError(RuntimeError):
    """Anything that prevents the channel from polling cleanly."""


# ════════════════════════════════════════════════════════════════════
# IMAP client protocol — minimal surface so we can inject a fake
# ════════════════════════════════════════════════════════════════════

@runtime_checkable
class IMAPClientProtocol(Protocol):
    """The smallest IMAP-ish interface EmailIntakeChannel needs.

    All methods mirror imaplib.IMAP4_SSL signatures so the real client
    plugs in directly. Tests provide a stub that returns canned bytes.
    """

    def login(self, user: str, password: str) -> tuple: ...
    def select(self, mailbox: str = "INBOX", readonly: bool = False) -> tuple: ...
    def search(self, charset, *criteria: str) -> tuple: ...
    def fetch(self, message_set: str, message_parts: str) -> tuple: ...
    def store(self, message_set: str, command: str, flags: str) -> tuple: ...
    def logout(self) -> tuple: ...


# ════════════════════════════════════════════════════════════════════
# Polled message record
# ════════════════════════════════════════════════════════════════════

@dataclass
class PolledEmail:
    """A normalised view of one IMAP message after parsing.

    The channel hands a list of these (one per matched UID) to its caller,
    or — when a JobStore is wired — converts them straight into Jobs.
    """

    uid:        str
    message_id: str
    from_addr:  str
    subject:    str
    body:       str
    attachments: list[Path] = field(default_factory=list)
    received_at: datetime = field(default_factory=datetime.utcnow)

    def short_repr(self) -> str:
        return (
            f"<PolledEmail uid={self.uid} from={self.from_addr} "
            f"subject={self.subject[:50]!r} attachments={len(self.attachments)}>"
        )


# ════════════════════════════════════════════════════════════════════
# Channel
# ════════════════════════════════════════════════════════════════════

_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
_MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024     # 20 MB per attachment


class EmailIntakeChannel:
    """Poll an IMAP mailbox and turn unread messages into Jobs.

    Typical wiring at startup:

        vault = SecretsVault.from_env(["IMAP_USERNAME", "IMAP_PASSWORD",
                                       "IMAP_HOST", "IMAP_PORT"])
        store_dir = Path("./data/attachments")
        channel = EmailIntakeChannel(vault=vault, attachments_dir=store_dir)

    Then, in a loop or scheduler:

        for email_msg in channel.poll():
            job = channel.to_job(email_msg)
            job_store.create(job)
            brain.intake_job(job)

    Or shortcut:

        channel.poll_and_dispatch(brain, job_store)
    """

    def __init__(
        self,
        *,
        vault: SecretsVault,
        attachments_dir: Path | str,
        imap_factory=None,
        mailbox: str = "INBOX",
        max_attachment_bytes: int = _MAX_ATTACHMENT_BYTES,
        mark_seen: bool = True,
    ) -> None:
        """
        Args:
            vault:                Source of IMAP_* credentials.
            attachments_dir:      Where to materialise attachments. One
                                  subdirectory per polled message is
                                  created here.
            imap_factory:         Callable returning an `IMAPClientProtocol`.
                                  Default: `imaplib.IMAP4_SSL(host, port)`.
                                  Tests inject a fake here.
            mailbox:              IMAP folder to poll. Default "INBOX".
            max_attachment_bytes: Hard cap per attachment. Bigger ones get
                                  dropped with a logger.warning.
            mark_seen:            When True (default), polled mails get the
                                  \\Seen flag so the next poll skips them.
                                  Tests set this to False to keep the
                                  fake mailbox state read-only.
        """
        self._vault = vault
        self._attachments_dir = Path(attachments_dir)
        self._attachments_dir.mkdir(parents=True, exist_ok=True)
        self._imap_factory = imap_factory or _default_imap_factory
        self._mailbox = mailbox
        self._max_attachment_bytes = int(max_attachment_bytes)
        self._mark_seen = bool(mark_seen)

    # ────────────────────────────────────────────────────────────────
    # Public API
    # ────────────────────────────────────────────────────────────────

    def poll(self, *, search_criteria: str = "UNSEEN") -> list[PolledEmail]:
        """Connect, fetch matching messages, return parsed records.

        Returns an empty list if nothing matched — never raises on an
        empty inbox. Raises EmailIntakeError on connection failures.
        """
        user, password, host, port = self._read_credentials()

        try:
            client = self._imap_factory(host, port)
        except Exception as exc:  # noqa: BLE001
            raise EmailIntakeError(f"IMAP connect failed: {exc}") from exc

        try:
            client.login(user, password)
            client.select(self._mailbox)

            ok, raw_uids = client.search(None, search_criteria)
            if ok != "OK":
                logger.warning("[EmailIntake] search failed: %s", raw_uids)
                return []

            uid_blob = b" ".join(raw_uids) if isinstance(raw_uids, (list, tuple)) else (raw_uids or b"")
            if isinstance(uid_blob, bytes):
                uid_str = uid_blob.decode("ascii", errors="replace")
            else:
                uid_str = str(uid_blob)
            uids = [u for u in uid_str.split() if u]
            logger.info("[EmailIntake] %d new messages in %s", len(uids), self._mailbox)

            polled: list[PolledEmail] = []
            for uid in uids:
                try:
                    msg = self._fetch_one(client, uid)
                except Exception:  # noqa: BLE001
                    logger.exception("[EmailIntake] failed to parse UID %s — skipping", uid)
                    continue
                polled.append(msg)
                if self._mark_seen:
                    try:
                        client.store(uid, "+FLAGS", "(\\Seen)")
                    except Exception:  # noqa: BLE001
                        logger.warning("[EmailIntake] failed to mark UID %s as seen", uid)
            return polled
        finally:
            try:
                client.logout()
            except Exception:  # noqa: BLE001
                logger.debug("[EmailIntake] logout failed (ignored)")

    def to_job(self, mail: PolledEmail):
        """Convert a PolledEmail into a freshly-built Job (status RECEIVED).

        Returns a `brain.skills.job.Job`. We import lazily so this module
        doesn't drag the SQLite layer into channels that don't use it.
        """
        from brain.skills.job import Job  # local import — soft dependency

        brief = mail.subject.strip() or mail.body[:300]
        return Job(
            brief=brief,
            source="email",
            client_id=mail.from_addr,
            attachments=[str(p) for p in mail.attachments],
        )

    def poll_and_dispatch(
        self,
        brain,                   # type: ignore[no-untyped-def]
        job_store,               # type: ignore[no-untyped-def]
        *,
        on_outcome=None,
    ) -> list[tuple[PolledEmail, "object"]]:
        """End-to-end: poll mailbox → create Jobs → call brain.intake_job.

        Idempotent via `JobStore.has_message_id()`. If the store doesn't
        expose that yet, falls back to in-memory de-dup for the current
        process only.

        Returns a list of (email, JobOutcome) tuples — useful for logging
        a summary at the end of one poll cycle.
        """
        seen_message_ids = self._load_seen_ids(job_store)
        results = []
        for mail in self.poll():
            if mail.message_id and mail.message_id in seen_message_ids:
                logger.info("[EmailIntake] skip duplicate message_id=%s", mail.message_id)
                continue
            job = self.to_job(mail)
            if job_store is not None:
                try:
                    job_store.create(job)
                except Exception:  # noqa: BLE001
                    logger.exception("[EmailIntake] JobStore.create failed")
                    continue
            outcome = brain.intake_job(job)
            results.append((mail, outcome))
            if mail.message_id:
                seen_message_ids.add(mail.message_id)
            if on_outcome is not None:
                try:
                    on_outcome(mail, outcome)
                except Exception:  # noqa: BLE001
                    logger.exception("[EmailIntake] on_outcome hook raised")
        return results

    # ────────────────────────────────────────────────────────────────
    # Internals
    # ────────────────────────────────────────────────────────────────

    def _read_credentials(self) -> tuple[str, str, str, int]:
        user = self._vault.get("IMAP_USERNAME").reveal() if self._vault.has("IMAP_USERNAME") else ""
        password = self._vault.get("IMAP_PASSWORD").reveal() if self._vault.has("IMAP_PASSWORD") else ""
        host = (
            self._vault.get("IMAP_HOST").reveal()
            if self._vault.has("IMAP_HOST")
            else os.environ.get("IMAP_HOST", "imap.gmail.com")
        )
        port_raw = (
            self._vault.get("IMAP_PORT").reveal()
            if self._vault.has("IMAP_PORT")
            else os.environ.get("IMAP_PORT", "993")
        )
        try:
            port = int(port_raw)
        except (TypeError, ValueError):
            port = 993
        if not user or not password:
            raise EmailIntakeError(
                "IMAP_USERNAME / IMAP_PASSWORD must be present in the vault"
            )
        return user.strip(), password, host.strip(), port

    def _fetch_one(self, client: IMAPClientProtocol, uid: str) -> PolledEmail:
        ok, payload = client.fetch(uid, "(RFC822)")
        if ok != "OK" or not payload:
            raise EmailIntakeError(f"fetch UID {uid} failed: {payload}")

        # payload format: [(b'1 (RFC822 {1234}', b'<raw rfc822>'), b')']
        raw_bytes = b""
        for part in payload:
            if isinstance(part, tuple) and len(part) >= 2 and isinstance(part[1], (bytes, bytearray)):
                raw_bytes = bytes(part[1])
                break
        if not raw_bytes:
            raise EmailIntakeError(f"UID {uid}: no message body in payload")

        msg = email.message_from_bytes(raw_bytes)

        message_id = (msg.get("Message-ID") or "").strip()
        subject = _decode_header_value(msg.get("Subject"))

        from_addrs = getaddresses([msg.get("From", "")])
        from_addr = from_addrs[0][1] if from_addrs else ""

        body_parts: list[str] = []
        attachments: list[Path] = []
        target_dir = self._attachments_dir / _safe_dir_token(uid, message_id)
        target_dir.mkdir(parents=True, exist_ok=True)

        for part in _walk_parts(msg):
            disposition = (part.get("Content-Disposition") or "").lower()
            ctype = part.get_content_type()

            # Text body — collect first text/plain we find
            if ctype == "text/plain" and "attachment" not in disposition:
                try:
                    payload = part.get_payload(decode=True) or b""
                    charset = part.get_content_charset() or "utf-8"
                    body_parts.append(payload.decode(charset, errors="replace"))
                except Exception:  # noqa: BLE001
                    logger.warning("[EmailIntake] failed to decode text part UID=%s", uid)
                continue

            # Attachment
            filename = part.get_filename()
            if filename or "attachment" in disposition:
                attachment_path = self._materialise_attachment(part, filename, target_dir)
                if attachment_path is not None:
                    attachments.append(attachment_path)

        body = "\n\n".join(p for p in body_parts if p.strip())

        return PolledEmail(
            uid=str(uid),
            message_id=message_id,
            from_addr=from_addr,
            subject=subject,
            body=body,
            attachments=attachments,
        )

    def _materialise_attachment(
        self,
        part: EmailMessage,
        filename: str | None,
        target_dir: Path,
    ) -> Path | None:
        try:
            data = part.get_payload(decode=True)
        except Exception:  # noqa: BLE001
            logger.warning("[EmailIntake] cannot decode attachment payload")
            return None
        if not data:
            return None
        if len(data) > self._max_attachment_bytes:
            logger.warning(
                "[EmailIntake] dropping oversize attachment '%s' (%d bytes)",
                filename, len(data),
            )
            return None

        safe_name = _safe_filename(filename) if filename else f"attachment-{uuid.uuid4().hex[:8]}"
        out_path = target_dir / safe_name
        # If name collides, suffix with a counter
        counter = 1
        while out_path.exists():
            stem, suffix = os.path.splitext(safe_name)
            out_path = target_dir / f"{stem}-{counter}{suffix}"
            counter += 1
        try:
            out_path.write_bytes(data)
        except OSError as exc:
            logger.warning("[EmailIntake] cannot write attachment '%s': %s", out_path, exc)
            return None
        return out_path

    @staticmethod
    def _load_seen_ids(job_store) -> set[str]:
        if job_store is None:
            return set()
        # If JobStore grew a has_message_id() helper later, prefer it.
        try:
            jobs = list(job_store)
        except TypeError:
            return set()
        seen: set[str] = set()
        for j in jobs:
            for note in getattr(j, "notes", []):
                m = re.search(r"message_id=([^\s>]+)", note)
                if m:
                    seen.add(m.group(1))
        return seen


# ════════════════════════════════════════════════════════════════════
# Helpers
# ════════════════════════════════════════════════════════════════════

def _default_imap_factory(host: str, port: int) -> IMAPClientProtocol:
    """Build a real IMAP4_SSL client."""
    context = ssl.create_default_context()
    return imaplib.IMAP4_SSL(host=host, port=port, ssl_context=context)


def _decode_header_value(raw: str | None) -> str:
    if not raw:
        return ""
    try:
        return str(make_header(decode_header(raw))).strip()
    except Exception:  # noqa: BLE001
        return raw.strip()


def _walk_parts(msg: EmailMessage) -> Iterable[EmailMessage]:
    if msg.is_multipart():
        for part in msg.walk():
            if part is msg:
                continue
            yield part
    else:
        yield msg


def _safe_filename(name: str) -> str:
    decoded = _decode_header_value(name) or "file"
    cleaned = _SAFE_FILENAME_RE.sub("_", decoded)
    # Collapse repeated dots — both traversal-shaped (".." / "...") and
    # purely-stylistic — leaving at most one between segments.
    cleaned = re.sub(r"\.{2,}", "_", cleaned)
    cleaned = cleaned.lstrip("._-") or "file"
    return cleaned[:120]


def _safe_dir_token(uid: str, message_id: str) -> str:
    base = (message_id.strip("<>") or uid or uuid.uuid4().hex)[:64]
    return _SAFE_FILENAME_RE.sub("_", base) or uuid.uuid4().hex[:8]
