"""DataClassifier — assigns public / private / sensitive / secret."""
from __future__ import annotations

import pytest

from core.data_classifier import DataClass, classify


class TestSecretWins:
    """Secret signals beat everything else: even when the text also has PII,
    it must still be classified SECRET (the strictest class).
    """

    def test_credential_assignment_is_secret(self):
        r = classify("API_KEY=verysecret123", source="file")
        assert r.cls is DataClass.SECRET

    def test_openai_key_is_secret(self):
        r = classify("token: sk-abcdefghijklmnopqrstuvwxyz0123", source="web")
        assert r.cls is DataClass.SECRET

    def test_secret_beats_pii(self):
        text = "Email a@b.com about key sk-ant-1234567890ABCDEFGHIJKLMN"
        r = classify(text, source="file")
        assert r.cls is DataClass.SECRET


class TestSensitivePII:
    @pytest.mark.parametrize(
        "text",
        [
            "Please contact andre@example.com",
            "His SSN is 123-45-6789",
            "Reach me at +1 415 555 1234",
        ],
    )
    def test_pii_markers_yield_sensitive(self, text):
        r = classify(text, source="file")
        assert r.cls is DataClass.SENSITIVE
        assert any("PII markers" in reason for reason in r.reasons)

    def test_multiple_pii_markers_are_reported_once(self):
        r = classify("Email a@example.com and b@example.com or +1 415 555 1234")
        assert r.cls is DataClass.SENSITIVE
        assert "PII markers: ['email', 'phone']" in r.reasons


class TestSourceDefaults:
    def test_clean_web_is_public(self):
        r = classify("The capital of France is Paris.", source="web")
        assert r.cls is DataClass.PUBLIC

    def test_clean_file_is_private(self):
        r = classify("Internal project plan.", source="file")
        assert r.cls is DataClass.PRIVATE

    def test_clean_cli_is_private(self):
        r = classify("Summarise this please.", source="cli")
        assert r.cls is DataClass.PRIVATE

    def test_unknown_source_falls_back_to_private(self):
        r = classify("Just some text.", source="unknown")
        assert r.cls is DataClass.PRIVATE


class TestEdgeCases:
    def test_empty_text_falls_back_to_source_default(self):
        assert classify("", source="web").cls is DataClass.PUBLIC
        assert classify("", source="file").cls is DataClass.PRIVATE

    def test_result_carries_source_back(self):
        r = classify("hello", source="web")
        assert r.source == "web"

    def test_result_has_reasons(self):
        r = classify("API_KEY=foo123", source="file")
        assert r.reasons, "every classification should carry at least one reason"


class TestKeywordSecretsToggle:
    """Trusted-internal tool output (our own logs/files/diffs) must not be
    quarantined as SECRET just because it *mentions* a credential word.

    Regression for the trace where ``read_logs:latest`` was classified
    ``class=secret`` on the keyword ``api_key`` while the real scanner found
    ``count=0`` — a naive-substring false positive. Regex credential SHAPES
    must still classify as SECRET even with keywords disabled.
    """

    def test_bare_keyword_is_secret_by_default(self):
        text = 'log reason=["contains secret keyword api_key"]'
        assert classify(text, source="tool_output").cls is DataClass.SECRET

    def test_bare_keyword_not_secret_when_keywords_disabled(self):
        text = 'log reason=["contains secret keyword api_key"]'
        r = classify(text, source="tool_output", keyword_secrets=False)
        assert r.cls is DataClass.PRIVATE

    def test_regex_shape_still_secret_when_keywords_disabled(self):
        # A real credential value has a redactable span -> still SECRET.
        text = "token sk-abcdefghijklmnopqrstuvwxyz012345 leaked"
        assert (
            classify(text, source="tool_output", keyword_secrets=False).cls
            is DataClass.SECRET
        )

    def test_key_value_assignment_still_secret_when_keywords_disabled(self):
        # KEY=VALUE is a regex rule, not a bare keyword -> still SECRET.
        assert (
            classify("api_key=hunter2value", source="file", keyword_secrets=False).cls
            is DataClass.SECRET
        )

    def test_pii_still_wins_when_keywords_disabled(self):
        r = classify("Contact andre@example.com", source="tool_output", keyword_secrets=False)
        assert r.cls is DataClass.SENSITIVE
