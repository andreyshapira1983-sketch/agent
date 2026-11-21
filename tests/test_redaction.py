"""Universal redaction layer (§7).

Two surfaces tested here, in isolation:
  - `redact_text(str)`     replaces every regex match with [REDACTED:<kind>]
  - `redact_payload(obj)`  deep-walks dicts/lists/tuples, redacting strings.

Integration with logger / LLM / loop is exercised in `test_safety_integration.py`.
"""
from __future__ import annotations

import pytest

from core.redaction import redact_payload, redact_text

# ============================================================
# redact_text
# ============================================================

class TestRedactText:
    def test_overlapping_matches_dont_corrupt_output(self):
        # The Anthropic shape sk-ant-... is also a substring of the
        # OpenAI sk-... pattern. After redaction, the original prefix
        # must not survive in any form.
        text = "key=sk-ant-1234567890ABCDEFGHIJKLMN end"
        out, _ = redact_text(text)
        assert "sk-ant" not in out
        assert "sk-" not in out


# ============================================================
# redact_payload (deep)
# ============================================================

class TestRedactPayload:
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
