"""MemoryWritePolicy + MemoryRetrievalPolicy unit tests.

Every reject branch from the architecture spec must be exercised so the
agent never silently persists secrets, noise, or non-consented data.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core.memory_policy import (
    MemoryRetrievalPolicy,
    MemoryWritePolicy,
)
from core.models import MemoryRecord

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


    def test_user_explicit_echo_bypasses(self):
        # Byte-identical content, but a HUMAN write is never echo-guarded.
        d = MemoryWritePolicy().decide(
            content="The build fails when the cache is stale.",
            tags=["fact"],
            source="user-explicit",
            recent_writes=self._recent("The build fails when the cache is stale."),
        )
        assert d.decision == "save"


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
    def test_returns_empty_when_question_is_blank(self):
        policy = MemoryRetrievalPolicy()
        recs = [_make_record("python is great")]
        assert policy.select(recs, "") == []
        assert policy.select(recs, "   ") == []


    def test_broad_project_question_prefers_recent_memory_over_stale_readme_status(self):
        policy = MemoryRetrievalPolicy(max_records=1)
        recent = _make_record(
            "Recent persistent memory: pytest passed=1840 and budget usage is current.",
            tags=["project", "memory", "tests", "budget"],
            age_s=60,
        )
        stale_readme = _make_record(
            "README.md says current project status has 1200 passing tests.",
            tags=["readme", "project", "status"],
            age_s=90 * 24 * 3600,
        )

        picked = policy.select([stale_readme, recent], "What do you know about your project?")

        assert picked == [recent]

    def test_stale_readme_status_is_marked_historical_in_prompt(self):
        policy = MemoryRetrievalPolicy()
        stale_readme = _make_record(
            "README.md says current project status has 1200 passing tests.",
            tags=["readme", "project", "status"],
            age_s=90 * 24 * 3600,
        )

        formatted = policy.format_for_prompt([stale_readme])

        assert "historical README/reference" in formatted
        assert "before treating as current" in formatted

    def test_readme_architecture_reference_can_still_be_selected(self):
        policy = MemoryRetrievalPolicy(max_records=1)
        readme_architecture = _make_record(
            "README.md describes the project architecture and tool loop.",
            tags=["readme", "architecture", "project"],
            age_s=90 * 24 * 3600,
        )

        picked = policy.select([readme_architecture], "What do you know about your project?")
        formatted = policy.format_for_prompt(picked)

        assert picked == [readme_architecture]
        assert "README architecture/reference" in formatted

    def test_broad_project_question_prefers_tech_debt_over_readme_overview(self):
        policy = MemoryRetrievalPolicy(max_records=1)
        tech_debt = _make_record(
            "TECH_DEBT.md tracks the current operator-routing backlog.",
            tags=["tech debt", "project"],
            age_s=14 * 24 * 3600,
        )
        readme_overview = _make_record(
            "README.md describes the project architecture overview.",
            tags=["readme", "architecture", "project"],
            age_s=60,
        )

        picked = policy.select([readme_overview, tech_debt], "What do you know about your project?")

        assert picked == [tech_debt]


    def test_russian_broad_project_question_selects_project_records(self):
        policy = MemoryRetrievalPolicy(max_records=10)
        recs = [
            _make_record("Operator routing bug was fixed.", tags=["operator-routing"]),
            _make_record("Autonomous runtime has pending debt.", tags=["autonomy"]),
            _make_record("Tech debt backlog is tracked.", tags=["tech debt"]),
            _make_record("Plain travel preference.", tags=["travel"]),
        ]

        picked = policy.select(recs, "Что ты уже знаешь о своём проекте?")
        picked_tags = {tag for rec in picked for tag in rec.tags}

        assert {"operator-routing", "autonomy", "tech debt"} <= picked_tags
        assert "travel" not in picked_tags

    def test_russian_broad_project_question_prefers_recent_status_memory(self):
        policy = MemoryRetrievalPolicy(max_records=1)
        recent = _make_record(
            "Recent memory: model routing and budget status were checked today.",
            tags=["project", "memory", "model", "budget"],
            age_s=60,
        )
        stale_readme = _make_record(
            "README.md says current model and budget status are from an old snapshot.",
            tags=["readme", "project", "status"],
            age_s=90 * 24 * 3600,
        )

        picked = policy.select([stale_readme, recent], "Что ты уже знаешь о своём проекте?")

        assert picked == [recent]


    def test_format_for_prompt_truncates(self):
        policy = MemoryRetrievalPolicy(per_record_chars=20)
        rec = _make_record("a" * 100, tags=["fact"])
        out = policy.format_for_prompt([rec])
        assert "…" in out
        assert rec.id in out
        assert "fact" in out

    def test_format_for_prompt_empty(self):
        assert MemoryRetrievalPolicy().format_for_prompt([]) == ""


# ============================================================
# Tags are labels; only reserved words switch the policy
# ============================================================

class TestUserLabelsVersusReservedTags:
    """The contract for what a tag IS, from the write policy's side.

    An operator writes labels in their own language. The policy reserves a
    short set of words -- the consent tags and the blocking tags -- and matches
    them by name after lowercasing. A label that is not one of those words must
    not act like one, in either direction: it neither grants consent nor blocks
    a write.

    Before 2026-08-07 the `:remember` parser dropped non-ASCII tags and
    substituted `user-approved`, which handed the policy a consent tag the
    operator never typed. The parser now keeps labels verbatim, so these are
    the boundaries that matter here.
    """


    def test_a_label_is_not_consent_for_an_agent_write(self):
        """The mirror of the test above: a label that merely looks important
        does not let the agent persist on its own."""
        d = MemoryWritePolicy().decide(
            content="User stack: Python 3.12 and Pydantic v2.",
            tags=["важное", "факт"],
            source="agent-auto",
        )
        assert d.decision == "reject"
