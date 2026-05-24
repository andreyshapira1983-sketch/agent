"""Tests for runtime.telegram_sender.TelegramSender."""

from __future__ import annotations

from brain.secrets import SecretsVault
from runtime.telegram_sender import TelegramSender, _chunk_text


def _vault_with(**kv) -> SecretsVault:
    v = SecretsVault()
    for k, val in kv.items():
        v.set(k, val)
    return v


def test_send_text_calls_send_message_api():
    calls = []

    def fake_http(token, method, params, *, timeout):
        calls.append({"token": token, "method": method, "params": dict(params)})
        return {"ok": True, "result": {"message_id": 42}}

    sender = TelegramSender(vault=_vault_with(TELEGRAM_BOT_TOKEN="t"), http=fake_http)
    assert sender.send_text(123, "hello") is True
    assert len(calls) == 1
    assert calls[0]["method"] == "sendMessage"
    assert calls[0]["params"]["chat_id"] == "123"
    assert calls[0]["params"]["text"] == "hello"


def test_send_text_returns_false_when_token_missing():
    sender = TelegramSender(vault=SecretsVault(), http=lambda *a, **k: {"ok": True})
    assert sender.send_text(123, "x") is False


def test_send_text_returns_false_when_api_returns_not_ok():
    def fake_http(*_a, **_k):
        return {"ok": False, "description": "bot blocked by user"}
    sender = TelegramSender(vault=_vault_with(TELEGRAM_BOT_TOKEN="t"), http=fake_http)
    assert sender.send_text(1, "hi") is False


def test_send_text_does_not_crash_on_network_exception(caplog):
    def fake_http(*_a, **_k):
        raise RuntimeError("network is down")
    sender = TelegramSender(vault=_vault_with(TELEGRAM_BOT_TOKEN="t"), http=fake_http)
    assert sender.send_text(1, "hi") is False  # graceful, not a crash


def test_send_text_with_empty_message_is_silent_noop():
    """Empty text → no API call → False. Silence is intentional."""
    calls = []
    def fake_http(*_a, **_k):
        calls.append("called")
        return {"ok": True}
    sender = TelegramSender(vault=_vault_with(TELEGRAM_BOT_TOKEN="t"), http=fake_http)
    assert sender.send_text(1, "   ") is False
    assert sender.send_text(1, "") is False
    assert calls == []  # never hit the wire


# ════════════════════════════════════════════════════════════════════
# Long-message splitting
# ════════════════════════════════════════════════════════════════════

def test_chunk_text_no_split_for_short_message():
    assert _chunk_text("hello", 100) == ["hello"]


def test_chunk_text_splits_long_message_on_whitespace():
    text = ("word " * 5000).strip()
    chunks = _chunk_text(text, 3500)
    assert len(chunks) >= 2
    # No chunk exceeds the cap (allow small slack for newline trims)
    assert all(len(c) <= 3500 for c in chunks)
    # Original text is preserved (modulo whitespace at boundaries)
    rebuilt = " ".join(chunks).replace("  ", " ")
    assert rebuilt.startswith("word word word")


def test_send_text_splits_into_multiple_api_calls_when_long():
    calls = []
    def fake_http(_t, _m, params, *, timeout):
        calls.append(params["text"])
        return {"ok": True}
    sender = TelegramSender(vault=_vault_with(TELEGRAM_BOT_TOKEN="t"), http=fake_http)
    huge = "x " * 4000
    assert sender.send_text(1, huge)
    assert len(calls) >= 2
