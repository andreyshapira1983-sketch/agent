"""Regression: the openai-key / anthropic-key patterns must not match inside
hyphenated words (CORE-13).

The `openai-key` rule was ``sk-[A-Za-z0-9_\\-]{20,}`` with a hyphen in the class
and no left word boundary, so "sk-" matched INSIDE common words — "ta**sk-**...",
"ri**sk-**...", "di**sk-**..." — followed by a hyphenated slug of 20+ chars.
Observed live: benign Russian ChatGPT web_search results classified
``class=secret, kinds=['openai-key']`` and redacted.
"""
from __future__ import annotations

from core.secret_scanner import contains_secret, scan

BENIGN = [
    "task-oriented-multi-agent-systems",
    "risk-management-and-mitigation-plan",
    "a disk-based-storage-engine-layer",
]


def test_hyphenated_words_are_not_flagged_as_keys():
    for text in BENIGN:
        found, reasons = contains_secret(text, include_keywords=False)
        assert not found, f"benign text flagged as secret: {text!r} -> {reasons}"
        assert not any(f.kind in ("openai-key", "anthropic-key") for f in scan(text))


def test_real_openai_key_still_detected():
    found, reasons = contains_secret(
        "OPENAI_API_KEY=sk-proj-abc123XYZ456def789ghi012jkl345mno",
        include_keywords=False,
    )
    assert found and any("openai-key" in r for r in reasons)


def test_real_anthropic_key_still_detected():
    found, reasons = contains_secret(
        "auth header: sk-ant-abc123XYZ456def789ghi012jkl345mnop",
        include_keywords=False,
    )
    assert found and any("anthropic-key" in r for r in reasons)
