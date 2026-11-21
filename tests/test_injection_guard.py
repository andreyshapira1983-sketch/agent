"""Tests for core/injection_guard.py — indirect prompt injection defence.

Test categories:
  Clean content    — no patterns, verdict=clean
  Override         — "ignore previous instructions" etc.
  Role switch      — "you are now", "act as", DAN/STAN
  Exfiltration     — "send above to https://"
  Delimiter        — <|im_end|>, [INST], </s>, ### System
  Aggregate        — worst-verdict wins when multiple categories found
  Edge cases       — empty string, case-insensitivity, annotate_suspicious
"""
from __future__ import annotations

from core.injection_guard import (
    prepare_untrusted_text_for_llm,
    scan_for_injection,
)

# ---------------------------------------------------------------------------
# Metadata and helpers
# ---------------------------------------------------------------------------

class TestScanResultMetadata:
    def test_is_clean_property(self):
        assert scan_for_injection("safe text").is_clean


# ---------------------------------------------------------------------------
# _TRUSTED_INTERNAL_TOOLS — exemption from injection scanning in the loop
# ---------------------------------------------------------------------------


class TestPrepareUntrustedTextForLlm:
    def test_clean_passthrough(self):
        text, inj = prepare_untrusted_text_for_llm("hello world", source_label="test")
        assert text == "hello world"
        assert inj.is_clean
