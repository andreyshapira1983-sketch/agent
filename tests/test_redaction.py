"""Universal redaction layer (§7).

Two surfaces tested here, in isolation:
  - `redact_text(str)`     replaces every regex match with [REDACTED:<kind>]
  - `redact_payload(obj)`  deep-walks dicts/lists/tuples, redacting strings.

Integration with logger / LLM / loop is exercised in `test_safety_integration.py`.
"""
from __future__ import annotations

from core.redaction import redact_payload, redact_text


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
