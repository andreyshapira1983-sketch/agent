"""
runtime/live_loop.py — Quiet, single-thread polling loop.

This is the runtime's main heartbeat. It:

    * long-polls Telegram (server-side timeout = 25s by default — quiet)
    * polls IMAP at most once per `email_poll_seconds` (default 60s)
    * routes each inbound message through `ChatHandler`
    * replies via the appropriate channel
    * emits AT MOST one log line per actual event — silence when idle

The loop NEVER prints background heartbeats. It NEVER dumps technical
state on a timer. The only output the operator sees is real activity.
"""

from __future__ import annotations

import logging
import signal
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .agent_runtime import AgentRuntime
from .chat import ChatReply

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════════
# Channel health — silence-first circuit breaker
# ════════════════════════════════════════════════════════════════════
#
# Why this exists:
#   When credentials are wrong (Telegram 401, IMAP AUTHENTICATIONFAILED),
#   the naive loop happily polls once per second and emits a WARNING
#   each time — exactly the "техника каждые 5 секунд" the operator
#   asked us to never produce. Same problem in milder form for transient
#   network errors.
#
# Policy:
#   * Auth errors  → log ONCE, disable channel permanently for this
#                    process. Operator fixes the token and restarts.
#   * Other errors → exponential backoff (2s → 4s → 8s → ... → 5min),
#                    log only on the FIRST failure and on recovery.
#   * Success      → reset health silently.

_BACKOFF_BASE_SECONDS = 2.0
_BACKOFF_MAX_SECONDS  = 300.0   # 5 minutes between retries when broken


@dataclass
class _ChannelHealth:
    """Per-channel circuit breaker state.

    Only the loop reads/writes this — channels themselves stay dumb.
    """
    name: str
    consecutive_failures: int = 0
    next_attempt_at: float = 0.0
    disabled_reason: str | None = None      # set ⇒ never poll again

    def is_disabled(self) -> bool:
        return self.disabled_reason is not None

    def may_poll(self, now: float) -> bool:
        if self.is_disabled():
            return False
        return now >= self.next_attempt_at

    def record_success(self) -> bool:
        """Returns True if this is a recovery (was failing, now ok)."""
        recovered = self.consecutive_failures > 0
        self.consecutive_failures = 0
        self.next_attempt_at = 0.0
        return recovered

    def record_failure(self, exc: BaseException, now: float) -> tuple[bool, float]:
        """Returns (is_first_failure, retry_in_seconds)."""
        first = self.consecutive_failures == 0
        self.consecutive_failures += 1
        delay = min(
            _BACKOFF_MAX_SECONDS,
            _BACKOFF_BASE_SECONDS * (2 ** (self.consecutive_failures - 1)),
        )
        self.next_attempt_at = now + delay
        return first, delay

    def disable(self, reason: str) -> None:
        self.disabled_reason = reason
        self.consecutive_failures += 1
        self.next_attempt_at = float("inf")


def _is_auth_failure(exc: BaseException) -> bool:
    """Heuristic — does this exception look like 'your credentials are wrong'?"""
    text = str(exc).lower()
    return any(token in text for token in (
        "401", "403",
        "unauthorized", "forbidden",
        "authenticationfailed", "authentication failed",
        "invalid credentials", "bad credentials",
        "invalid token", "token rejected",
    ))


@dataclass
class _Tick:
    # `None` means "never polled yet" — the first cycle always runs email
    # so the operator doesn't have to wait `email_poll_seconds` for the
    # mailbox to be checked when the agent starts.
    last_email_at: float | None = None
    telegram: _ChannelHealth = field(default_factory=lambda: _ChannelHealth("telegram"))
    email:    _ChannelHealth = field(default_factory=lambda: _ChannelHealth("email"))


