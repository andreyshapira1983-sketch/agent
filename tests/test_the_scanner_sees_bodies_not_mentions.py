"""The secret scanner judges the body of a value, not the mention of a word.

Measured 2026-08-29: `contains_secret` blocked three legitimate texts out of
seven — a document explaining who supplies access configuration, ordinary code
reading a value from the environment, and a function signature whose parameter
is merely NAMED after a credential. The agent could therefore write neither a
plan about access, nor code that works with keys, nor — the sharpest case — a
test about the scanner itself: the organ blocked its own repair.

The cure is the agent's: a real secret has a BODY — random material with no
internal structure — while a reference (an environment lookup, a type
annotation, a config call) has none. His measure is the ratio of unique
characters (>= 0.55 at length >= 40), with a separate threshold for a narrow
alphabet (hex of 32+), because sixteen symbols can never reach the general
ratio. His first measure (share of NON-alphanumeric characters) was refuted by
measurement — real keys score 0.00-0.04 there and would all have passed.

Both directions are pinned here: a short real password must still be caught,
and two deliberate decisions of the organ are guarded explicitly — the word
`token` is NOT a credential name (otherwise `max_tokens` would blind every
log), and a short hex value under a non-credential name is not a secret.
"""
from __future__ import annotations

from core.secret_scanner import contains_secret


def _is_secret(text: str) -> bool:
    """Ask exactly as the live caller (tools/file_write) asks."""
    return contains_secret(text, include_keywords=False)[0]


class TestRealSecretsAreCaught:
    def test_vendor_key_in_assignment(self):
        assert _is_secret("OPENAI_API_KEY=sk-" + "proj-abc123def456ghi789jkl012mno345")

    def test_short_password_is_still_caught(self):
        assert _is_secret("password: hunter2")

    def test_short_key_is_still_caught(self):
        assert _is_secret("API_KEY=verysecret123")

    def test_hex_body_under_credential_name(self):
        assert _is_secret(
            "secret_key = d41d8cd98f00b204e9800998ecf8427e" + "d41d8cd98f00b204"
        )


class TestMentionsAndReferencesPass:
    def test_document_about_access(self):
        assert not _is_secret("Шаг 2: оператор предоставляет api key для сервиса.")

    def test_environment_lookup(self):
        assert not _is_secret('api_key = os.environ["OPENAI' + '_API_KEY"]')

    def test_function_signature(self):
        assert not _is_secret("def connect(api_key: str, token: str) -> Client:")

    def test_config_call(self):
        assert not _is_secret("api_key = cfg.get('openai')")

    def test_long_path(self):
        assert not _is_secret("/home/user/projects/agent/core/secret_scanner.py")


class TestDeliberateBoundaries:
    """These two pin decisions the organ made on purpose."""

    def test_max_tokens_is_not_a_credential(self):
        # `token` is deliberately absent from the credential names: a model
        # parameter must not be redacted out of the logs this layer keeps
        # readable.
        assert not _is_secret("max_tokens=4096")

    def test_short_hex_under_a_plain_name(self):
        # Not a credential name, and shorter than the narrow-alphabet
        # threshold of 32 — a digest, not a secret.
        assert not _is_secret("hash=d41d8cd98f00b204")
