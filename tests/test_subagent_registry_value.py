"""TD-033 — human value reviews projected onto subagent registry scoring.

Hermetic: no git, pytest subprocess, LLM, provider, or network. The registry
writes to a tmp path; ``reconcile_value_reviews`` is fed plain
``{item_id: verdict}`` snapshots (as ``ValueReviewLog.effective_by_item_id``
would produce).
"""
from __future__ import annotations

import json
from pathlib import Path

from core.subagent_registry import (
    _MIN_JUDGED_FOR_RECOMMENDATION as _MIN_JUDGED,
)
from core.subagent_registry import (
    SubagentRegistry,
)


def _reg(tmp_path: Path) -> SubagentRegistry:
    return SubagentRegistry.load(tmp_path)


# ── scoring ──────────────────────────────────────────────────────────────────


def test_accepted_beats_committed_local_alone(tmp_path: Path) -> None:
    """A human ``accepted`` (full weight) must outscore committed_local (0.25)."""
    committed = _reg(tmp_path / "a")
    committed.roles["builder"].invocations = 1
    committed.record_lane_outcome("builder", "committed_local")
    committed_useful = committed.roles["builder"].usefulness_score

    accepted = _reg(tmp_path / "b")
    accepted.roles["builder"].invocations = 1
    accepted.reconcile_value_reviews({"ain_1": "accepted"})
    accepted_useful = accepted.roles["builder"].usefulness_score

    assert accepted_useful > committed_useful


# ── projection semantics ─────────────────────────────────────────────────────


def test_unknown_verdict_is_ignored(tmp_path: Path) -> None:
    reg = _reg(tmp_path)
    reg.reconcile_value_reviews({"ain_1": "not_a_verdict", "ain_2": "accepted"})
    assert reg.roles["builder"].confirmed_value == 1
    total = sum(r.value_rejected for r in reg.roles.values())
    assert total == 0


# ── persistence / tolerance ──────────────────────────────────────────────────


def test_old_json_without_new_fields_loads_with_zero_defaults(tmp_path: Path) -> None:
    path = tmp_path / "data" / "subagent_registry.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    # A pre-TD-033 ledger: no confirmed_value / value_rejected keys.
    path.write_text(
        json.dumps(
            {"roles": {"builder": {"invocations": 3, "successes": 2}}}
        ),
        encoding="utf-8",
    )
    reg = SubagentRegistry.load(tmp_path)
    assert reg.roles["builder"].confirmed_value == 0
    assert reg.roles["builder"].value_rejected == 0
    assert reg.roles["builder"].invocations == 3


# ── status invariant + recommendation gate (TD-034 P0 fix) ──────────────────


def test_value_rejected_counts_as_judged_evidence(tmp_path: Path) -> None:
    """Before TD-034 the gate excluded value_rejected, so recommendation stayed
    ``keep`` regardless of how many human rejections landed. Guard against that
    regression: with no producer-stage events at all, pure human rejections must
    still be enough judged evidence to open the gate."""
    reg = _reg(tmp_path)
    reg.roles["builder"].invocations = 8
    reg.reconcile_value_reviews(
        {f"ain_{i}": "rejected_low_value" for i in range(_MIN_JUDGED)}
    )
    assert reg.roles["builder"].recommendation != "keep"
