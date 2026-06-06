"""MemoryWritePolicy + MemoryRetrievalPolicy unit tests.

Every reject branch from the architecture spec must be exercised so the
agent never silently persists secrets, noise, or non-consented data.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core.memory_policy import (
    MemoryRetrievalPolicy,
    MemoryWriteDecision,
    MemoryWritePolicy,
)
from core.models import MemoryRecord


# ============================================================
# Write Policy — accepts
# ============================================================

class TestWritePolicyAccepts:
    def test_user_explicit_source_passes(self):
        policy = MemoryWritePolicy()
        d = policy.decide(
            content="I prefer concise Russian answers.",
            tags=[],
            source="user-explicit",
        )
        assert d.decision == "save"

    def test_consent_tag_passes(self):
        policy = MemoryWritePolicy()
        d = policy.decide(
            content="User stack: Python 3.12 + Pydantic v2.",
            tags=["preference"],
            source="agent-auto",
        )
        assert d.decision == "save"

    def test_decision_tag_passes(self):
        policy = MemoryWritePolicy()
        d = policy.decide(
            content="Architecture: keep planner LLM-driven, executor deterministic.",
            tags=["decision"],
            source="agent-auto",
        )
        assert d.decision == "save"

    def test_sensitive_data_consent_tag_passes_but_marks_redaction(self):
        policy = MemoryWritePolicy()
        d = policy.decide(
            content="Contact email is andre@example.com.",
            tags=["fact", "sensitive-data-consent"],
            source="user-explicit",
        )
        assert d.decision == "save"
        assert any("PII markers" in r for r in d.reasons)
        assert any("stored redacted" in r for r in d.reasons)


# ============================================================
# Write Policy — context freeze (operator brake)
# ============================================================

class TestWritePolicyContextFreeze:
    def test_frozen_source_is_rejected_even_with_consent_tag(self):
        policy = MemoryWritePolicy(frozen_sources={"agent-auto"})
        d = policy.decide(
            content="User stack: Python 3.12 + Pydantic v2.",
            tags=["fact"],
            source="agent-auto",
        )
        assert d.decision == "reject"
        assert any("frozen in this context" in r for r in d.reasons)

    def test_freeze_is_case_insensitive(self):
        policy = MemoryWritePolicy(frozen_sources={"Agent-Auto"})
        d = policy.decide(
            content="Some auto-learned fact about the codebase.",
            tags=["fact"],
            source="AGENT-AUTO",
        )
        assert d.decision == "reject"
        assert any("frozen in this context" in r for r in d.reasons)

    def test_user_explicit_not_frozen_when_only_agent_auto_frozen(self):
        policy = MemoryWritePolicy(frozen_sources={"agent-auto"})
        d = policy.decide(
            content="I prefer concise Russian answers.",
            tags=["preference"],
            source="user-explicit",
        )
        assert d.decision == "save"

    def test_default_policy_does_not_freeze_anything(self):
        policy = MemoryWritePolicy()
        assert policy.frozen_sources == frozenset()
        d = policy.decide(
            content="User stack: Python 3.12 + Pydantic v2.",
            tags=["fact"],
            source="agent-auto",
        )
        assert d.decision == "save"


# ============================================================
# Write Policy — rejects: secrets
# ============================================================

class TestWritePolicyRejectsSecrets:
    @pytest.mark.parametrize(
        "secret",
        [
            "OpenAI key: sk-abcdefghijklmnopqrstuvwxyz0123",
            "Anthropic: sk-ant-1234567890ABCDEFGHIJKLMN",
            "GitHub PAT: ghp_aaaaaaaaaaaaaaaaaaaaXXX",
            "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",
            "AWS AKIAIOSFODNN7EXAMPLE",
            "-----BEGIN PRIVATE KEY-----\nMIIEv...",
        ],
    )
    def test_credential_pattern_rejected_despite_consent(self, secret):
        policy = MemoryWritePolicy()
        d = policy.decide(content=secret, tags=["fact"], source="user-explicit")
        assert d.decision == "reject"
        assert any("secret" in r for r in d.reasons)

    @pytest.mark.parametrize(
        "phrase",
        [
            "my password is hunter2",
            "API_KEY=foo123",
            "my apikey: bar",
            "Authorization: Bearer x",
            "private_key=PEM_BLOB",
        ],
    )
    def test_secret_keyword_rejected(self, phrase):
        policy = MemoryWritePolicy()
        d = policy.decide(content=phrase, tags=["fact"], source="user-explicit")
        assert d.decision == "reject"
        assert any("secret keyword" in r for r in d.reasons)


# ============================================================
# Write Policy — rejects: sensitive PII without explicit consent
# ============================================================

class TestWritePolicyRejectsSensitiveData:
    @pytest.mark.parametrize(
        "content",
        [
            "Email: andre@example.com",
            "SSN: 123-45-6789",
            "Phone: +1 415 555 1234",
        ],
    )
    def test_pii_rejected_without_sensitive_consent_tag(self, content):
        policy = MemoryWritePolicy()
        d = policy.decide(content=content, tags=["fact"], source="user-explicit")
        assert d.decision == "reject"
        assert any("PII markers" in r for r in d.reasons)
        assert any("sensitive-data-consent" in r for r in d.reasons)


# ============================================================
# Write Policy — rejects: structural noise
# ============================================================

class TestWritePolicyRejectsNoise:
    def test_empty_rejected(self):
        policy = MemoryWritePolicy()
        d = policy.decide(content="   ", tags=["fact"], source="user-explicit")
        assert d.decision == "reject"
        assert any("empty" in r for r in d.reasons)

    def test_too_short_rejected(self):
        policy = MemoryWritePolicy()
        d = policy.decide(content="hi", tags=["fact"], source="user-explicit")
        assert d.decision == "reject"
        assert any("too short" in r for r in d.reasons)

    def test_too_long_rejected(self):
        policy = MemoryWritePolicy()
        d = policy.decide(content="x" * 5_000, tags=["fact"], source="user-explicit")
        assert d.decision == "reject"
        assert any("too long" in r for r in d.reasons)

    def test_tool_dump_rejected(self):
        policy = MemoryWritePolicy()
        dump = (
            '[{"title": "X", "url": "https://example.com/page", '
            '"snippet": "..."}, {"url": "https://b.com"}]'
        )
        d = policy.decide(content=dump, tags=["fact"], source="user-explicit")
        assert d.decision == "reject"
        assert any("tool-result" in r for r in d.reasons)


# ============================================================
# Write Policy — write-time dedup (MVP-10)
# ============================================================

class TestWritePolicyDedup:
    """The Write Policy refuses to persist a near-duplicate of any
    record already on disk. The `existing` parameter is supplied by
    the caller (typically `store.load()`), keeping the policy a pure
    function. Same threshold as `core.hygiene.deduplicate_memory`.
    """

    def _existing(self) -> list[MemoryRecord]:
        return [
            MemoryRecord(
                content="I prefer concise Russian answers and JSON output",
                tags=["preference", "fact"],
                owner="user",
            )
        ]

    def test_no_existing_records_keeps_old_behaviour(self):
        d = MemoryWritePolicy().decide(
            content="A normal fact about the project.",
            tags=["fact"],
            source="user-explicit",
            existing=[],
        )
        assert d.decision == "save"

    def test_exact_duplicate_rejected(self):
        existing = self._existing()
        d = MemoryWritePolicy().decide(
            content="I prefer concise Russian answers and JSON output",
            tags=["preference", "fact"],
            source="user-explicit",
            existing=existing,
        )
        assert d.decision == "reject"
        assert any("duplicate of" in r for r in d.reasons)
        assert any(existing[0].id in r for r in d.reasons)

    def test_case_and_whitespace_insensitive_dedup(self):
        d = MemoryWritePolicy().decide(
            content="I PREFER concise   Russian answers and JSON   output",
            tags=["preference", "fact"],
            source="user-explicit",
            existing=self._existing(),
        )
        assert d.decision == "reject"

    def test_unrelated_new_record_passes(self):
        d = MemoryWritePolicy().decide(
            content="The project ships on Friday and the deploy is at noon.",
            tags=["fact"],
            source="user-explicit",
            existing=self._existing(),
        )
        assert d.decision == "save"

    def test_dedup_runs_AFTER_secret_check(self):
        # A secret with a near-duplicate match still rejects on secret —
        # the credential reason should win, not the dedup reason. We
        # construct an existing record that *would* match if dedup ran
        # first, but the secret pattern is the more important signal.
        existing = [
            MemoryRecord(
                content="my password is hunter2",
                tags=["preference"],
                owner="user",
            )
        ]
        d = MemoryWritePolicy().decide(
            content="my password is hunter2",
            tags=["preference"],
            source="user-explicit",
            existing=existing,
        )
        assert d.decision == "reject"
        # The secret-keyword reason must be present; the dedup reason
        # would say "duplicate of" — we tolerate it being present too,
        # but the secret reason MUST be there.
        assert any(
            "password" in r or "credential" in r or "secret" in r
            for r in d.reasons
        )

    def test_dedup_does_not_compare_against_zero_length(self):
        # An existing empty-content record can't accidentally match
        # everything.
        existing = [
            MemoryRecord(content="", tags=["preference"], owner="user")
        ]
        d = MemoryWritePolicy().decide(
            content="A real fact about the project.",
            tags=["fact"],
            source="user-explicit",
            existing=existing,
        )
        assert d.decision == "save"


# ============================================================
# Write Policy — echo gate (A1 Memory Echo Antibody)
# ============================================================

class TestWritePolicyEchoGate:
    """`recent_writes` lets the policy refuse an `agent-auto` record that
    echoes something the agent itself wrote in the recent window. The
    detector lives in `core.memory_echo_antibody`; here we lock in that
    the policy wires it before the on-disk dedup gate and that
    `user-explicit` writes are never echo-guarded.
    """

    def _recent(self, content: str, source: str = "agent-auto"):
        from core.memory_echo_antibody import make_event

        return [make_event(content, tags=["fact"], source=source)]

    def test_agent_auto_echo_is_rejected(self):
        d = MemoryWritePolicy().decide(
            content="The build fails when the cache is stale.",
            tags=["fact"],
            source="agent-auto",
            recent_writes=self._recent("The build fails when the cache is stale."),
        )
        assert d.decision == "reject"
        assert any("memory_echo_suspected" in r for r in d.reasons)

    def test_user_explicit_echo_bypasses(self):
        # Byte-identical content, but a HUMAN write is never echo-guarded.
        d = MemoryWritePolicy().decide(
            content="The build fails when the cache is stale.",
            tags=["fact"],
            source="user-explicit",
            recent_writes=self._recent("The build fails when the cache is stale."),
        )
        assert d.decision == "save"

    def test_fresh_agent_auto_write_passes(self):
        d = MemoryWritePolicy().decide(
            content="A brand new observation about the deploy pipeline.",
            tags=["fact"],
            source="agent-auto",
            recent_writes=self._recent("Something completely unrelated earlier."),
        )
        assert d.decision == "save"

    def test_no_recent_writes_keeps_old_behaviour(self):
        d = MemoryWritePolicy().decide(
            content="The build fails when the cache is stale.",
            tags=["fact"],
            source="agent-auto",
            recent_writes=[],
        )
        assert d.decision == "save"

    def test_echo_gate_runs_before_secret_check_does_not_mask_secret(self):
        # A secret must still be caught even if it also echoes a recent
        # write — the secret reason is the more important signal and the
        # echo gate sits AFTER the hard secret block.
        d = MemoryWritePolicy().decide(
            content="my password is hunter2",
            tags=["fact"],
            source="agent-auto",
            recent_writes=self._recent("my password is hunter2"),
        )
        assert d.decision == "reject"
        assert any(
            "password" in r or "credential" in r or "secret" in r
            for r in d.reasons
        )


# ============================================================
# Write Policy — owner parameter (third-party data gate)
# ============================================================

class TestWritePolicyOwnerGate:
    """The `owner` parameter sits between consent and persistence (§7
    «данные других людей»). First-party values pass quietly; anything
    else needs the `cross-owner-consent` tag. Unit-level smoke here;
    the loop-side flow is also exercised in test_safety_integration.py.
    """

    def test_default_owner_is_first_party(self):
        # Existing callers don't pass `owner` — backward-compat: save.
        d = MemoryWritePolicy().decide(
            content="A normal fact about the project.",
            tags=["fact"],
            source="user-explicit",
        )
        assert d.decision == "save"

    @pytest.mark.parametrize("owner", ["self", "user", "session"])
    def test_first_party_owners_save(self, owner):
        d = MemoryWritePolicy().decide(
            content="A normal fact about the project.",
            tags=["fact"],
            source="user-explicit",
            owner=owner,
        )
        assert d.decision == "save"
        assert any(f"owner={owner}" in r for r in d.reasons)

    @pytest.mark.parametrize("owner", ["User", "  USER  ", "Session"])
    def test_first_party_owner_match_is_case_and_whitespace_insensitive(
        self, owner
    ):
        d = MemoryWritePolicy().decide(
            content="A normal fact about the project.",
            tags=["fact"],
            source="user-explicit",
            owner=owner,
        )
        assert d.decision == "save"

    @pytest.mark.parametrize(
        "owner", ["client", "partner", "employee", "third_party"]
    )
    def test_third_party_owner_without_consent_rejected(self, owner):
        d = MemoryWritePolicy().decide(
            content="Something my client said in the meeting.",
            tags=["fact"],
            source="user-explicit",
            owner=owner,
        )
        assert d.decision == "reject"
        assert any("third-party" in r for r in d.reasons)
        assert any("cross-owner-consent" in r for r in d.reasons)

    def test_third_party_owner_with_consent_tag_saves(self):
        d = MemoryWritePolicy().decide(
            content="My client gave permission to record this.",
            tags=["fact", "cross-owner-consent"],
            source="user-explicit",
            owner="client",
        )
        assert d.decision == "save"

    def test_owner_gate_runs_AFTER_secret_check(self):
        # A secret with a cross-owner-consent tag must STILL be rejected
        # by the secret scanner — consent doesn't unlock credentials.
        d = MemoryWritePolicy().decide(
            content="here's the password: hunter2",
            tags=["fact", "cross-owner-consent"],
            source="user-explicit",
            owner="client",
        )
        assert d.decision == "reject"
        # Reject reason mentions secret, not third-party — secret wins.
        assert any("secret" in r for r in d.reasons)


# ============================================================
# Write Policy — rejects: consent
# ============================================================

class TestWritePolicyRejectsNoConsent:
    def test_no_source_no_tag_rejected(self):
        policy = MemoryWritePolicy()
        d = policy.decide(
            content="Random observation that should not be stored.",
            tags=[],
            source="agent-auto",
        )
        assert d.decision == "reject"
        assert any("consent" in r for r in d.reasons)

    def test_irrelevant_tag_rejected(self):
        policy = MemoryWritePolicy()
        d = policy.decide(
            content="Random observation that should not be stored.",
            tags=["misc"],
            source="agent-auto",
        )
        assert d.decision == "reject"

    @pytest.mark.parametrize("blocked", ["transient", "temporary", "do-not-save", "ephemeral"])
    def test_blocked_tag_rejected_even_with_consent(self, blocked):
        policy = MemoryWritePolicy()
        d = policy.decide(
            content="Some otherwise legitimate fact.",
            tags=["fact", blocked],
            source="user-explicit",
        )
        assert d.decision == "reject"
        assert any("blocked tag" in r for r in d.reasons)


# ============================================================
# Retrieval Policy
# ============================================================

def _make_record(content: str, tags: list[str] | None = None, age_s: int = 0) -> MemoryRecord:
    return MemoryRecord(
        type="semantic",
        content=content,
        tags=tags or [],
        owner="user",
        created_at=datetime.now(timezone.utc) - timedelta(seconds=age_s),
    )


class TestRetrievalPolicy:
    def test_returns_empty_when_no_records(self):
        policy = MemoryRetrievalPolicy()
        assert policy.select([], "anything") == []

    def test_returns_empty_when_question_is_blank(self):
        policy = MemoryRetrievalPolicy()
        recs = [_make_record("python is great")]
        assert policy.select(recs, "") == []
        assert policy.select(recs, "   ") == []

    def test_picks_keyword_match(self):
        policy = MemoryRetrievalPolicy()
        recs = [
            _make_record("User prefers Python over JavaScript"),
            _make_record("Weather is nice today"),
            _make_record("User dislikes meetings on Mondays"),
        ]
        picked = policy.select(recs, "tell me about python and javascript")
        # Only the first record shares strong tokens (python, javascript).
        assert len(picked) == 1
        assert "Python" in picked[0].content

    def test_caps_at_max_records(self):
        policy = MemoryRetrievalPolicy(max_records=2)
        recs = [
            _make_record("apple banana cherry"),
            _make_record("apple banana date"),
            _make_record("apple banana eggplant"),
            _make_record("apple banana fig"),
        ]
        picked = policy.select(recs, "apple banana question")
        assert len(picked) == 2

    def test_recency_breaks_ties(self):
        policy = MemoryRetrievalPolicy(max_records=1)
        recs = [
            _make_record("python rocks", age_s=3600),
            _make_record("python rocks", age_s=0),  # newest
        ]
        picked = policy.select(recs, "tell me about python")
        assert len(picked) == 1
        assert picked[0].created_at == max(r.created_at for r in recs)

    def test_stopwords_dont_score(self):
        # Question is mostly stopwords; only "weather" carries signal.
        policy = MemoryRetrievalPolicy()
        recs = [
            _make_record("The weather is what it is."),
            _make_record("Completely unrelated text about cookies."),
        ]
        picked = policy.select(recs, "What is the weather?")
        assert len(picked) == 1
        assert "weather" in picked[0].content.lower()

    def test_tag_overlap_counts(self):
        policy = MemoryRetrievalPolicy(min_score=1)
        recs = [_make_record("X.", tags=["python", "preference"])]
        picked = policy.select(recs, "tell me about python")
        assert len(picked) == 1

    def test_format_for_prompt_truncates(self):
        policy = MemoryRetrievalPolicy(per_record_chars=20)
        rec = _make_record("a" * 100, tags=["fact"])
        out = policy.format_for_prompt([rec])
        assert "…" in out
        assert rec.id in out
        assert "fact" in out

    def test_format_for_prompt_empty(self):
        assert MemoryRetrievalPolicy().format_for_prompt([]) == ""
