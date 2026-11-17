"""Prefixed key names, JSON/quoted forms and whole PEM blocks are seen and masked."""
from __future__ import annotations

import pytest

from core.redaction import redact_dlp_text, redact_payload, redact_text
from core.secret_scanner import contains_secret, scan

_FAKE_PEM_BODY = (
    "MIIEfakeFAKEfakeFAKEfakeFAKEfakeFAKEfakeFAKEfakeFAKEfakeFAKEfake\n"
    "Zm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFy\n"
    "c2Vjb25kbGluZW9mZmFrZWJvZHk=\n"
)
_FAKE_PEM = (
    "-----BEGIN RSA PRIVATE KEY-----\n" + _FAKE_PEM_BODY + "-----END RSA PRIVATE KEY-----"
)


@pytest.mark.parametrize(("text", "value"), [
    ("DB_PASSWORD=hunter2", "hunter2"),
    ("SERVICE_API_KEY=abc123xyz", "abc123xyz"),
    ("export MY_APP_SECRET_KEY=abc123xyz", "abc123xyz"),
    ("prod.db_password: hunter2", "hunter2"),
    ('{"password": "hunter2"}', "hunter2"),
    ('{"db_password":"hunter2"}', "hunter2"),
    ("{'api_key': 'abc123xyz'}", "abc123xyz"),
    ('password = "correct horse battery"', "battery"),
    ('{"password": "correct horse battery"}', "battery"),
])
def test_the_value_is_found_and_masked(text: str, value: str) -> None:
    assert any(f.kind == "credential-assignment" for f in scan(text)), text
    assert contains_secret(text, include_keywords=False)[0]
    redacted, findings = redact_text(text)
    assert findings and value not in redacted
    assert value not in redact_dlp_text(text)[0]
    assert value not in redact_payload({"note": text})["note"]


@pytest.mark.parametrize("text", [
    "max_tokens=4096",
    "password_hint: ask the admin",
    "api_key = os.environ['SERVICE_API_KEY']",
    "def connect(db_password: str) -> None:",
    'headers = {"api_key": api_key}',
    "if not api_key: return None",
])
def test_references_and_neighbours_stay_readable(text: str) -> None:
    assert not [f for f in scan(text) if f.kind == "credential-assignment"], text


def test_a_whole_pem_block_is_masked() -> None:
    text = f"key follows\n{_FAKE_PEM}\nend of note"
    redacted, findings = redact_text(text)
    assert [f.kind for f in findings] == ["private-key-block"]
    assert redacted == "key follows\n[REDACTED:private-key-block]\nend of note"
    for line in _FAKE_PEM_BODY.split():
        assert line not in redact_payload({"blob": text})["blob"]


def test_a_pem_block_with_escaped_newlines_is_masked() -> None:
    text = "PRIVATE_KEY_PEM=" + _FAKE_PEM.replace("\n", "\\n")
    redacted, _ = redact_text(text)
    assert "MIIEfake" not in redacted and "c2Vjb25k" not in redacted


def test_a_pem_body_without_its_end_line_is_masked() -> None:
    text = "-----BEGIN PRIVATE KEY-----\n" + _FAKE_PEM_BODY + "\nplain words after"
    redacted, _ = redact_text(text)
    assert "MIIEfake" not in redacted and "c2Vjb25k" not in redacted
    assert "plain words after" in redacted


def test_other_private_key_kinds_are_blocks_too() -> None:
    for label in ("ENCRYPTED PRIVATE KEY", "DSA PRIVATE KEY", "PGP PRIVATE KEY BLOCK"):
        text = f"-----BEGIN {label}-----\n{_FAKE_PEM_BODY}-----END {label}-----"
        assert redact_text(text)[0] == "[REDACTED:private-key-block]", label


def test_a_bare_pem_header_is_still_a_finding() -> None:
    assert [f.kind for f in scan("-----BEGIN OPENSSH PRIVATE KEY-----")] == ["private-key-block"]


def test_text_after_a_pem_block_is_not_swallowed() -> None:
    text = f"{_FAKE_PEM}\nnotes: rotate the key monthly"
    assert redact_text(text)[0].endswith("\nnotes: rotate the key monthly")
