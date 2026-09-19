"""Defect 2: the novelty gate compared phrasings, so two module splits collided.

Post-mortem of the 19:31 tick (2026-08-19): the goal «split the oversized
module 'core/model_router.py'» was declined as a repeat of «split the
oversized module 'core/smart_memory.py'» — Jaccard 0.78, the only differing
tokens being model/router/smart/memory. Engineering goals arrive from a
template, so after the road to the backlog opened, every second engineering
goal died. The fix is IDENTITY, not a lower threshold: when both goals name
their work target, sameness of target decides; the token overlap stays as
the fallback for goals that name none.
"""
from __future__ import annotations

from core.charter_goal import _repeats_recent

_SPLIT_SMART = (
    "Propose a plan to split the oversized module 'core/smart_memory.py' "
    "into focused modules to enhance maintainability and clarity."
)
_SPLIT_ROUTER = (
    "Propose a plan to split the oversized module 'core/model_router.py' "
    "into focused modules to enhance maintainability and clarity."
)


def test_two_different_modules_are_not_a_repeat() -> None:
    """The live specimen: same template, different work."""
    assert _repeats_recent(_SPLIT_ROUTER, (_SPLIT_SMART,)) == ""


def test_the_same_target_in_new_words_is_a_repeat() -> None:
    """Identity also STRENGTHENS the gate: a reworded goal about the same
    document is a repeat even when the token overlap is low."""
    old = (
        "Draft a proposal for the structure and rules of the "
        "'EVIDENCE_RECORD_SCHEMA.md' document, focusing on the schema for "
        "durable memory records, including provenance, timestamps, and status "
        "transitions."
    )
    new = "Extend EVIDENCE_RECORD_SCHEMA.md with retention tables"
    assert _repeats_recent(new, (old,)) == old


def test_the_same_module_is_still_a_repeat() -> None:
    assert _repeats_recent(_SPLIT_SMART, (_SPLIT_SMART,)) == _SPLIT_SMART


def test_a_bare_name_matches_a_path() -> None:
    """'model_router.py' and 'core/model_router.py' are the same work."""
    assert _repeats_recent(
        "Split model_router.py into focused modules",
        (_SPLIT_ROUTER,),
    ) == _SPLIT_ROUTER


def test_targetless_goals_still_fall_back_to_overlap() -> None:
    """No identity on either side: the old token judge stays in force."""
    old = "Analyze existing governance frameworks for autonomous organizations"
    new = "Analyze existing governance frameworks for autonomous organisations"
    assert _repeats_recent(new, (old,)) == old


def test_unrelated_targetless_goals_are_not_repeats() -> None:
    assert _repeats_recent(
        "Compare budget accounting practices in agent frameworks",
        ("Analyze existing governance frameworks for autonomous organizations",),
    ) == ""


def test_a_mixed_pair_falls_back_to_overlap() -> None:
    """One side names a target, the other does not — no identity comparison
    is possible, so the token judge decides, unchanged threshold and all."""
    near = (
        "Propose a plan to split the oversized module into focused modules "
        "to enhance maintainability and clarity"
    )  # Jaccard 0.81 against the smart_memory goal — a repeat
    far = "Propose a plan to split the oversized module into focused modules"
    # Jaccard 0.56 — below the untouched 0.6 threshold, not a repeat
    assert _repeats_recent(_SPLIT_SMART, (near,)) == near
    assert _repeats_recent(_SPLIT_SMART, (far,)) == ""
