"""Tests for runtime.email_sender.EmailSender — outbound SMTP twin."""

from __future__ import annotations

import smtplib

import pytest

from brain.secrets import SecretsVault
from runtime.email_sender import EmailSender


class _FakeSMTP:
    """Context-managed stand-in for smtplib.SMTP."""

    instances: list = []

    def __init__(self, host, port, timeout=None):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.ehlos = 0
        self.starttls_called = False
        self.login_args = None
        self.sent: list = []
        _FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def ehlo(self):
        self.ehlos += 1

    def starttls(self):
        self.starttls_called = True

    def login(self, user, pwd):
        self.login_args = (user, pwd)

    def send_message(self, msg):
        self.sent.append(msg)


def _vault_with_creds() -> SecretsVault:
    v = SecretsVault()
    v.set("EMAIL_USERNAME", "anya@example.com")
    v.set("EMAIL_PASSWORD", "app password")  # space stripped by sender
    return v


def setup_function(_):
    _FakeSMTP.instances.clear()


# ────────────────────────────────────────────────────────────────────


def test_send_text_returns_false_when_recipient_or_body_empty():
    sender = EmailSender(vault=_vault_with_creds(), smtp_factory=_FakeSMTP)
    assert sender.send_text(to="", subject="hi", body="hello") is False
    assert sender.send_text(to="bob@x.com", subject="hi", body="   ") is False
    assert _FakeSMTP.instances == []


def test_send_text_returns_false_when_credentials_missing():
    sender = EmailSender(vault=SecretsVault(), smtp_factory=_FakeSMTP)
    assert sender.send_text(to="bob@x.com", subject="hi", body="hi") is False
    assert _FakeSMTP.instances == []


def test_send_text_happy_path_sends_login_and_message():
    sender = EmailSender(vault=_vault_with_creds(), smtp_factory=_FakeSMTP)
    ok = sender.send_text(to="bob@x.com", subject="Re: edit", body="done")
    assert ok is True
    assert len(_FakeSMTP.instances) == 1
    inst = _FakeSMTP.instances[0]
    assert inst.starttls_called
    assert inst.login_args == ("anya@example.com", "apppassword")
    assert len(inst.sent) == 1
    msg = inst.sent[0]
    assert msg["From"] == "anya@example.com"
    assert msg["To"] == "bob@x.com"
    assert msg["Subject"] == "Re: edit"


def test_send_text_threads_with_in_reply_to():
    sender = EmailSender(vault=_vault_with_creds(), smtp_factory=_FakeSMTP)
    sender.send_text(
        to="bob@x.com", subject="Re: x", body="ok",
        in_reply_to="<orig-id@example.com>",
    )
    msg = _FakeSMTP.instances[0].sent[0]
    assert msg["In-Reply-To"] == "<orig-id@example.com>"
    assert msg["References"] == "<orig-id@example.com>"


def test_send_text_uses_safe_fallback_subject_when_empty():
    sender = EmailSender(vault=_vault_with_creds(), smtp_factory=_FakeSMTP)
    sender.send_text(to="bob@x.com", subject="   ", body="ok")
    msg = _FakeSMTP.instances[0].sent[0]
    assert msg["Subject"] == "Re:"


def test_dry_run_does_not_touch_network():
    sender = EmailSender(
        vault=_vault_with_creds(), smtp_factory=_FakeSMTP, dry_run=True,
    )
    assert sender.send_text(to="bob@x.com", subject="x", body="hi") is True
    assert _FakeSMTP.instances == []


def test_smtp_error_returns_false_and_logs(caplog):
    class _BoomSMTP:
        def __init__(self, *_a, **_kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def ehlo(self): pass
        def starttls(self): pass
        def login(self, *_a):
            raise smtplib.SMTPException("server hates us")
        def send_message(self, *_a): raise AssertionError("should not reach")

    sender = EmailSender(vault=_vault_with_creds(), smtp_factory=_BoomSMTP)
    with caplog.at_level("WARNING"):
        ok = sender.send_text(to="bob@x.com", subject="x", body="hi")
    assert ok is False
    assert any("SMTP error" in r.message for r in caplog.records)


def test_auth_error_returns_false():
    class _AuthBomb:
        def __init__(self, *_a, **_kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def ehlo(self): pass
        def starttls(self): pass
        def login(self, *_a):
            raise smtplib.SMTPAuthenticationError(535, b"bad password")
        def send_message(self, *_a): pass

    sender = EmailSender(vault=_vault_with_creds(), smtp_factory=_AuthBomb)
    assert sender.send_text(to="bob@x.com", subject="x", body="hi") is False


def test_network_error_returns_false():
    class _Disconnected:
        def __init__(self, *_a, **_kw):
            raise OSError("nameserver down")
    sender = EmailSender(vault=_vault_with_creds(), smtp_factory=_Disconnected)
    assert sender.send_text(to="bob@x.com", subject="x", body="hi") is False
