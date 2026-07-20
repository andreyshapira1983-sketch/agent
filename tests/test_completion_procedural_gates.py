"""A procedure earns credit for work that was finished, and is debited only
for a failure the run itself demonstrates.

Before this, `procedure_from_episode`, `with_episode` and `feedback_for_episode`
each keyed on `outcome` — the evidence axis. So the live store's blocked
non-answer credited `tools:file_read` up to `success_count=5, confidence=0.857`
(MIR-057), and an evidence-`partial` answer debited a procedure that may have
executed perfectly.

Two named predicates now decide, and the credit one is used by all three
paths so they cannot drift:

    procedure_credit_allowed   achieved + success + tools
    procedure_debit_allowed    a failure the RUN demonstrates

The debit predicate is deliberately narrow: a frozen `failed` PLUS an
exhausted replan. `completion_state` stays authoritative and is never
recomputed here; `replan_exhausted` only narrows an existing `failed`, it can
never create one. The question it answers is whether this stored failure came
from something the run did, or from the answer's own claim — a model saying
"I failed" is not evidence that the workflow it used is bad.

An aborted run is neutral for a structural reason rather than a policy one:
the abort path carries no `used_procedure_ids`, so no causal link exists to
debit through. Abort feedback would need attribution AND a normalised failure
code designed together, and is tracked separately.

Derived `aborted:*` tags are never consulted — a value computed from another
value must not be what authorises a debit. The raw reason is deliberately not
persisted either: a field without attribution would look like working abort
feedback while creating none.

MIR-048's evidence-`partial` debit is retired here as a deliberate policy
change: weak support for an answer is not proof that the procedure failed.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.bootstrap import build_agent
from core.smart_memory import (
    EpisodeRecord,
    ProcedureRecord,
    feedback_for_episode,
    procedure_credit_allowed,
    procedure_debit_allowed,
    procedure_from_episode,
)

QUESTION = "how do I deploy the service"


@pytest.fixture(autouse=True)
def _offline_routing(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "HF_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AGENT_ALLOW_MOCK_ROUTING", "1")


def _ep(
    *,
    outcome: str = "success",
    completion: str | None = "achieved",
    tools: tuple[str, ...] = ("file_read",),
    replan: bool = False,
    tags: tuple[str, ...] = (),
    eid: str = "ep-1",
) -> EpisodeRecord:
    return EpisodeRecord(
        goal="deploy", question=QUESTION, outcome=outcome,  # type: ignore[arg-type]
        summary="s", tools_used=tools, verified_chunks=3, unverified_chunks=0,
        completion_state=completion, replan_exhausted=replan,  # type: ignore[arg-type]
        tags=tags, used_procedure_ids=("proc-1",),
        id=eid, created_at=datetime.now(timezone.utc).isoformat(),
    )


def _procedure() -> ProcedureRecord:
    return ProcedureRecord(
        id="proc-1", name="Workflow using file_read", workflow_key="tools:file_read",
        trigger_tags=("file_read",), steps=("Run tool: file_read",),
        source_episode_ids=(), success_count=2, failure_count=0,
        confidence=0.75, status="active",
    )


def _apply(tmp_path: Path, episode: EpisodeRecord) -> ProcedureRecord:
    agent = build_agent(tmp_path, with_memory=True, approval_provider=None)
    agent.procedural_store.rewrite([_procedure()])
    agent.procedural_store.apply_episode_feedback(episode)
    return agent.procedural_store.load()[0]


# ==========================================================================
# Credit — one predicate, three paths.
# ==========================================================================
def test_an_achieved_success_still_earns_credit(tmp_path: Path) -> None:
    episode = _ep()

    assert procedure_credit_allowed(episode) is True
    assert procedure_from_episode(episode) is not None
    assert feedback_for_episode(episode) == "success"
    assert _apply(tmp_path, episode).success_count == 3


def test_a_blocked_success_earns_nothing(tmp_path: Path) -> None:
    """The live case: the claims held up, the task did not get done."""
    episode = _ep(completion="blocked")

    assert procedure_credit_allowed(episode) is False
    assert procedure_from_episode(episode) is None
    assert feedback_for_episode(episode) == "none"
    after = _apply(tmp_path, episode)
    assert (after.success_count, after.failure_count, after.confidence) == (2, 0, 0.75)


@pytest.mark.parametrize(
    "completion", ["partially_achieved", "refused", "unknown", None]
)
def test_neither_credit_nor_debit_for_the_middle_states(completion, tmp_path: Path) -> None:
    episode = _ep(completion=completion)

    assert procedure_credit_allowed(episode) is False
    assert procedure_debit_allowed(episode) is False
    after = _apply(tmp_path, episode)
    assert (after.success_count, after.failure_count) == (2, 0)


def test_both_axes_are_required_for_credit() -> None:
    """`achieved` does not rescue an answer whose support did not hold."""
    assert procedure_credit_allowed(_ep(outcome="partial")) is False
    assert procedure_credit_allowed(_ep(outcome="failed")) is False


def test_credit_still_needs_tools() -> None:
    assert procedure_credit_allowed(_ep(tools=())) is False


def test_the_same_predicate_guards_the_counter_and_the_creation() -> None:
    """A path that could credit without creating would drift from one that
    creates without crediting."""
    blocked = _ep(completion="blocked")
    procedure = _procedure().with_episode(blocked)

    assert procedure.success_count == 2, "with_episode must ask the same question"
    assert procedure_from_episode(blocked) is None


# ==========================================================================
# Debit — only a failure the run demonstrates.
# ==========================================================================
def test_an_exhausted_replan_debits(tmp_path: Path) -> None:
    episode = _ep(outcome="failed", completion="failed", replan=True)

    assert procedure_debit_allowed(episode) is True
    assert feedback_for_episode(episode) == "failure"
    assert _apply(tmp_path, episode).failure_count == 1


def test_an_aborted_run_is_neutral_and_reaches_no_feedback(tmp_path: Path) -> None:
    """Not a policy choice — there is no causal link to debit through.

    `_record_aborted_episode` carries no `used_procedure_ids`, so feedback
    exits before touching anything. Supporting abort feedback needs
    attribution AND a normalised failure code, designed together; tracked
    separately, deliberately out of scope here.
    """
    agent = build_agent(tmp_path, with_memory=True, approval_provider=None)
    agent.procedural_store.rewrite([_procedure()])
    aborted = EpisodeRecord(
        goal="g", question=QUESTION, outcome="failed", summary="s",  # type: ignore[arg-type]
        tools_used=(), completion_state="failed",  # type: ignore[arg-type]
        tags=("episode", "failed", "aborted", "aborted:TypeError"),
        used_procedure_ids=None,
        id="ep-abort", created_at=datetime.now(timezone.utc).isoformat(),
    )

    report = agent.procedural_store.apply_episode_feedback(aborted)

    assert procedure_debit_allowed(aborted) is False, "no replan exhaustion, no debit"
    assert report["attribution"] == "unknown", "the abort path records no attribution"
    assert report["applied"] == 0
    after = agent.procedural_store.load()[0]
    assert (after.success_count, after.failure_count) == (2, 0)


def test_a_declared_failure_is_neutral(tmp_path: Path) -> None:
    """The model reporting it did not succeed says nothing about the workflow
    it used — the failure may be entirely upstream of the procedure."""
    episode = _ep(outcome="failed", completion="failed")

    assert procedure_debit_allowed(episode) is False
    assert feedback_for_episode(episode) == "none"
    after = _apply(tmp_path, episode)
    assert (after.success_count, after.failure_count) == (2, 0)


def test_a_cancelled_run_is_neutral(tmp_path: Path) -> None:
    """Cancellation is a control signal; punishing a procedure for it would
    make the counters describe scheduling."""
    episode = _ep(outcome="failed", completion="cancelled")

    assert procedure_debit_allowed(episode) is False
    assert _apply(tmp_path, episode).failure_count == 0


def test_an_abort_tag_alone_does_not_authorise_a_debit(tmp_path: Path) -> None:
    """Tags are derived. A derived value must not be what opens the debit."""
    episode = _ep(
        outcome="failed", completion="failed",
        tags=("episode", "failed", "aborted", "aborted:TypeError"),
    )

    assert procedure_debit_allowed(episode) is False
    assert _apply(tmp_path, episode).failure_count == 0


def test_the_frozen_state_outranks_a_structural_field(tmp_path: Path) -> None:
    """`replan_exhausted` narrows an existing `failed`; it cannot create one."""
    episode = _ep(completion="achieved", replan=True)

    assert procedure_debit_allowed(episode) is False
    assert _apply(tmp_path, episode).failure_count == 0


def test_legacy_is_never_retroactively_debited(tmp_path: Path) -> None:
    """A row from before the axis carries `replan_exhausted` but no verdict."""
    episode = _ep(outcome="failed", completion=None, replan=True)

    assert procedure_debit_allowed(episode) is False
    after = _apply(tmp_path, episode)
    assert (after.success_count, after.failure_count) == (2, 0)


# ==========================================================================
# The retired policy, stated rather than quietly dropped.
# ==========================================================================
def test_an_evidence_partial_no_longer_debits(tmp_path: Path) -> None:
    """Deliberate change to MIR-048. `partial` means unverified support
    outnumbered verified support — a fact about the ANSWER's grounding, not
    proof that the workflow failed. It used to cost the procedure a failure."""
    episode = _ep(outcome="partial", completion="partially_achieved")

    assert feedback_for_episode(episode) == "none"
    after = _apply(tmp_path, episode)
    assert after.failure_count == 0, "evidence weakness is not procedural failure"


# ==========================================================================
# Guards on the neighbouring commits.
# ==========================================================================
def test_a_crashed_verifier_still_blocks_credit(tmp_path: Path) -> None:
    """Commit 2's suppression is an independent safety net, not superseded."""
    agent = build_agent(tmp_path, with_memory=True, approval_provider=None)
    agent.procedural_store.rewrite([_procedure()])
    episode = _ep()

    report = agent.procedural_store.apply_episode_feedback(episode, allow_credit=False)

    assert report["credit_suppressed"] is True
    assert agent.procedural_store.load()[0].success_count == 2


def test_feedback_stays_idempotent(tmp_path: Path) -> None:
    agent = build_agent(tmp_path, with_memory=True, approval_provider=None)
    agent.procedural_store.rewrite([_procedure()])
    episode = _ep()

    agent.procedural_store.apply_episode_feedback(episode)
    agent.procedural_store.apply_episode_feedback(episode)

    assert agent.procedural_store.load()[0].success_count == 3


def test_the_episodic_readers_are_untouched(tmp_path: Path) -> None:
    """Commit 4's gates keep their behaviour; this commit moves nothing there."""
    from core.smart_memory import decide_usage_eligibility

    assert decide_usage_eligibility(_ep(tools=())) is True
    assert decide_usage_eligibility(_ep(completion="blocked", tools=())) is False
