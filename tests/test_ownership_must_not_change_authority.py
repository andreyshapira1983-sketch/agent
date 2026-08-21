"""Proof 8 of 8: changing ownership must not change effective authority.

The other seven proofs describe a mechanism that does not exist yet and are
banked. This one is different by design: it is GREEN today. It records what the
unattended organs are actually allowed to do, measured at the sites that refuse,
so that after any consolidation the same contract must still hold. If an organ
comes out of that work holding one right it did not hold before, this goes red.

Two premises were corrected by measurement before this test was written, and
both matter more than the assertions below.

FIRST: there are not four different organ envelopes. `agent_tick.py` builds the
queue drain (:946), the self-build producer (:684), hygiene (:1165) and the
campaign lane (:1352) with identical arguments —
`build_agent(workspace, approval_provider=None, **UNATTENDED_MEMORY_PROFILE)`.
Probing all four returns the same 52 observables with zero differences. The
per-organ differences people assume exist are not in the construction; they are
made later by runtime mutation of the shared policy, planner and gateway
objects, which is exactly the mechanism that becomes unsafe once one host is
shared (MIR-114).

SECOND: the real envelope boundary today is unattended versus interactive, and
it is sharp — seven observables differ, every one of them a right.

So consolidation is cheaper than it looked in one way and more dangerous in
another: there is one envelope to preserve, not four, and the thing that must
never happen is an organ inheriting the interactive one.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_tick import UNATTENDED_MEMORY_PROFILE
from app.bootstrap import build_agent
from core.approval import AutoApprover
from tests.support.authority_probe import probe_authority

#: The observables on which the two profiles genuinely differ. Measured, not
#: chosen: every other observable is equal between them, so a test that pinned
#: all 52 would fail on an unrelated change and teach nothing.
_AUTHORITY_AXES = (
    "effective_sinks",
    "permitted_sinks",
    "episodic_replay",
    "escalation_terminal",
    "escalating_probes",
    "knowledge_auto_write_effective",
    "knowledge_refusal_rule",
)


def _unattended(workspace: Path):
    return build_agent(workspace, approval_provider=None, **UNATTENDED_MEMORY_PROFILE)


def test_every_unattended_organ_holds_the_same_authority(workspace: Path) -> None:
    """The queue drain, the self-build producer, hygiene and the campaign lane
    are one envelope wearing four constructor calls."""
    organs = {name: probe_authority(_unattended(workspace))
              for name in ("queue", "self_build", "hygiene", "campaign")}
    reference = organs["queue"]
    for name, observed in organs.items():
        differing = sorted(k for k in reference if observed.get(k) != reference.get(k))
        assert not differing, (
            f"organ {name!r} differs from the queue drain on {differing} — the "
            "four build sites are no longer one envelope, so consolidation can "
            "no longer preserve 'the' unattended authority"
        )


def test_the_unattended_envelope_is_what_it_is(workspace: Path) -> None:
    """The contract a consolidation must reproduce exactly."""
    observed = probe_authority(_unattended(workspace))
    assert observed["effective_sinks"] == ("episode", "hygiene")
    assert observed["permitted_sinks"] == ("episode", "hygiene")
    assert observed["episodic_replay"] == "refuses_replay"
    assert observed["escalation_terminal"] == "refuse:no_provider"
    assert observed["knowledge_auto_write_effective"] is False


def test_the_unattended_envelope_is_narrower_than_the_interactive_one(
    workspace: Path,
) -> None:
    """The direction of the difference is the point. If an organ were ever run
    under an interactive host it would gain every right on this list, silently:
    six more durable sinks, a stored answer served in place of a real cycle, an
    approval provider that says yes, and automatic knowledge writes."""
    unattended = probe_authority(_unattended(workspace))
    interactive = probe_authority(
        build_agent(workspace, with_memory=True,
                    approval_provider=AutoApprover(default="approve"))
    )

    differing = sorted(k for k in unattended if interactive.get(k) != unattended.get(k))
    assert differing == sorted(_AUTHORITY_AXES), (
        f"the two profiles now differ on {differing}; the axes this test guards "
        f"are {sorted(_AUTHORITY_AXES)}. A new axis is not a failure — it is a "
        "right that nobody has pinned yet, so add it here deliberately."
    )

    assert set(unattended["permitted_sinks"]) < set(interactive["permitted_sinks"])
    assert unattended["episodic_replay"] == "refuses_replay"
    assert interactive["episodic_replay"] == "serves_stored_answer"
    assert unattended["escalation_terminal"].startswith("refuse")
    assert interactive["escalation_terminal"].startswith("ask")


@pytest.mark.parametrize("axis", _AUTHORITY_AXES)
def test_each_guarded_axis_is_actually_observable(workspace: Path, axis: str) -> None:
    """Boundary pin: a probe that silently stopped reporting an axis would make
    every assertion above vacuous."""
    assert axis in probe_authority(_unattended(workspace))
