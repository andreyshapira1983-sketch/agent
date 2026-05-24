"""Tests for brain.privacy — PIIRedactor and PIIFilter."""

from __future__ import annotations

import logging

import pytest

from brain.privacy import PIIFilter, PIIRedactor


# ────────────────────────────────────────────────────────────────────
# Redaction
# ────────────────────────────────────────────────────────────────────

class TestPIIRedactorBasics:

    def test_email_is_redacted(self):
        r = PIIRedactor()
        out = r.redact("write me at user@example.com tomorrow", session_id="s")
        assert "user@example.com" not in out
        assert "[EMAIL_1]" in out

    def test_phone_ru_is_redacted(self):
        r = PIIRedactor()
        out = r.redact("call +7 (495) 123-45-67 today", session_id="s")
        assert "495" not in out or "[PHONE" in out
        assert "[PHONE_RU_1]" in out

    def test_card_is_redacted(self):
        r = PIIRedactor()
        out = r.redact("card 4111 1111 1111 1111 expires soon", session_id="s")
        assert "4111 1111 1111 1111" not in out
        assert "[CARD_1]" in out

    def test_passport_ru_is_redacted(self):
        r = PIIRedactor()
        out = r.redact("passport 4509 123456 issued", session_id="s")
        assert "4509 123456" not in out
        assert "[PASSPORT_RU_1]" in out

    def test_ipv4_is_redacted(self):
        r = PIIRedactor()
        out = r.redact("server at 192.168.1.42 went down", session_id="s")
        assert "192.168.1.42" not in out
        assert "[IPV4_1]" in out

    def test_clean_text_is_unchanged(self):
        r = PIIRedactor()
        text = "The Brain controls the LLM; LLM is just a tool."
        assert r.redact(text, session_id="s") == text

    def test_empty_input_is_safe(self):
        r = PIIRedactor()
        assert r.redact("", session_id="s") == ""
        assert r.redact("   ", session_id="s") == "   "


class TestPIIRedactorStableMapping:

    def test_same_value_gets_same_token(self):
        r = PIIRedactor()
        out1 = r.redact("send to a@b.com", session_id="s")
        out2 = r.redact("again to a@b.com", session_id="s")
        assert out1.count("[EMAIL_1]") == 1
        assert out2.count("[EMAIL_1]") == 1

    def test_different_values_get_different_tokens(self):
        r = PIIRedactor()
        out = r.redact("a@b.com and c@d.com", session_id="s")
        assert "[EMAIL_1]" in out
        assert "[EMAIL_2]" in out

    def test_sessions_are_isolated(self):
        r = PIIRedactor()
        out1 = r.redact("a@b.com", session_id="alice")
        out2 = r.redact("c@d.com", session_id="bob")
        # Both sessions start counting at 1 independently
        assert "[EMAIL_1]" in out1
        assert "[EMAIL_1]" in out2

    def test_forget_drops_mapping(self):
        r = PIIRedactor()
        r.redact("a@b.com", session_id="s")
        assert r.stats().get("s", 0) > 0
        r.forget("s")
        assert "s" not in r.stats()


class TestPIIRedactorRestore:

    def test_restore_reverses_redact(self):
        r = PIIRedactor()
        original = "email user@example.com or call +7 495 123-45-67"
        redacted = r.redact(original, session_id="s")
        restored = r.restore(redacted, session_id="s")
        assert restored == original

    def test_restore_in_wrong_session_returns_tokens(self):
        r = PIIRedactor()
        r.redact("user@example.com", session_id="s1")
        out = r.restore("[EMAIL_1]", session_id="s2")
        # No mapping in s2 — tokens stay as-is
        assert out == "[EMAIL_1]"

    def test_restore_handles_overlapping_token_numbers(self):
        r = PIIRedactor()
        # Generate 12 emails so we get _1 through _12 and ensure _1 doesn't
        # collide with the inside of _12 during reverse substitution.
        emails = [f"u{i}@example.com" for i in range(1, 13)]
        text = " ".join(emails)
        redacted = r.redact(text, session_id="s")
        restored = r.restore(redacted, session_id="s")
        assert restored == text


class TestPIIRedactorAnonymous:

    def test_anonymous_strips_numbers(self):
        r = PIIRedactor()
        out = r.redact_anonymous("send to a@b.com and c@d.com")
        assert "a@b.com" not in out
        assert "c@d.com" not in out
        assert out.count("[EMAIL]") == 2

    def test_anonymous_does_not_persist_mapping(self):
        r = PIIRedactor()
        r.redact_anonymous("a@b.com")
        assert r.stats() == {}


# ────────────────────────────────────────────────────────────────────
# Logging filter
# ────────────────────────────────────────────────────────────────────

class TestPIIFilter:

    def _make_logger(self, name: str) -> tuple[logging.Logger, list[logging.LogRecord]]:
        log = logging.getLogger(name)
        log.handlers.clear()
        log.setLevel(logging.DEBUG)
        records: list[logging.LogRecord] = []

        class _Capture(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                records.append(record)

        log.addHandler(_Capture())
        log.addFilter(PIIFilter())
        return log, records

    def test_filter_scrubs_message(self):
        log, records = self._make_logger("test_pii_filter_msg")
        log.info("user a@b.com just registered")
        assert len(records) == 1
        msg = records[0].getMessage()
        assert "a@b.com" not in msg
        assert "[EMAIL]" in msg

    def test_filter_scrubs_args(self):
        log, records = self._make_logger("test_pii_filter_args")
        log.info("user %s registered", "a@b.com")
        msg = records[0].getMessage()
        assert "a@b.com" not in msg
        assert "[EMAIL]" in msg

    def test_filter_scrubs_phone_and_card_together(self):
        log, records = self._make_logger("test_pii_filter_multi")
        log.warning("phone +7 495 123-45-67 card 4111 1111 1111 1111")
        msg = records[0].getMessage()
        assert "495" not in msg or "[PHONE" in msg
        assert "4111" not in msg

    def test_filter_does_not_break_on_non_string_args(self):
        log, records = self._make_logger("test_pii_filter_types")
        log.info("count=%d ratio=%.2f", 42, 0.5)
        # Should pass through unchanged
        assert "42" in records[0].getMessage()
        assert "0.50" in records[0].getMessage()
