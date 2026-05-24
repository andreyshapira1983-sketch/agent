"""Tests for tools/builtins/email_tool.py — focus on attachments handling."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tools.builtins.email_tool import EmailTool, _MAX_ATTACHMENT_BYTES, _MAX_ATTACHMENTS


# ════════════════════════════════════════════════════════════════════
# Validation
# ════════════════════════════════════════════════════════════════════

class TestValidation:

    def test_missing_to(self):
        result = EmailTool().execute(subject="s", body="b")
        assert not result.success
        assert "'to'" in result.error

    def test_missing_subject(self):
        result = EmailTool().execute(to="a@b.com", body="b")
        assert not result.success
        assert "subject" in result.error

    def test_invalid_email(self):
        result = EmailTool().execute(to="not-an-email", subject="s", body="b")
        assert not result.success


# ════════════════════════════════════════════════════════════════════
# dry_run
# ════════════════════════════════════════════════════════════════════

class TestDryRun:

    def test_dry_run_default(self):
        result = EmailTool().execute(to="a@b.com", subject="hi", body="hello")
        assert result.success
        assert result.output["mode"] == "dry_run"
        assert result.output["attachments"] == []

    def test_dry_run_with_attachments(self, tmp_path):
        f = tmp_path / "doc.txt"
        f.write_text("hello", encoding="utf-8")
        result = EmailTool().execute(
            to="a@b.com",
            subject="hi",
            body="see attached",
            attachments=[str(f)],
        )
        assert result.success
        assert result.output["mode"] == "dry_run"
        assert len(result.output["attachments"]) == 1
        assert str(f.resolve()) == result.output["attachments"][0]


# ════════════════════════════════════════════════════════════════════
# Attachment validation
# ════════════════════════════════════════════════════════════════════

class TestAttachmentValidation:

    def test_missing_file(self, tmp_path):
        result = EmailTool().execute(
            to="a@b.com", subject="s", body="b",
            attachments=[str(tmp_path / "missing.txt")],
        )
        assert not result.success
        assert "не найдено" in result.error.lower() or "not" in result.error.lower()

    def test_path_is_dir_rejected(self, tmp_path):
        result = EmailTool().execute(
            to="a@b.com", subject="s", body="b",
            attachments=[str(tmp_path)],
        )
        assert not result.success

    def test_too_many_attachments(self, tmp_path):
        files = []
        for i in range(_MAX_ATTACHMENTS + 1):
            f = tmp_path / f"f{i}.txt"
            f.write_text("x", encoding="utf-8")
            files.append(str(f))
        result = EmailTool().execute(
            to="a@b.com", subject="s", body="b",
            attachments=files,
        )
        assert not result.success
        assert "много" in result.error.lower() or "max" in result.error.lower()

    def test_too_large_attachment(self, tmp_path, monkeypatch):
        # Don't actually allocate 20MB — patch the limit
        from tools.builtins import email_tool
        monkeypatch.setattr(email_tool, "_MAX_ATTACHMENT_BYTES", 10)
        f = tmp_path / "big.bin"
        f.write_bytes(b"0" * 100)
        result = EmailTool().execute(
            to="a@b.com", subject="s", body="b",
            attachments=[str(f)],
        )
        assert not result.success
        assert "большое" in result.error.lower() or "large" in result.error.lower()

    def test_wrong_type_for_attachments(self):
        result = EmailTool().execute(
            to="a@b.com", subject="s", body="b",
            attachments=42,  # not a list/str
        )
        assert not result.success

    def test_string_attachment_is_accepted(self, tmp_path):
        f = tmp_path / "single.txt"
        f.write_text("x", encoding="utf-8")
        result = EmailTool().execute(
            to="a@b.com", subject="s", body="b",
            attachments=str(f),  # single string, not list
        )
        assert result.success
        assert len(result.output["attachments"]) == 1


# ════════════════════════════════════════════════════════════════════
# Build attachment part
# ════════════════════════════════════════════════════════════════════

class TestAttachmentPart:

    def test_part_for_existing_text_file(self, tmp_path):
        f = tmp_path / "doc.txt"
        f.write_text("hello world", encoding="utf-8")
        part = EmailTool._build_attachment_part(f)
        assert part is not None
        # Content-Disposition has filename
        assert f.name in part["Content-Disposition"]
        # Base64-encoded payload (encoded by encoders.encode_base64)
        assert part.get_payload(decode=True) == b"hello world"

    def test_part_for_docx_uses_octet_stream(self, tmp_path):
        f = tmp_path / "doc.docx"
        f.write_bytes(b"binary content")
        part = EmailTool._build_attachment_part(f)
        assert part is not None
        # docx mimetype guess might be application/vnd.openxmlformats..., that's fine
        assert part.get_content_maintype() in {"application", "text"}


# ════════════════════════════════════════════════════════════════════
# Real-send path (mocked SMTP)
# ════════════════════════════════════════════════════════════════════

class TestRealSendMocked:

    def test_send_with_attachment(self, tmp_path, monkeypatch):
        monkeypatch.setenv("EMAIL_USERNAME", "agent@example.com")
        monkeypatch.setenv("EMAIL_PASSWORD", "test-password")
        monkeypatch.setenv("EMAIL_SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("EMAIL_SMTP_PORT", "25")

        f = tmp_path / "report.docx"
        f.write_bytes(b"X" * 100)

        sent_messages = []

        class FakeSMTP:
            def __init__(self, *_a, **_kw): pass
            def __enter__(self): return self
            def __exit__(self, *_a): pass
            def ehlo(self): pass
            def starttls(self): pass
            def login(self, *_a, **_kw): pass
            def sendmail(self, _f, _t, msg_str):
                sent_messages.append(msg_str)

        with patch("smtplib.SMTP", FakeSMTP):
            result = EmailTool().execute(
                to="client@example.com",
                subject="Edited file",
                body="See attached.",
                attachments=[str(f)],
                dry_run=False,
            )
        assert result.success
        assert result.output["mode"] == "sent"
        assert len(sent_messages) == 1
        # MIME boundary present + filename header
        assert "Content-Disposition" in sent_messages[0]
        assert "report.docx" in sent_messages[0]
