"""A procedure earns credit for work that was finished, and is debited only
for a failure the run itself demonstrates.

Before this, `procedure_from_episode`, the counter-moving fold-in (then
`with_episode`; retired 2026-08-03 — production folds in via
`merged_from_episode`, and counters move only through `with_outcome`) and
`feedback_for_episode` each keyed on `outcome` — the evidence axis. So the
live store's blocked non-answer credited `tools:file_read` up to
`success_count=5, confidence=0.857` (MIR-057), and an evidence-`partial`
answer debited a procedure that may have executed perfectly.

Two named predicates now decide, and the credit one is used by every
crediting path so they cannot drift:

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
