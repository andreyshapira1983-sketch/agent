"""Escaped JSON, glued key names and END-less armored keys are masked; boolean settings are not."""
from __future__ import annotations

import re
import time

import pytest

from core.redaction import redact_payload, redact_text
from core.secret_scanner import (
    _PEM_OPEN_BODY,
    REGEX_RULES,
    contains_secret,
    is_credential_key,
    scan,
)

_B64 = (
    "MIIEfakeFAKEfakeFAKEfakeFAKEfakeFAKEfakeFAKEfakeFAKEfakeFAKEfake",
    "Zm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFy",
    "=fAkE",
)


def _kinds(text: str) -> list[str]:
    return [f.kind for f in scan(text)]


@pytest.mark.parametrize(("text", "value"), [
    ('{\\"password\\": \\"hunter2\\"}', "hunter2"),
    ('body={\\"db_password\\":\\"hunter2\\"}', "hunter2"),
    ("{\\'api_key\\': \\'abc123xyz\\'}", "abc123xyz"),
    ('"{\\"password\\": \\"correct horse battery\\"}"', "battery"),
    ("dbPassword=hunter2", "hunter2"),
    ("DBPASSWORD=hunter2", "hunter2"),
    ("openaiApiKey: abc123xyz", "abc123xyz"),
    ('{"userPassword": "hunter2"}', "hunter2"),
    ("export MYSECRETKEY=abc123xyz", "abc123xyz"),
])
def test_the_value_is_found_and_masked(text: str, value: str) -> None:
    assert "credential-assignment" in _kinds(text), text
    assert contains_secret(text, include_keywords=False)[0]
    assert value not in redact_text(text)[0]
    assert value not in redact_payload({"note": text})["note"]


@pytest.mark.parametrize("key", ["dbPassword", "DBPASSWORD", "openaiApiKey", "userAuthToken"])
def test_a_glued_key_names_a_credential_in_a_mapping(key: str) -> None:
    assert is_credential_key(key)
    assert redact_payload({key: "hunter2"})[key] != "hunter2"


@pytest.mark.parametrize("key", ["max_tokens", "password_hint", "passwordless", "api_keys_count"])
def test_a_neighbour_key_is_still_not_a_credential(key: str) -> None:
    assert not is_credential_key(key)


@pytest.mark.parametrize("text", [
    "require_password: yes",
    "require_password: no",
    "use_api_key=1",
    "USE_API_KEY=0",
    "requirePassword: on",
    "allow_empty_password = off",
    "store_password: true",
    '{"require_password": "yes"}',
    '{\\"api_key\\": api_key}',
    "passwordless: abc123xyz",
])
def test_a_boolean_setting_is_not_a_secret(text: str) -> None:
    assert "credential-assignment" not in _kinds(text), text


@pytest.mark.parametrize("text", ["password: yes", "password=1", "DB_PASSWORD=10", "db_password: yesterday"])
def test_only_a_prefixed_name_with_a_flag_value_is_let_through(text: str) -> None:
    assert "credential-assignment" in _kinds(text), text


def _armored(*, end: bool, nl: str = "\n") -> str:
    lines = [
        "-----BEGIN PGP PRIVATE KEY BLOCK-----",
        "Version: GnuPG v2",
        "Comment: fake key for a test",
        "",
        *_B64,
    ]
    if end:
        lines.append("-----END PGP PRIVATE KEY BLOCK-----")
    return nl.join(lines)


@pytest.mark.parametrize("nl", ["\n", "\r\n", "\\n"])
def test_an_armored_key_without_its_end_line_is_masked_whole(nl: str) -> None:
    text = "key:" + nl + _armored(end=False, nl=nl) + nl + nl + "plain words after"
    redacted, findings = redact_text(text)
    assert "private-key-block" in [f.kind for f in findings]
    for part in (*_B64, "GnuPG", "fake key"):
        assert part not in redacted, part
    assert "plain words after" in redacted


def test_an_encrypted_pem_without_its_end_line_is_masked_whole() -> None:
    text = "\n".join([
        "-----BEGIN RSA PRIVATE KEY-----",
        "Proc-Type: 4,ENCRYPTED",
        "DEK-Info: AES-128-CBC,0123456789ABCDEF0123456789ABCDEF",
        "",
        *_B64[:2],
    ])
    redacted, _ = redact_text(text + "\nplain words after")
    assert redacted == "[REDACTED:private-key-block]\nplain words after"


def test_a_complete_armored_block_is_one_finding() -> None:
    assert redact_text(_armored(end=True))[0] == "[REDACTED:private-key-block]"


def test_prose_after_the_body_is_not_taken_for_a_header() -> None:
    text = "-----BEGIN PRIVATE KEY-----\n" + "\n".join(_B64[:2]) + "\nnote: rotate monthly"
    assert redact_text(text)[0].endswith("\nnote: rotate monthly")


def _seconds(pattern, text: str) -> float:
    best = float("inf")
    for _ in range(3):
        start = time.perf_counter()
        for _match in pattern.finditer(text):
            pass
        best = min(best, time.perf_counter() - start)
    return best


def _assert_linear(pattern, head: str, unit: str) -> None:
    small = _seconds(pattern, head + unit * 5000)
    large = _seconds(pattern, head + unit * 20000)
    # Linear growth is x4; quadratic would be x16. The floor absorbs timer noise.
    assert large < max(small * 9, 0.05), (unit, small, large)


@pytest.mark.parametrize("unit", [
    "password\\",
    "dbpassword ",
    "apikeyapikey",
    '\\"password\\": \\"aaaa',
    'password: \\"a',
    "password='a\\",
])
def test_the_assignment_rule_costs_linear_on_hostile_text(unit: str) -> None:
    _assert_linear(dict(REGEX_RULES)["credential-assignment"], "", unit)


@pytest.mark.parametrize("unit", ["\n", "\nA: b", "\nVersion", "\nAAAA ", "\n \t", "\\n", "\nA: b\\"])
def test_the_end_less_key_body_costs_linear_on_hostile_text(unit: str) -> None:
    """The END-less branch alone: the BEGIN…END branch is bounded by its own 16000 cap."""
    head = "-----BEGIN PRIVATE KEY-----"
    _assert_linear(re.compile(head + _PEM_OPEN_BODY), head, unit)
