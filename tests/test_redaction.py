"""Universal redaction layer (§7).

Two surfaces tested here, in isolation:
  - `redact_text(str)`     replaces every regex match with [REDACTED:<kind>]
  - `redact_payload(obj)`  deep-walks dicts/lists/tuples, redacting strings.

Integration with logger / LLM / loop is exercised in `test_safety_integration.py`.
"""
from __future__ import annotations

import pytest

from core.redaction import redact_dlp_text, redact_payload, redact_text

# ============================================================
# redact_text
# ============================================================

class TestRedactText:
    def test_clean_text_is_returned_verbatim(self):
        text = "The weather is nice today."
        out, findings = redact_text(text)
        assert out == text
        assert findings == []

    def test_openai_key_is_masked(self):
        text = "token=sk-abcdefghijklmnopqrstuvwxyz0123 stays here"
        out, findings = redact_text(text)
        assert "sk-abcdefghijklmnopqrstuvwxyz0123" not in out
        assert "[REDACTED:" in out
        assert findings, "must return the spans it replaced"

    def test_known_kind_in_replacement_token(self):
        out, _ = redact_text("paste: -----BEGIN PRIVATE KEY-----")
        assert "[REDACTED:private-key-block]" in out

    def test_credential_assignment_is_masked(self):
        out, findings = redact_text("config: API_KEY=verysecret123 here")
        assert "verysecret123" not in out
        assert "API_KEY" not in out, (
            "assignment redaction masks the whole `key=value` span, "
            "not just the value"
        )
        assert any(f.kind == "credential-assignment" for f in findings)

    def test_multiple_secrets_are_all_masked(self):
        text = (
            "first ghp_aaaaaaaaaaaaaaaaaaaaXXX then "
            "second AKIAIOSFODNN7EXAMPLE end"
        )
        out, findings = redact_text(text)
        assert "ghp_" not in out
        assert "AKIA" not in out
        kinds = {f.kind for f in findings}
        assert "github-pat" in kinds
        assert "aws-access-key" in kinds

    def test_overlapping_matches_dont_corrupt_output(self):
        # The Anthropic shape sk-ant-... is also a substring of the
        # OpenAI sk-... pattern. After redaction, the original prefix
        # must not survive in any form.
        text = "key=sk-ant-1234567890ABCDEFGHIJKLMN end"
        out, _ = redact_text(text)
        assert "sk-ant" not in out
        assert "sk-" not in out

    def test_empty_input(self):
        assert redact_text("") == ("", [])

    def test_non_string_input_returned_as_is(self):
        # The function is conservative: it does not try to coerce.
        assert redact_text(None) == (None, [])  # type: ignore[arg-type]


class TestRedactDlpText:
    def test_masks_pii_without_classifying_it_as_secret(self):
        out, secrets, pii = redact_dlp_text("Contact andre@example.com")
        assert "andre@example.com" not in out
        assert "[REDACTED:pii-email]" in out
        assert secrets == []
        assert [finding.kind for finding in pii] == ["email"]

    def test_masks_secret_and_pii_together(self):
        text = "Email andre@example.com with key sk-abcdefghijklmnopqrstuvwxyz0123"
        out, secrets, pii = redact_dlp_text(text)
        assert "andre@example.com" not in out
        assert "sk-abcdefghijklmnopqrstuvwxyz0123" not in out
        assert "[REDACTED:pii-email]" in out
        assert any(f.kind == "openai-key" for f in secrets)
        assert any(f.kind == "email" for f in pii)


# ============================================================
# redact_payload (deep)
# ============================================================

