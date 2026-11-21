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


def test_the_same_module_is_still_a_repeat() -> None:
    assert _repeats_recent(_SPLIT_SMART, (_SPLIT_SMART,)) == _SPLIT_SMART
