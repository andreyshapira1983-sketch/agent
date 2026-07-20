"""Task completion is a separate axis from evidence quality (MIR-057), schema.

The defect: `outcome` is derived from chunk counts alone, so a cycle that was
blocked — the evidence budget truncated the file it needed — banks `success`,
becomes `usage_eligible`, and credits a procedure. Verification measures
whether claims are grounded; it cannot measure whether the goal was reached.

This commit adds the axis and nothing else: two durable fields, an assembly
rule, serialization. No reader consults `completion_state` yet, so behaviour is
unchanged — the gates land in later commits.

Two properties are pinned here because they are the ones that cannot be fixed
later without rewriting history:

*   **The state is frozen at banking.** A state re-derived on every read would
    reclassify old episodes whenever the rule changes, and procedural feedback
    for those episodes has already been applied under the old rule.
*   **Legacy is never reconstructed.** A row written before this field simply
    has no key; it reads as `unknown` and no abort tag or `replan_exhausted`
    flag is retro-fitted into a verdict. `None` (never classified) stays
    distinguishable from an explicit state, exactly as `usage_eligible` does.
"""
from __future__ import annotations

import json

import pytest

from core.smart_memory import (
    EpisodeRecord,
    assemble_completion_state,
    effective_completion,
    episode_from_agent_cycle,
)


def _cycle(**kwargs):
    base = dict(
        goal="count the lines",
        question="сколько строк в файле core/loop_methods2.py",
        answer="Conclusion: точное число определить нельзя, содержимое обрезано",
        tools_used=["file_read"],
        source_labels=["file:core/loop_methods2.py"],
        verified_chunks=3,
        unverified_chunks=2,
    )
    base.update(kwargs)
    return episode_from_agent_cycle(**base)


# ==========================================================================
# Assembly — the closed table, applied at banking only.
# ==========================================================================
def test_a_cancelled_run_is_cancelled_not_failed() -> None:
    assert assemble_completion_state(
        aborted_reason="cancelled", replan_exhausted=False, declared=None
    ) == "cancelled"


def test_any_other_abort_is_failed() -> None:
    assert assemble_completion_state(
        aborted_reason="TypeError", replan_exhausted=False, declared=None
    ) == "failed"


def test_replan_exhaustion_is_failed() -> None:
    assert assemble_completion_state(
        aborted_reason="", replan_exhausted=True, declared=None
    ) == "failed"


def test_a_declaration_sets_the_state_when_no_override_fired() -> None:
    assert assemble_completion_state(
        aborted_reason="", replan_exhausted=False, declared="blocked"
    ) == "blocked"


def test_no_declaration_and_no_override_is_unknown() -> None:
    """The honest default: nothing observed, so nothing is claimed."""
    assert assemble_completion_state(
        aborted_reason="", replan_exhausted=False, declared=None
    ) == "unknown"


@pytest.mark.parametrize("declared", ["achieved", "partially_achieved", "failed"])
def test_a_structural_override_always_dominates_the_declaration(declared: str) -> None:
    """A model that says it succeeded cannot overrule a run that did not finish."""
    assert assemble_completion_state(
        aborted_reason="cancelled", replan_exhausted=False, declared=declared
    ) == "cancelled"
    assert assemble_completion_state(
        aborted_reason="", replan_exhausted=True, declared=declared
    ) == "failed"


# ==========================================================================
# Freezing — the state is stored, never recomputed on read.
# ==========================================================================
def test_the_state_is_frozen_into_the_banked_episode() -> None:
    episode = _cycle(declared_completion="blocked")

    assert episode.declared_completion == "blocked"
    assert episode.completion_state == "blocked"