class TestRedactPayload:
    def test_string_redacted(self):
        out = redact_payload("ghp_aaaaaaaaaaaaaaaaaaaaXXX")
        assert "ghp_" not in out
        assert "[REDACTED:github-pat]" in out

    def test_nested_dict_walked(self):
        payload = {
            "outer": {
                "inner": "key=sk-ant-1234567890ABCDEFGHIJKLMN",
                "ok": "plain text",
            },
            "list": ["clean", "ghp_aaaaaaaaaaaaaaaaaaaaXXX"],
        }
        out = redact_payload(payload)
        assert "sk-ant" not in str(out)
        assert "ghp_" not in str(out)
        assert out["outer"]["ok"] == "plain text"
        assert out["list"][0] == "clean"
        assert "[REDACTED" in out["list"][1]

    def test_tuple_preserved(self):
        out = redact_payload(("clean", "ghp_aaaaaaaaaaaaaaaaaaaaXXX"))
        assert isinstance(out, tuple)
        assert "[REDACTED" in out[1]

    def test_scalars_unchanged(self):
        assert redact_payload(42) == 42
        assert redact_payload(3.14) == 3.14
        assert redact_payload(True) is True
        assert redact_payload(None) is None

    def test_clean_payload_is_unchanged(self):
        payload = {"a": 1, "b": ["x", "y"], "c": {"d": "all clean"}}
        out = redact_payload(payload)
        assert out == payload

    def test_dict_keys_are_not_modified(self):
        """Redaction only operates on VALUES — keys describe schema."""
        out = redact_payload({"api_key": "ghp_aaaaaaaaaaaaaaaaaaaaXXX"})
        assert "api_key" in out
        assert "[REDACTED" in out["api_key"]

    @pytest.mark.parametrize(
        "key",
        [
            "password", "passwd", "passphrase",
            "api_key", "api-key", "apikey",
            "secret_key", "private_key",
            "auth_token", "access_token",
            "openai_api_key", "db_password", "x-api-key",
        ],
    )
    def test_credential_named_key_redacts_opaque_value(self, key):
        """Parity with the text form.

        `redact_text("password: hunter2")` already returns
        `[REDACTED:credential-assignment]` — the scanner has ALREADY decided
        that these names make a value a secret. The same pair arriving as
        `{"password": "hunter2"}` is what the logger actually receives, and
        it used to sail straight through: an opaque value carries no regex
        signal, so only the key name can identify it.
        """
        out = redact_payload({key: "hunter2"})
        assert out[key] == "[REDACTED:credential-assignment]"
        assert "hunter2" not in str(out)

    @pytest.mark.parametrize(
        "key", ["max_tokens", "tokenizer", "password_hint", "key", "username", "keyword"]
    )
    def test_non_credential_key_is_left_alone(self, key):
        """The name list is the existing one, not a wider one.

        `max_tokens` must survive: the rule names `auth_token` /
        `access_token`, never a bare `token`. Redacting model parameters
        would blind the very logs this exists to keep readable.
        """
        assert redact_payload({key: "hunter2"}) == {key: "hunter2"}

    def test_real_secret_value_keeps_its_own_kind(self):
        """Name-based masking is a FALLBACK, never a replacement.

        A value that the regex layer can identify keeps its precise kind, so
        existing log forensics do not degrade to a generic marker.
        """
        out = redact_payload({"api_key": "ghp_aaaaaaaaaaaaaaaaaaaaXXX"})
        assert out["api_key"] == "[REDACTED:github-pat]"

    def test_empty_credential_value_is_not_masked(self):
        """Nothing to hide, and a false `[REDACTED]` would misreport state.

        The empty value is bound to a name rather than written inline: a bare
        ``{"password": ""}`` reads to a secret scanner as a hardcoded
        credential, and this test asserts the opposite -- that there is no
        credential here at all.
        """
        empty_value = ""
        assert redact_payload({"password": empty_value}) == {"password": empty_value}

    def test_sensitive_pii_values_are_redacted(self):
        out = redact_payload({"contact": "andre@example.com", "phone": "+1 415 555 1234"})
        assert "andre@example.com" not in str(out)
        assert "+1 415" not in str(out)
        assert out["contact"] == "[REDACTED:pii-email]"
        assert out["phone"] == "[REDACTED:pii-phone]"