class LiveLoop:
    """One thread, two pollers, zero idle chatter.

    Stop conditions:
        * SIGINT / SIGTERM (graceful shutdown)
        * `loop.stop()` from another thread (rare — mostly used in tests)
    """

    def __init__(
        self,
        rt: AgentRuntime,
        *,
        clock=time.monotonic,
        sleeper=time.sleep,
    ) -> None:
        self._rt = rt
        self._clock = clock
        self._sleep = sleeper
        self._running = False
        self._tick = _Tick()

    # ────────────────────────────────────────────────────────────────

    def stop(self) -> None:
        self._running = False

    def run_forever(self, *, install_signal_handlers: bool = True) -> None:
        self._running = True
        if install_signal_handlers:
            self._install_signals()

        logger.info("[live] starting — %s", self._rt.notes[-1] if self._rt.notes else "")
        try:
            while self._running:
                events = self._one_cycle()
                if events == 0:
                    # Idle: short sleep, no log line.
                    self._sleep(1.0)
        finally:
            close = getattr(self._rt, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:  # noqa: BLE001
                    logger.exception("[live] runtime.close() raised")
            logger.info("[live] stopped")

    # ────────────────────────────────────────────────────────────────

    def _one_cycle(self) -> int:
        """Run one pass. Returns count of events processed (for tests)."""
        events = 0
        events += self._poll_telegram()
        if self._email_due():
            events += self._poll_email()
            self._tick.last_email_at = self._clock()
        return events

    # ── Telegram ────────────────────────────────────────────────
    def _poll_telegram(self) -> int:
        intake = self._rt.telegram_intake
        sender = self._rt.telegram_sender
        if intake is None or sender is None:
            return 0
        if not self._tick.telegram.may_poll(self._clock()):
            return 0
        try:
            messages = intake.poll()
        except Exception as exc:  # noqa: BLE001 — never crash the loop
            self._note_channel_failure(self._tick.telegram, exc)
            return 0
        self._note_channel_success(self._tick.telegram)
        if not messages:
            return 0
        logger.info("[live] telegram: %d new message(s)", len(messages))
        for msg in messages:
            self._handle_chat_message(msg, sender)
        return len(messages)

    def _handle_chat_message(self, msg, sender) -> None:
        client_id = f"telegram:{msg.chat_id}:{msg.username or msg.user_id}"
        reply: ChatReply = self._rt.chat.handle(
            brief=msg.text,
            client_id=client_id,
            attachments=[str(p) for p in (msg.attachments or [])],
            source="telegram",
        )
        if reply.text.strip():
            ok = sender.send_text(msg.chat_id, reply.text)
            if not ok:
                logger.warning("[live] telegram reply failed for chat=%s", msg.chat_id)
        if reply.deliverables:
            logger.info("[live] deliverables ready: %s", reply.deliverables)
        self._audit_chat(source="telegram", client_id=client_id, reply=reply)

    # ── Email ───────────────────────────────────────────────────
    def _email_due(self) -> bool:
        if self._rt.email_intake is None:
            return False
        # Honour the channel's own backoff/disable first — never poll a
        # channel the breaker has tripped.
        if not self._tick.email.may_poll(self._clock()):
            return False
        if self._tick.last_email_at is None:
            return True
        now = self._clock()
        return (now - self._tick.last_email_at) >= self._rt.config.email_poll_seconds

    def _poll_email(self) -> int:
        intake = self._rt.email_intake
        if intake is None:
            return 0
        try:
            mails = intake.poll()
        except Exception as exc:  # noqa: BLE001
            self._note_channel_failure(self._tick.email, exc)
            return 0
        self._note_channel_success(self._tick.email)
        if not mails:
            return 0
        logger.info("[live] email: %d new message(s)", len(mails))
        for mail in mails:
            self._handle_email(mail)
        return len(mails)

    def _handle_email(self, mail) -> None:
        client_id = mail.from_addr or "anonymous@inbox"
        brief = (mail.subject or "").strip() or (mail.body or "")[:300]
        reply = self._rt.chat.handle(
            brief=brief,
            client_id=client_id,
            attachments=[str(p) for p in (mail.attachments or [])],
            source="email",
        )
        # Two distinct cases:
        #   1. Workflow path: the workflow ran the `email` tool itself
        #      to deliver the artifact. We MUST NOT also reply here, or
        #      the client gets two emails. Skip outbound.
        #   2. Conversation path: chat-only reply, no workflow output.
        #      Without an email-out side the client just gets silence,
        #      so the loop sends the reply via EmailSender.
        if (
            self._rt.email_sender is not None
            and reply.text.strip()
            and not reply.used_workflow
        ):
            subject = _reply_subject(mail.subject)
            sent = self._rt.email_sender.send_text(
                to=client_id,
                subject=subject,
                body=reply.text,
                in_reply_to=getattr(mail, "message_id", None),
            )
            if not sent:
                logger.warning("[live] email reply failed for to=%s", client_id)
        self._audit_chat(source="email", client_id=client_id, reply=reply)

    # ── Channel health ──────────────────────────────────────────
    def _note_channel_failure(self, health: _ChannelHealth, exc: BaseException) -> None:
        """One log line per state change — never one per poll."""
        if _is_auth_failure(exc):
            # Already disabled? Stay silent — we already shouted once.
            if health.is_disabled():
                return
            reason = f"auth failure ({type(exc).__name__}): {exc}"
            health.disable(reason)
            logger.warning(
                "[live] %s: %s — channel disabled. "
                "Fix the credentials in .env and restart.",
                health.name, reason,
            )
            return

        first, delay = health.record_failure(exc, self._clock())
        if first:
            # Speak once, then go quiet while we back off.
            logger.warning(
                "[live] %s poll failed: %s — retrying in %.0fs (silenced until then)",
                health.name, exc, delay,
            )
        # Subsequent failures stay silent. When/if we recover, we log it.

    def _note_channel_success(self, health: _ChannelHealth) -> None:
        if health.record_success():
            logger.info("[live] %s: recovered", health.name)

    # ── Audit ───────────────────────────────────────────────────
    def _audit_chat(self, *, source: str, client_id: str, reply: ChatReply) -> None:
        try:
            self._rt.audit.record(
                actor="channel",
                action="chat_reply",
                target=source,
                verdict="OK" if not reply.needs_approval else "REQUIRE_APPROVAL",
                params={
                    "client_id":    client_id,
                    "used_workflow": reply.used_workflow,
                    "job_id":        reply.job_id,
                    "deliverables":  reply.deliverables,
                    "preview":       reply.text[:120],
                },
            )
        except Exception:  # noqa: BLE001 — audit failures must not break chat
            logger.exception("[live] audit.record raised")

    # ── Signals ─────────────────────────────────────────────────
    def _install_signals(self) -> None:
        def handler(signum, _frame):  # type: ignore[no-untyped-def]
            logger.info("[live] caught signal %s — shutting down", signum)
            self.stop()
        try:
            signal.signal(signal.SIGINT, handler)
        except (ValueError, OSError):
            pass
        try:
            signal.signal(signal.SIGTERM, handler)
        except (AttributeError, ValueError, OSError):
            pass


# ════════════════════════════════════════════════════════════════════
# Convenience entry point
# ════════════════════════════════════════════════════════════════════

def run_forever(rt: AgentRuntime) -> None:
    """Build a `LiveLoop` and run it. Blocks the current thread."""
    LiveLoop(rt).run_forever()


# ════════════════════════════════════════════════════════════════════
# Helpers
# ════════════════════════════════════════════════════════════════════

def _reply_subject(original: str | None) -> str:
    """Return a subject prefixed with ``Re:`` exactly once."""
    base = (original or "").strip() or "your message"
    if base.lower().startswith("re:"):
        return base
    return f"Re: {base}"