def test_a_replay_lands_on_unknown_without_a_special_rule() -> None:
    """No abort, no replan, no declaration — branch 5, by construction.

    The fast-path replay needs no override of its own: it is already refused
    by the evidence axis (it banks 0/1 → `partial`), and inventing a
    `source_labels` predicate for it would tie the rule to an unenforced
    naming convention.
    """
    episode = _cycle(
        answer="cached answer", tools_used=[], source_labels=["memory:ep-1"],
        verified_chunks=0, unverified_chunks=1,
    )

    assert episode.declared_completion is None
    assert episode.completion_state == "unknown"


def test_a_stored_state_survives_a_rule_change(monkeypatch: pytest.MonkeyPatch) -> None:
    """Feedback was already applied under the old rule; re-deriving rewrites it."""
    episode = _cycle(declared_completion="achieved")
    restored = EpisodeRecord.from_dict(json.loads(json.dumps(episode.to_dict())))

    monkeypatch.setattr(
        "core.smart_memory.assemble_completion_state",
        lambda **_: "failed",
    )

    assert restored.completion_state == "achieved", (
        "the banked verdict is history, not a view over today's rule"
    )


# ==========================================================================
# Legacy — absent means never classified, and is never reconstructed.
# ==========================================================================
def test_a_legacy_row_reads_as_unclassified() -> None:
    row = {
        "id": "ep-legacy", "goal": "g", "question": "q", "outcome": "success",
        "summary": "s", "verified_chunks": 3, "unverified_chunks": 0,
    }

    episode = EpisodeRecord.from_dict(row)

    assert episode.completion_state is None, "None marks a row that was never classified"
    assert episode.declared_completion is None
    assert effective_completion(episode) == "unknown", "readers see one answer"


def test_legacy_abort_and_replan_are_not_reconstructed_on_read() -> None:
    """The strict contract: no key, no verdict — whatever else the row says."""
    row = {
        "id": "ep-legacy-abort", "goal": "g", "question": "q", "outcome": "failed",
        "summary": "s", "replan_exhausted": True,
        "tags": ["episode", "failed", "aborted", "aborted:TypeError"],
    }

    episode = EpisodeRecord.from_dict(row)

    assert episode.completion_state is None
    assert effective_completion(episode) == "unknown", (
        "re-deriving legacy would hand a verdict to episodes whose procedural "
        "feedback was already applied under the old policy"
    )


def test_an_unknown_stored_token_does_not_become_a_verdict() -> None:
    row = {
        "id": "ep-x", "goal": "g", "question": "q", "outcome": "success", "summary": "s",
        "completion_state": "totally_fine", "declared_completion": "totally_fine",
    }

    episode = EpisodeRecord.from_dict(row)

    assert effective_completion(episode) == "unknown"
    assert episode.declared_completion is None


# ==========================================================================
# Serialization — omit-when-None, so absent stays honest.
# ==========================================================================
def test_none_fields_are_omitted_not_written_as_null() -> None:
    payload = EpisodeRecord(
        goal="g", question="q", outcome="success", summary="s"
    ).to_dict()

    assert "declared_completion" not in payload
    assert "completion_state" not in payload


def test_an_explicit_state_round_trips() -> None:
    episode = _cycle(declared_completion="refused")
    restored = EpisodeRecord.from_dict(json.loads(json.dumps(episode.to_dict())))

    assert restored.completion_state == episode.completion_state
    assert restored.declared_completion == "refused"


def test_an_explicit_null_reads_as_absent() -> None:
    row = {
        "id": "ep-n", "goal": "g", "question": "q", "outcome": "success", "summary": "s",
        "completion_state": None, "declared_completion": None,
    }

    assert EpisodeRecord.from_dict(row).completion_state is None


# ==========================================================================
# Behaviour is unchanged in this commit.
# ==========================================================================
def test_the_evidence_axis_is_untouched() -> None:
    """`outcome` keeps its meaning; this commit adds an axis, it moves none."""
    blocked = _cycle(declared_completion="blocked")

    assert blocked.outcome == "success", (
        "the evidence axis still reports that the claims were well supported — "
        "that is exactly why a second axis is needed"
    )
    assert blocked.answer_quality_score == pytest.approx(0.6)
