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
