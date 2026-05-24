"""
channels/telegram_intake.py — Telegram bot intake (lightweight wrapper).

This channel mirrors the public API of `EmailIntakeChannel` so a single
orchestrator loop can dispatch from either source without conditional
code paths. The intake uses Telegram's HTTP getUpdates endpoint
(long-poll) — no third-party SDK required.

What it does NOT do
───────────────────
- Handle voice / video / large media uploads. Anything bigger than the
  configured byte cap is rejected with a friendly error reply.
- Run a webhook server. Long-polling is intentionally chosen for
  symmetry with EmailIntakeChannel and for ease of local development.

Dependencies
────────────
Only stdlib (`urllib`). Credentials: `TELEGRAM_BOT_TOKEN` in the
SecretsVault.

This file is meant as a Phase-3 scaffold. The first production
deployment will likely swap the long-poller for a webhook + reverse
proxy; the public Channel methods stay the same.
"""

from __future__ import annotations

import io
import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from brain.secrets import SecretsVault

logger = logging.getLogger(__name__)


_TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"
_MAX_DOC_BYTES = 20 * 1024 * 1024


# ════════════════════════════════════════════════════════════════════
# Errors
# ════════════════════════════════════════════════════════════════════

class TelegramIntakeError(RuntimeError):
    """Raised when the channel cannot reach Telegram or auth fails."""


# ════════════════════════════════════════════════════════════════════
# Polled record
# ════════════════════════════════════════════════════════════════════

@dataclass
class PolledMessage:
    update_id:   int
    chat_id:     int
    user_id:     int
    username:    str
    text:        str
    received_at: datetime = field(default_factory=datetime.utcnow)
    attachments: list[Path] = field(default_factory=list)

    def short_repr(self) -> str:
        return (
            f"<PolledMessage update={self.update_id} chat={self.chat_id} "
            f"user={self.username!r} text={self.text[:60]!r}>"
        )


# ════════════════════════════════════════════════════════════════════
# Channel
# ════════════════════════════════════════════════════════════════════

