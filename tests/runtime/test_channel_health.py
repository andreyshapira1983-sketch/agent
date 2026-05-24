"""Tests for the per-channel circuit breaker in runtime.live_loop.

Covers:
    * Auth failures disable the channel permanently and log ONCE.
    * Transient failures back off exponentially and log only on
      first failure and on recovery.
    * Successful polls reset health silently.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from runtime.chat import ChatReply
from runtime.live_loop import (
    LiveLoop,
    _BACKOFF_BASE_SECONDS,
    _BACKOFF_MAX_SECONDS,
    _ChannelHealth,
    _is_auth_failure,
)


# ════════════════════════════════════════════════════════════════════
# Fakes (kept tiny — full ones live in test_live_loop.py)
# ════════════════════════════════════════════════════════════════════

class _BoomIntake:
    """Telegram-shaped intake that raises a chosen exception on every poll."""

    def __init__(self, exc):
        self.exc = exc
        self.calls = 0

    def poll(self):
        self.calls += 1
        raise self.exc


class _FlakyIntake:
    """Fails N times, then returns []."""

    def __init__(self, fails: int, exc):
        self.remaining = fails
        self.exc = exc
        self.calls = 0

    def poll(self):
        self.calls += 1
        if self.remaining > 0:
            self.remaining -= 1
            raise self.exc
        return []


class _FakeAudit:
    def record(self, **_kw): pass


class _FakeRuntime:
    def __init__(self, *, telegram_intake=None, email_intake=None):
        self.telegram_intake = telegram_intake
        self.telegram_sender = _FakeSender()
        self.email_intake = email_intake
        self.email_sender = None
        self.chat = _NoopChat()
        self.audit = _FakeAudit()
        from runtime.config import AgentConfig
        self.config = AgentConfig(email_poll_seconds=60)
        self.notes = []


class _FakeSender:
    def send_text(self, _chat, _txt): return True


class _NoopChat:
    def handle(self, **_kw): return ChatReply(text="hi")


# ════════════════════════════════════════════════════════════════════
# Auth-failure detection
# ════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("msg", [
    "HTTP Error 401: Unauthorized",
    "Telegram getUpdates failed: HTTP Error 401: Unauthorized",
    "[AUTHENTICATIONFAILED] Invalid credentials (Failure)",
    "403 Forbidden",
    "bad credentials",
    "invalid token",
])
def test_auth_failure_detection_positive(msg):
    assert _is_auth_failure(RuntimeError(msg)) is True


@pytest.mark.parametrize("msg", [
    "Connection reset by peer",
    "timed out",
    "Name or service not known",
    "Server returned 500",
    "HTTP Error 502: Bad Gateway",
])
def test_auth_failure_detection_negative(msg):
    assert _is_auth_failure(RuntimeError(msg)) is False


# ════════════════════════════════════════════════════════════════════
# ChannelHealth state machine
# ════════════════════════════════════════════════════════════════════

def test_disabled_channel_never_polls():
    h = _ChannelHealth("x")
    h.disable("creds bad")
    assert h.may_poll(now=10_000.0) is False
    assert h.is_disabled() is True


def test_backoff_doubles_per_failure():
    h = _ChannelHealth("x")
    first, d1 = h.record_failure(RuntimeError("net"), now=0.0)
    assert first is True
    assert d1 == _BACKOFF_BASE_SECONDS
    _, d2 = h.record_failure(RuntimeError("net"), now=10.0)
    assert d2 == _BACKOFF_BASE_SECONDS * 2
    _, d3 = h.record_failure(RuntimeError("net"), now=20.0)
    assert d3 == _BACKOFF_BASE_SECONDS * 4


def test_backoff_caps_at_max():
    h = _ChannelHealth("x")
    for i in range(40):                       # 2^40 would overflow real time
        _, d = h.record_failure(RuntimeError("net"), now=float(i))
    assert d == _BACKOFF_MAX_SECONDS


def test_record_success_resets_and_signals_recovery():
    h = _ChannelHealth("x")
    h.record_failure(RuntimeError("net"), now=0.0)
    h.record_failure(RuntimeError("net"), now=5.0)
    recovered = h.record_success()
    assert recovered is True
    assert h.consecutive_failures == 0
    # A second success in a row is NOT a recovery
    assert h.record_success() is False


def test_may_poll_respects_next_attempt_time():
    h = _ChannelHealth("x")
    h.record_failure(RuntimeError("net"), now=100.0)
    assert h.may_poll(now=100.5) is False
    assert h.may_poll(now=100.0 + _BACKOFF_BASE_SECONDS) is True


# ════════════════════════════════════════════════════════════════════
# Integration with LiveLoop
# ════════════════════════════════════════════════════════════════════

def test_telegram_auth_failure_logs_once_and_disables(caplog):
    intake = _BoomIntake(RuntimeError("HTTP Error 401: Unauthorized"))
    rt = _FakeRuntime(telegram_intake=intake)
    loop = LiveLoop(rt, clock=lambda: 0.0, sleeper=lambda _s: None)

    with caplog.at_level(logging.WARNING, logger="runtime.live_loop"):
        for _ in range(20):
            loop._one_cycle()

    # Channel polled exactly ONCE — the disable killed all further polls
    assert intake.calls == 1
    # And we logged exactly one warning about it
    auth_warnings = [
        r for r in caplog.records
        if r.name == "runtime.live_loop" and "disabled" in r.message
    ]
    assert len(auth_warnings) == 1
    assert "401" in auth_warnings[0].message


def test_transient_failure_logs_once_then_silent_until_backoff(caplog):
    intake = _BoomIntake(RuntimeError("Connection reset by peer"))
    rt = _FakeRuntime(telegram_intake=intake)
    clock = {"t": 0.0}
    loop = LiveLoop(rt, clock=lambda: clock["t"], sleeper=lambda _s: None)

    with caplog.at_level(logging.WARNING, logger="runtime.live_loop"):
        # 10 rapid cycles within 0.5 seconds — only the first should poll
        for _ in range(10):
            loop._one_cycle()
            clock["t"] += 0.05

    assert intake.calls == 1
    warnings = [
        r for r in caplog.records
        if r.name == "runtime.live_loop" and "telegram poll failed" in r.message
    ]
    assert len(warnings) == 1
    assert "retrying in" in warnings[0].message


def test_transient_failure_retries_after_backoff(caplog):
    intake = _BoomIntake(RuntimeError("Connection reset"))
    rt = _FakeRuntime(telegram_intake=intake)
    clock = {"t": 0.0}
    loop = LiveLoop(rt, clock=lambda: clock["t"], sleeper=lambda _s: None)

    with caplog.at_level(logging.WARNING, logger="runtime.live_loop"):
        loop._one_cycle()                          # fail #1
        clock["t"] = _BACKOFF_BASE_SECONDS + 0.1   # backoff elapsed
        loop._one_cycle()                          # fail #2 — silent

    assert intake.calls == 2
    warnings = [
        r for r in caplog.records
        if r.name == "runtime.live_loop" and "telegram poll failed" in r.message
    ]
    # Still only ONE warning — repeats are silenced
    assert len(warnings) == 1


def test_recovery_after_transient_failures_logs_once(caplog):
    intake = _FlakyIntake(fails=2, exc=RuntimeError("Connection reset"))
    rt = _FakeRuntime(telegram_intake=intake)
    clock = {"t": 0.0}
    loop = LiveLoop(rt, clock=lambda: clock["t"], sleeper=lambda _s: None)

    with caplog.at_level(logging.INFO, logger="runtime.live_loop"):
        loop._one_cycle()                          # fail
        clock["t"] += _BACKOFF_BASE_SECONDS + 0.1
        loop._one_cycle()                          # fail
        clock["t"] += _BACKOFF_BASE_SECONDS * 2 + 0.1
        loop._one_cycle()                          # ok → recovery

    assert intake.calls == 3
    recoveries = [r for r in caplog.records if "recovered" in r.message]
    assert len(recoveries) == 1


def test_email_auth_failure_disables_channel_after_one_log(caplog):
    intake = _BoomIntake(RuntimeError("[AUTHENTICATIONFAILED] Invalid credentials"))
    rt = _FakeRuntime(email_intake=intake)
    clock = {"t": 0.0}
    loop = LiveLoop(rt, clock=lambda: clock["t"], sleeper=lambda _s: None)

    with caplog.at_level(logging.WARNING, logger="runtime.live_loop"):
        for _ in range(5):
            loop._one_cycle()
            clock["t"] += 100.0      # well past email_poll_seconds

    assert intake.calls == 1         # disabled after first failure
    auth_warns = [r for r in caplog.records if "disabled" in r.message]
    assert len(auth_warns) == 1
    assert "email" in auth_warns[0].message


def test_healthy_channel_emits_no_warnings(caplog):
    """Sanity: when polling succeeds, we never log per-cycle noise."""
    class _Quiet:
        def poll(self): return []
    rt = _FakeRuntime(telegram_intake=_Quiet())
    loop = LiveLoop(rt, clock=lambda: 0.0, sleeper=lambda _s: None)

    with caplog.at_level(logging.INFO, logger="runtime.live_loop"):
        for _ in range(50):
            loop._one_cycle()

    assert caplog.records == []
