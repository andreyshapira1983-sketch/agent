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


# Sample credentials are assembled from a prefix plus a body instead of being
# written as one literal. These shapes are real enough that GitHub's own push
# protection rejected the first version of this file (Slack token, Slack
# webhook, GitLab token) — which independently confirms the formats below are
# worth detecting. Splitting the prefix keeps the fixtures readable without
# storing anything that reads as a live credential.
_FINE_PAT_BODY = "_11ABCDEFG0aBcDeFgHiJ_kLmNoPqRsTuVwXyZ1234567890aBcDeFgHiJkLmNo"
_GH_BODY = "_aB3dE5gH7jK9mN1pQ3sT5vW7yZ9bD1fH3jL5"
_SLACK_BODY = "-123456789012-1234567890123-aBcDeFgHiJkLmNoPqRsTuVwX"
_SLACK_HOOK_HOST = "https://hooks.slack.com/"
_SLACK_HOOK_PATH = "services/T00000000/B00000000/XXXXXXXXXXXXXXXXXXXXXXXX"
_GOOGLE_BODY = "SyA1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q"
_GITLAB_BODY = "-aB3dE5gH7jK9mN1pQ3sT"
_NPM_BODY = "_aB3dE5gH7jK9mN1pQ3sT5vW7yZ9bD1fH3jL5"
_STRIPE_BODY = "_live_aB3dE5gH7jK9mN1pQ3sT5vW7"


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
        # Explicit ids so a failing case is named by its KIND in the node id,
        # not by its parameter value — pytest would otherwise echo the
        # secret-shaped string into CI logs and bug reports (PR #207 review).
        ids=[
            "openai", "anthropic", "github-pat", "huggingface", "aws",
            "bearer", "pem", "pem-rsa", "assign-key", "assign-password",
            "assign-token",
        ],
    )
    def test_known_credential_shapes_are_found(self, text, expected_kind):
        findings = scan(text)
        assert findings, f"expected a finding in {text!r}"
        kinds = {f.kind for f in findings}
        assert expected_kind in kinds

    @pytest.mark.parametrize(
        ("text", "expected_kind"),
        [
            # GitHub issues five more token shapes besides the classic `ghp_`
            # PAT, and this repo drives `gh` — so any of them can end up in
            # captured output. Fine-grained PATs carry `_` inside the body.
            ("github_pat" + _FINE_PAT_BODY, "github-fine-grained-pat"),
            ("gho" + _GH_BODY, "github-token"),
            ("ghs" + _GH_BODY, "github-token"),
            ("ghu" + _GH_BODY, "github-token"),
            ("ghr" + _GH_BODY, "github-token"),
            ("xoxb" + _SLACK_BODY, "slack-token"),
            ("xoxp" + _SLACK_BODY, "slack-token"),
            (_SLACK_HOOK_HOST + _SLACK_HOOK_PATH, "slack-webhook"),
            ("AIza" + _GOOGLE_BODY, "google-api-key"),
            ("glpat" + _GITLAB_BODY, "gitlab-pat"),
            ("npm" + _NPM_BODY, "npm-token"),
            ("sk" + _STRIPE_BODY, "stripe-key"),
            ("rk" + _STRIPE_BODY, "stripe-key"),
            # Stateless installation token: `ghs_<app id>_<JWT>` — dots and
            # underscores are part of the credential body.
            ("ghs_123456_eyJhbGciOiJSUzI1NiJ9.eyJpc3MiOjF9.sig-part_x", "github-token"),
        ],
        ids=[
            "fine-grained-pat", "gho", "ghs", "ghu", "ghr", "xoxb", "xoxp",
            "slack-webhook", "google", "gitlab", "npm", "stripe-sk",
            "stripe-rk", "ghs-stateless",
        ],
    )
    def test_modern_token_formats_are_found(self, text, expected_kind):
        # Measured against the live scanner before this rule set existed: all
        # of these reached both logging surfaces verbatim (`TraceLogger` writes
        # the JSONL line and prints to stderr), which is exactly what CodeQL
        # flags on `core/logger.py`. The underscore prefixes matter: `sk_live_`
        # is not caught by the `sk-` rule because that one requires a hyphen.
        findings = scan(text)
        assert findings, f"expected a finding in {text!r}"
        assert expected_kind in {f.kind for f in findings}

    @pytest.mark.parametrize(
        "text",
        [
            # Publishable Stripe keys are public BY DESIGN — they live in
            # frontend configs and build manifests. Flagging one feeds
            # `contains_secret()` and the memory write policy, quarantining
            # innocent content (PR #207 review, Greptile P1).
            "pk" + _STRIPE_BODY,
            # `kk_` was never a Stripe prefix; it fell out of the original
            # `[sprk]k_` character class by accident.
            "kk" + _STRIPE_BODY,
            # Left boundaries: a match must not start inside a glued token.
            "abchttps://hooks.slack.com/" + _SLACK_HOOK_PATH,
            "xghp_aaaaaaaaaaaaaaaaaaaaXXX",
            "shhf_aaaaaaaaaaaaaaaaaaaaXXX",
            # A Google-key-shaped head on a LONGER same-class string is some
            # other identifier, not a 39-char key.
            "AIza" + _GOOGLE_BODY + "0extralength",
        ],
        ids=[
            "stripe-publishable", "stripe-kk", "glued-slack-webhook",
            "glued-ghp", "glued-hf", "overlong-google",
        ],
    )
    def test_lookalikes_are_not_flagged(self, text):
        """The false-positive half of the #207 review debt.

        Every regex hit reaches `contains_secret()` and the memory write
        policy, so a lookalike costs real content its place in memory — the
        cost of a false positive here is not cosmetic.
        """
        assert scan(text) == [], f"lookalike wrongly flagged: {text[:24]}..."

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

    def test_include_keywords_false_drops_bare_keyword(self):
        """With the soft keyword layer disabled, a bare mention of a
        credential word is NOT a secret (no redactable span)."""
        flag, reasons = contains_secret(
            "log: contains secret keyword api_key", include_keywords=False
        )
        assert flag is False
        assert reasons == []

    def test_include_keywords_false_keeps_regex(self):
        """Disabling keywords must never suppress a real credential shape."""
        flag, reasons = contains_secret(
            "token ghp_aaaaaaaaaaaaaaaaaaaaXXX", include_keywords=False
        )
        assert flag is True
        assert any("github-pat" in r for r in reasons)


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
