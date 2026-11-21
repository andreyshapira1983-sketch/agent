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

class TestTrustedInternalTools:
    """Verify the exemption set is defined and contains the right members."""

    def test_internal_file_tools_in_set(self):
        from core.loop import _TRUSTED_INTERNAL_TOOLS
        for tool in ("file_read", "list_dir", "diff_file", "run_tests", "read_logs"):
            assert tool in _TRUSTED_INTERNAL_TOOLS, f"{tool!r} should be trusted"

    def test_external_tools_not_in_set(self):
        from core.loop import _TRUSTED_INTERNAL_TOOLS
        for tool in ("web_search", "web_fetch", "rss_fetch",
                     "semantic_scholar_search", "shell_exec"):
            assert tool not in _TRUSTED_INTERNAL_TOOLS, (
                f"{tool!r} must NOT be trusted — its content is external"
            )


class TestPrepareUntrustedTextForLlm:
    def test_clean_passthrough(self):
        text, inj = prepare_untrusted_text_for_llm("hello world", source_label="test")
        assert text == "hello world"
        assert inj.is_clean