class TelegramIntakeChannel:
    """Long-poll a Telegram bot and convert messages into Jobs.

    Usage mirrors `EmailIntakeChannel`:

        vault = SecretsVault.from_env(["TELEGRAM_BOT_TOKEN"])
        channel = TelegramIntakeChannel(
            vault=vault, attachments_dir=Path("./data/tg"),
        )
        channel.poll_and_dispatch(brain, job_store)
    """

    def __init__(
        self,
        *,
        vault: SecretsVault,
        attachments_dir: Path | str,
        http_factory=None,
        max_attachment_bytes: int = _MAX_DOC_BYTES,
        long_poll_timeout: int = 25,
        skip_backlog: bool = True,
    ) -> None:
        """
        Args:
            skip_backlog: When True (default), the very first `poll()` call
                quietly drops anything Telegram has buffered from before the
                bot started. Prevents the agent from waking up and replying
                to a year-old message stuck in the queue.
        """
        self._vault = vault
        self._attachments_dir = Path(attachments_dir)
        self._attachments_dir.mkdir(parents=True, exist_ok=True)
        self._http = http_factory or _default_http
        self._max_bytes = int(max_attachment_bytes)
        self._timeout = int(long_poll_timeout)
        self._offset = 0  # next update_id to fetch
        self._skip_backlog = bool(skip_backlog)
        self._initialised = False

    # ────────────────────────────────────────────────────────────────

    def poll(self) -> list[PolledMessage]:
        """Fetch new updates since the last call.

        On the very first call, if `skip_backlog=True` (default), any
        messages Telegram had buffered before the bot started are
        silently dropped — the agent only acts on conversations that
        happen while it's actually online.
        """
        token = self._token()
        if not self._initialised and self._skip_backlog:
            self._fast_forward_offset(token)
            self._initialised = True

        params = {"timeout": self._timeout, "offset": self._offset}
        try:
            updates = self._http(token, "getUpdates", params)
        except Exception as exc:  # noqa: BLE001
            raise TelegramIntakeError(f"Telegram getUpdates failed: {exc}") from exc

        if not updates.get("ok"):
            raise TelegramIntakeError(
                f"Telegram API returned not-ok: {updates.get('description')}"
            )

        polled: list[PolledMessage] = []
        for upd in updates.get("result", []):
            self._offset = max(self._offset, int(upd.get("update_id", 0)) + 1)
            msg = upd.get("message") or upd.get("channel_post")
            if not msg:
                continue
            try:
                polled.append(self._parse_message(token, upd["update_id"], msg))
            except Exception:  # noqa: BLE001
                logger.exception("[TelegramIntake] failed to parse update %s", upd.get("update_id"))
                continue
        return polled

    def _fast_forward_offset(self, token: str) -> None:
        """Discover the latest update_id without processing earlier messages.

        Telegram's getUpdates with `offset=-1` and `timeout=0` returns at
        most one update — the most recent one. We bump our offset just
        past it so the next real poll only sees brand-new traffic.
        """
        try:
            probe = self._http(token, "getUpdates", {"offset": -1, "timeout": 0})
        except Exception as exc:  # noqa: BLE001 — log + fall back to offset=0
            # DEBUG, not WARNING: if the token is broken the live loop's
            # circuit breaker will say so once with a clearer message. No
            # need to shout twice for the same root cause.
            logger.debug("[TelegramIntake] backlog fast-forward failed: %s", exc)
            return
        if not isinstance(probe, dict) or not probe.get("ok"):
            return
        results = probe.get("result") or []
        if not results:
            return
        latest_id = int(results[-1].get("update_id", 0))
        if latest_id > 0:
            self._offset = latest_id + 1
            logger.info(
                "[TelegramIntake] skipped backlog up to update_id=%s", latest_id,
            )

    # ────────────────────────────────────────────────────────────────

    def to_job(self, message: PolledMessage):
        from brain.skills.job import Job  # local import — soft dep
        client_id = f"telegram:{message.chat_id}:{message.username or message.user_id}"
        brief = message.text or "(no text)"
        return Job(
            brief=brief,
            source="telegram",
            client_id=client_id,
            attachments=[str(p) for p in message.attachments],
        )

    def poll_and_dispatch(self, brain, job_store, *, on_outcome=None):
        results = []
        for message in self.poll():
            job = self.to_job(message)
            if job_store is not None:
                try:
                    job_store.create(job)
                except Exception:  # noqa: BLE001
                    logger.exception("[TelegramIntake] JobStore.create failed")
                    continue
            outcome = brain.intake_job(job)
            results.append((message, outcome))
            if on_outcome is not None:
                try:
                    on_outcome(message, outcome)
                except Exception:  # noqa: BLE001
                    logger.exception("[TelegramIntake] on_outcome hook raised")
        return results

    # ────────────────────────────────────────────────────────────────
    # Internals
    # ────────────────────────────────────────────────────────────────

    def _token(self) -> str:
        if not self._vault.has("TELEGRAM_BOT_TOKEN"):
            raise TelegramIntakeError("TELEGRAM_BOT_TOKEN missing from vault")
        return self._vault.get("TELEGRAM_BOT_TOKEN").reveal()

    def _parse_message(self, token: str, update_id: int, msg: dict) -> PolledMessage:
        chat_id = int(msg.get("chat", {}).get("id", 0))
        sender = msg.get("from") or {}
        user_id = int(sender.get("id", 0))
        username = str(sender.get("username") or sender.get("first_name") or "")

        text = str(msg.get("text") or msg.get("caption") or "").strip()

        attachments: list[Path] = []
        doc = msg.get("document")
        if doc:
            attachment = self._download_document(token, doc)
            if attachment is not None:
                attachments.append(attachment)

        return PolledMessage(
            update_id=update_id,
            chat_id=chat_id,
            user_id=user_id,
            username=username,
            text=text,
            attachments=attachments,
        )

    def _download_document(self, token: str, doc: dict) -> Path | None:
        file_id = doc.get("file_id")
        file_name = str(doc.get("file_name") or f"tg-{file_id}.bin")
        file_size = int(doc.get("file_size") or 0)
        if not file_id or file_size > self._max_bytes:
            logger.warning("[TelegramIntake] skipping oversize/invalid document %s", file_name)
            return None
        try:
            meta = self._http(token, "getFile", {"file_id": file_id})
            file_path = meta.get("result", {}).get("file_path")
            if not file_path:
                return None
            url = f"https://api.telegram.org/file/bot{token}/{file_path}"
            with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310
                data = resp.read(self._max_bytes + 1)
            if len(data) > self._max_bytes:
                logger.warning("[TelegramIntake] document exceeded max bytes")
                return None
        except Exception:  # noqa: BLE001
            logger.exception("[TelegramIntake] download failed for file_id=%s", file_id)
            return None

        safe = _safe_filename(file_name)
        out = self._attachments_dir / f"{file_id}_{safe}"
        try:
            out.write_bytes(data)
        except OSError:
            logger.exception("[TelegramIntake] write_bytes failed for %s", out)
            return None
        return out


# ════════════════════════════════════════════════════════════════════
# Default HTTP transport
# ════════════════════════════════════════════════════════════════════

def _default_http(token: str, method: str, params: dict[str, Any]) -> dict:
    url = _TELEGRAM_API.format(token=token, method=method)
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310
        body = resp.read().decode("utf-8", errors="replace")
    return json.loads(body)


# ════════════════════════════════════════════════════════════════════
# Helpers
# ════════════════════════════════════════════════════════════════════

import re as _re
_SAFE_RE = _re.compile(r"[^A-Za-z0-9._-]+")


def _safe_filename(name: str) -> str:
    cleaned = _SAFE_RE.sub("_", name)
    cleaned = _re.sub(r"\.{2,}", "_", cleaned)
    cleaned = cleaned.lstrip("._-") or "file"
    return cleaned[:120]
