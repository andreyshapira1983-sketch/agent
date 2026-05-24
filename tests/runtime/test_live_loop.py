"""Tests for runtime.live_loop.LiveLoop — quiet, event-driven polling."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from runtime.chat import ChatReply
from runtime.live_loop import LiveLoop


# ════════════════════════════════════════════════════════════════════
# Fakes
# ════════════════════════════════════════════════════════════════════

@dataclass
class _FakeMsg:
    chat_id: int = 42
    username: str = "alice"
    user_id: int = 7
    text: str = "hi"
    attachments: list = field(default_factory=list)


class _FakeTelegramIntake:
    def __init__(self, messages_per_call):
        self.messages_per_call = list(messages_per_call)
        self.calls = 0

    def poll(self):
        if self.calls < len(self.messages_per_call):
            out = self.messages_per_call[self.calls]
        else:
            out = []
        self.calls += 1
        return out


class _FakeTelegramSender:
    def __init__(self):
        self.sent = []

    def send_text(self, chat_id, text):
        self.sent.append((chat_id, text))
        return True


class _FakeChatHandler:
    def __init__(self, reply=None):
        self.reply = reply or ChatReply(text="ok")
        self.calls = []

    def handle(self, *, brief, client_id, attachments=None, source="chat"):
        self.calls.append({
            "brief": brief, "client_id": client_id,
            "attachments": list(attachments or []), "source": source,
        })
        return self.reply


class _FakeAudit:
    def __init__(self):
        self.records = []

    def record(self, **kwargs):
        self.records.append(kwargs)


class _FakeEmailSender:
    def __init__(self, ok=True):
        self.sent = []
        self.ok = ok

    def send_text(self, *, to, subject, body, in_reply_to=None):
        self.sent.append({
            "to": to, "subject": subject, "body": body,
            "in_reply_to": in_reply_to,
        })
        return self.ok


class _FakeRuntime:
    def __init__(self, *, telegram_intake=None, telegram_sender=None,
                 email_intake=None, email_sender=None,
                 chat=None, audit=None,
                 email_poll_seconds: int = 60):
        self.telegram_intake = telegram_intake
        self.telegram_sender = telegram_sender
        self.email_intake = email_intake
        self.email_sender = email_sender
        self.chat = chat or _FakeChatHandler()
        self.audit = audit or _FakeAudit()
        from runtime.config import AgentConfig
        self.config = AgentConfig(email_poll_seconds=email_poll_seconds)
        self.notes = ["bootstrapped"]


# ════════════════════════════════════════════════════════════════════
# Tests
# ════════════════════════════════════════════════════════════════════

def test_loop_is_silent_when_idle():
    """No messages → loop returns 0 events; nothing logged or sent."""
    rt = _FakeRuntime(
        telegram_intake=_FakeTelegramIntake([[]]),
        telegram_sender=_FakeTelegramSender(),
    )
    loop = LiveLoop(rt, clock=lambda: 0.0, sleeper=lambda _s: None)
    events = loop._one_cycle()
    assert events == 0
    assert rt.telegram_sender.sent == []
    assert rt.audit.records == []
    assert rt.chat.calls == []


def test_telegram_message_yields_one_reply():
    intake = _FakeTelegramIntake([[_FakeMsg(text="hi", chat_id=99)]])
    sender = _FakeTelegramSender()
    chat = _FakeChatHandler(reply=ChatReply(text="привет"))
    rt = _FakeRuntime(telegram_intake=intake, telegram_sender=sender, chat=chat)
    loop = LiveLoop(rt, clock=lambda: 0.0, sleeper=lambda _s: None)
    events = loop._one_cycle()
    assert events == 1
    assert sender.sent == [(99, "привет")]
    assert chat.calls[0]["brief"] == "hi"
    assert "telegram:99:alice" in chat.calls[0]["client_id"]
    assert len(rt.audit.records) == 1
    assert rt.audit.records[0]["action"] == "chat_reply"


def test_telegram_attachment_propagates_to_chat_handler():
    intake = _FakeTelegramIntake(
        [[_FakeMsg(text="edit", attachments=[Path("/tmp/a.docx")])]]
    )
    sender = _FakeTelegramSender()
    chat = _FakeChatHandler(
        reply=ChatReply(
            text="готово", used_workflow=True, job_id="j", deliverables=["d"],
        )
    )
    rt = _FakeRuntime(telegram_intake=intake, telegram_sender=sender, chat=chat)
    LiveLoop(rt, clock=lambda: 0.0, sleeper=lambda _s: None)._one_cycle()
    # attachment path forwarded as string (OS-agnostic — Path repr differs)
    assert len(chat.calls[0]["attachments"]) == 1
    assert "a.docx" in chat.calls[0]["attachments"][0]
    assert sender.sent == [(42, "готово")]


def test_telegram_poll_exception_is_swallowed():
    class _Boom:
        def poll(self):
            raise RuntimeError("nope")
    rt = _FakeRuntime(telegram_intake=_Boom(), telegram_sender=_FakeTelegramSender())
    loop = LiveLoop(rt, clock=lambda: 0.0, sleeper=lambda _s: None)
    assert loop._one_cycle() == 0
    assert rt.telegram_sender.sent == []


def test_email_only_polls_after_interval():
    class _FakeEmailIntake:
        def __init__(self):
            self.poll_count = 0
        def poll(self):
            self.poll_count += 1
            return []

    fake_email = _FakeEmailIntake()
    rt = _FakeRuntime(email_intake=fake_email, email_poll_seconds=60)

    clock = {"t": 0.0}
    loop = LiveLoop(rt, clock=lambda: clock["t"], sleeper=lambda _s: None)

    # First cycle at t=0: email_due returns True (last_email_at=0 → delta=0,
    # but >= 60 fails; we need to start with last set to 0 AND clock at 60+)
    loop._one_cycle()
    assert fake_email.poll_count == 1  # first call at t=0 always runs

    # Second cycle 5s later — too soon
    clock["t"] = 5.0
    loop._one_cycle()
    assert fake_email.poll_count == 1

    # Third cycle 65s later — due
    clock["t"] = 65.0
    loop._one_cycle()
    assert fake_email.poll_count == 2


def test_email_message_routes_through_chat_handler():
    @dataclass
    class _FakeEmail:
        subject: str = "edit please"
        body: str = ""
        from_addr: str = "alice@example.com"
        attachments: list = field(default_factory=list)

    class _FakeIntake:
        def poll(self):
            return [_FakeEmail(attachments=[Path("/tmp/file.docx")])]

    chat = _FakeChatHandler(
        reply=ChatReply(text="ack", used_workflow=True, job_id="j2"),
    )
    rt = _FakeRuntime(email_intake=_FakeIntake(), chat=chat)
    LiveLoop(rt, clock=lambda: 0.0, sleeper=lambda _s: None)._one_cycle()
    assert len(chat.calls) == 1
    assert chat.calls[0]["source"] == "email"
    assert chat.calls[0]["client_id"] == "alice@example.com"
    assert chat.calls[0]["brief"] == "edit please"
    # No telegram_sender wired — but audit MUST still record the event
    assert len(rt.audit.records) == 1


def test_loop_stop_terminates_run_forever_quickly():
    rt = _FakeRuntime(
        telegram_intake=_FakeTelegramIntake([[]]),
        telegram_sender=_FakeTelegramSender(),
    )

    counters = {"sleeps": 0}
    loop = LiveLoop(
        rt,
        clock=lambda: 0.0,
        sleeper=lambda _s: counters.update(sleeps=counters["sleeps"] + 1) or
                          (loop.stop() if counters["sleeps"] >= 2 else None),
    )
    loop.run_forever(install_signal_handlers=False)
    # Should have exited after a couple idle cycles
    assert counters["sleeps"] >= 2


def test_audit_failure_does_not_break_message_flow():
    class _FailingAudit:
        def record(self, **_kw):
            raise RuntimeError("audit db locked")
    intake = _FakeTelegramIntake([[_FakeMsg(text="ping")]])
    sender = _FakeTelegramSender()
    rt = _FakeRuntime(
        telegram_intake=intake, telegram_sender=sender,
        chat=_FakeChatHandler(), audit=_FailingAudit(),
    )
    # Must not raise — audit failures stay internal.
    LiveLoop(rt, clock=lambda: 0.0, sleeper=lambda _s: None)._one_cycle()
    assert len(sender.sent) == 1


# ════════════════════════════════════════════════════════════════════
# Email-out symmetry — conversational reply via EmailSender
# ════════════════════════════════════════════════════════════════════

@dataclass
class _FakeEmail:
    subject: str = "edit please"
    body: str = ""
    from_addr: str = "alice@example.com"
    message_id: str = "<msg-1@example.com>"
    attachments: list = field(default_factory=list)


class _FakeIntakeOne:
    def __init__(self, mail):
        self.mail = mail
        self._sent = False

    def poll(self):
        if self._sent:
            return []
        self._sent = True
        return [self.mail]


def test_chat_email_reply_uses_email_sender():
    """Conversational reply (no workflow) → loop sends via EmailSender."""
    mail = _FakeEmail(subject="hi", message_id="<orig@x>")
    sender = _FakeEmailSender()
    chat = _FakeChatHandler(reply=ChatReply(text="hello human", used_workflow=False))
    rt = _FakeRuntime(
        email_intake=_FakeIntakeOne(mail),
        email_sender=sender,
        chat=chat,
    )
    LiveLoop(rt, clock=lambda: 0.0, sleeper=lambda _s: None)._one_cycle()
    assert len(sender.sent) == 1
    msg = sender.sent[0]
    assert msg["to"] == "alice@example.com"
    assert msg["subject"] == "Re: hi"          # Re: prepended
    assert msg["body"] == "hello human"
    assert msg["in_reply_to"] == "<orig@x>"   # threading preserved


def test_workflow_email_reply_does_not_double_send():
    """Workflow already sent the delivery email — loop MUST NOT re-send."""
    mail = _FakeEmail(subject="edit")
    sender = _FakeEmailSender()
    chat = _FakeChatHandler(
        reply=ChatReply(text="готово", used_workflow=True, job_id="j1"),
    )
    rt = _FakeRuntime(email_intake=_FakeIntakeOne(mail), email_sender=sender, chat=chat)
    LiveLoop(rt, clock=lambda: 0.0, sleeper=lambda _s: None)._one_cycle()
    assert sender.sent == []   # workflow owns the delivery


def test_email_reply_skipped_when_no_email_sender():
    """No email-out configured → silence (audit still records)."""
    mail = _FakeEmail()
    chat = _FakeChatHandler(reply=ChatReply(text="hi", used_workflow=False))
    rt = _FakeRuntime(email_intake=_FakeIntakeOne(mail), chat=chat)
    LiveLoop(rt, clock=lambda: 0.0, sleeper=lambda _s: None)._one_cycle()
    assert len(rt.audit.records) == 1   # still audited


def test_email_reply_skipped_when_text_empty():
    """Empty reply text → never invoke sender (matches Telegram behaviour)."""
    mail = _FakeEmail()
    sender = _FakeEmailSender()
    chat = _FakeChatHandler(reply=ChatReply(text="   ", used_workflow=False))
    rt = _FakeRuntime(email_intake=_FakeIntakeOne(mail), email_sender=sender, chat=chat)
    LiveLoop(rt, clock=lambda: 0.0, sleeper=lambda _s: None)._one_cycle()
    assert sender.sent == []


def test_email_reply_subject_does_not_double_prefix_re():
    """`Re: Re: hi` shouldn't happen — `Re:` stays single."""
    mail = _FakeEmail(subject="Re: previous thread")
    sender = _FakeEmailSender()
    chat = _FakeChatHandler(reply=ChatReply(text="ok", used_workflow=False))
    rt = _FakeRuntime(email_intake=_FakeIntakeOne(mail), email_sender=sender, chat=chat)
    LiveLoop(rt, clock=lambda: 0.0, sleeper=lambda _s: None)._one_cycle()
    assert sender.sent[0]["subject"] == "Re: previous thread"


def test_runtime_close_called_on_loop_exit():
    """`run_forever` must call rt.close() when the loop stops."""
    closes = []

    class _ClosableRuntime(_FakeRuntime):
        def close(self_inner):  # noqa: N805
            closes.append(True)

    rt = _ClosableRuntime(
        telegram_intake=_FakeTelegramIntake([[]]),
        telegram_sender=_FakeTelegramSender(),
    )
    counters = {"sleeps": 0}
    loop = LiveLoop(
        rt,
        clock=lambda: 0.0,
        sleeper=lambda _s: counters.update(sleeps=counters["sleeps"] + 1) or
                          (loop.stop() if counters["sleeps"] >= 1 else None),
    )
    loop.run_forever(install_signal_handlers=False)
    assert closes == [True]
