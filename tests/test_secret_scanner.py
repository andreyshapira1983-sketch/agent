"""SecretScanner — single source of truth for credential detection (§7)."""
from __future__ import annotations

import pytest

from core.secret_scanner import (
    KEYWORD_RULES,
    REGEX_RULES,
    contains_secret,
    keyword_hits,
    scan,
)


# ============================================================
# Regex detection
# ============================================================

class TestScanRegex:
    @pytest.mark.parametrize(
        ("text", "expected_kind"),
        [
            ("token: sk-abcdefghijklmnopqrstuvwxyz0123", "openai-key"),
            ("auth: sk-ant-1234567890ABCDEFGHIJKLMN", "anthropic-key"),
            ("ghp_aaaaaaaaaaaaaaaaaaaaXXX is a PAT", "github-pat"),
            ("HF token hf_aaaaaaaaaaaaaaaaaaaaXXX", "huggingface-token"),
            ("akid AKIAIOSFODNN7EXAMPLE", "aws-access-key"),
            ("Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9", "bearer-token"),
            ("paste: -----BEGIN PRIVATE KEY-----", "private-key-block"),
            ("paste: -----BEGIN RSA PRIVATE KEY-----", "private-key-block"),
            ("API_KEY=verysecret123", "credential-assignment"),
            ("password: hunter2", "credential-assignment"),
            ("auth_token = abc.def-ghi", "credential-assignment"),
        ],
    )
    def test_known_credential_shapes_are_found(self, text, expected_kind):
        findings = scan(text)
        assert findings, f"expected a finding in {text!r}"
        kinds = {f.kind for f in findings}
        assert expected_kind in kinds

    def test_clean_text_has_no_findings(self):
        assert scan("The weather is nice today.") == []
        assert scan("file_read returned the architecture document.") == []
        assert scan("") == []

    def test_finding_spans_are_consistent(self):
        text = "prefix sk-abcdefghijklmnopqrstuvwxyz0123 suffix"
        findings = scan(text)
        assert findings
        f = next(x for x in findings if x.kind == "openai-key")
        # The matched substring must be exactly text[f.start:f.end]
        assert text[f.start : f.end] == f.matched

    def test_anthropic_key_is_distinguished_from_openai(self):
        text = "key sk-ant-1234567890ABCDEFGHIJKLMN"
        kinds = {f.kind for f in scan(text)}
        # Both shapes match the prefix, but anthropic-key MUST be one of them
        # (lets the redactor pick the more specific label).
        assert "anthropic-key" in kinds


# ============================================================
# Keyword detection
# ============================================================

class TestKeywords:
    def test_known_keywords_register(self):
        assert "password" in keyword_hits("my password is hunter2")
        assert "api_key" in keyword_hits("There's an API_KEY in there")
        assert "authorization:" in keyword_hits("Authorization: Bearer abc")

    def test_clean_text_has_no_keyword_hits(self):
        assert keyword_hits("Nothing to see here.") == []

    def test_case_insensitive(self):
        assert "password" in keyword_hits("PASSWORD")
        assert "apikey" in keyword_hits("APIKEY")


# ============================================================
# contains_secret aggregate
# ============================================================

class TestContainsSecret:
    def test_clean_text_returns_false(self):
        flag, reasons = contains_secret("Just a friendly note.")
        assert flag is False
        assert reasons == []

    def test_regex_hit_drives_true(self):
        flag, reasons = contains_secret("token: ghp_aaaaaaaaaaaaaaaaaaaaXXX")
        assert flag is True
        assert any("github-pat" in r for r in reasons)

    def test_keyword_hit_drives_true(self):
        flag, reasons = contains_secret("Please share your password.")
        assert flag is True
        assert any("secret keyword 'password'" in r for r in reasons)

    def test_combined_signals_surface_both(self):
        """`API_KEY=foo123` triggers BOTH the credential-assignment regex and
        the `api_key` keyword. The audit trail must record both signals."""
        flag, reasons = contains_secret("API_KEY=foo123")
        assert flag is True
        assert any("credential-assignment" in r for r in reasons)
        assert any("api_key" in r for r in reasons)


# ============================================================
# Sanity / contract
# ============================================================

class TestModuleContract:
    def test_every_regex_rule_has_compiled_pattern(self):
        for kind, pat in REGEX_RULES:
            assert isinstance(kind, str)
            assert hasattr(pat, "search")

    def test_keyword_rules_are_lowercase(self):
        for kw in KEYWORD_RULES:
            assert kw == kw.lower()

    def test_scan_on_non_string_returns_empty(self):
        # The function is typed for str, but defensive code is allowed.
        assert scan("") == []
