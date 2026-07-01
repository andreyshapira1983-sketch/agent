"""TD-033 — human value reviews projected onto subagent registry scoring.

Hermetic: no git, pytest subprocess, LLM, provider, or network. The registry
writes to a tmp path; ``reconcile_value_reviews`` is fed plain
``{item_id: verdict}`` snapshots (as ``ValueReviewLog.effective_by_item_id``
would produce).
"""
from __future__ import annotations

import json
from pathlib import Path

from core.subagent_registry import VERDICT_ROLE, SubagentRegistry


def _reg(tmp_path: Path) -> SubagentRegistry:
    return SubagentRegistry.load(tmp_path)


# ── attribution ──────────────────────────────────────────────────────────────


def test_accepted_credits_builder_confirmed_value(tmp_path: Path) -> None:
    reg = _reg(tmp_path)
    reg.reconcile_value_reviews({"ain_1": "accepted"})
    assert reg.roles["builder"].confirmed_value == 1
    assert reg.roles["builder"].value_rejected == 0
    # No other role touched.
    for rid in ("manager", "researcher", "critic", "reporter"):
        assert reg.roles[rid].confirmed_value == 0
        assert reg.roles[rid].value_rejected == 0


def test_rejected_low_value_penalises_builder_only(tmp_path: Path) -> None:
    reg = _reg(tmp_path)
    reg.reconcile_value_reviews({"ain_1": "rejected_low_value"})
    assert reg.roles["builder"].value_rejected == 1
    assert reg.roles["builder"].confirmed_value == 0
    for rid in ("manager", "researcher", "critic", "reporter"):
        assert reg.roles[rid].value_rejected == 0


def test_rejected_wrong_target_penalises_manager_only(tmp_path: Path) -> None:
    reg = _reg(tmp_path)
    reg.reconcile_value_reviews({"ain_1": "rejected_wrong_target"})
    assert reg.roles["manager"].value_rejected == 1
    for rid in ("builder", "researcher", "critic", "reporter"):
        assert reg.roles[rid].value_rejected == 0


def test_rejected_misleading_summary_penalises_reporter_only(tmp_path: Path) -> None:
    reg = _reg(tmp_path)
    reg.reconcile_value_reviews({"ain_1": "rejected_misleading_summary"})
    assert reg.roles["reporter"].value_rejected == 1
    for rid in ("builder", "researcher", "critic", "manager"):
        assert reg.roles[rid].value_rejected == 0


def test_rejected_risky_penalises_critic_only(tmp_path: Path) -> None:
    reg = _reg(tmp_path)
    reg.reconcile_value_reviews({"ain_1": "rejected_risky"})
    assert reg.roles["critic"].value_rejected == 1
    for rid in ("builder", "researcher", "manager", "reporter"):
        assert reg.roles[rid].value_rejected == 0


def test_verdict_role_map_is_complete() -> None:
    assert VERDICT_ROLE == {
        "accepted": "builder",
        "rejected_low_value": "builder",
        "rejected_wrong_target": "manager",
        "rejected_misleading_summary": "reporter",
        "rejected_risky": "critic",
    }


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


def test_value_rejected_lowers_scores(tmp_path: Path) -> None:
    reg = _reg(tmp_path)
    b = reg.roles["builder"]
    b.invocations = 2
    b.successes = 1
    reg.reconcile_value_reviews({"ain_1": "rejected_low_value"})
    b = reg.roles["builder"]
    # A rejection drags trust down relative to the success alone.
    assert b.value_rejected == 1
    assert b.trust_score < 1.0


# ── projection semantics ─────────────────────────────────────────────────────


def test_reconcile_is_idempotent(tmp_path: Path) -> None:
    reg = _reg(tmp_path)
    effective = {"ain_1": "accepted", "ain_2": "rejected_risky"}
    reg.reconcile_value_reviews(effective)
    first = {rid: (r.confirmed_value, r.value_rejected) for rid, r in reg.roles.items()}
    reg.reconcile_value_reviews(effective)
    second = {rid: (r.confirmed_value, r.value_rejected) for rid, r in reg.roles.items()}
    assert first == second
    assert reg.roles["builder"].confirmed_value == 1
    assert reg.roles["critic"].value_rejected == 1


def test_verdict_change_reprojects_without_double_count(tmp_path: Path) -> None:
    reg = _reg(tmp_path)
    reg.reconcile_value_reviews({"ain_1": "accepted"})
    assert reg.roles["builder"].confirmed_value == 1
    # Same item, latest verdict now rejected -> confirmed_value clears, one rejection.
    reg.reconcile_value_reviews({"ain_1": "rejected_low_value"})
    assert reg.roles["builder"].confirmed_value == 0
    assert reg.roles["builder"].value_rejected == 1


def test_reattribution_moves_penalty_between_roles(tmp_path: Path) -> None:
    reg = _reg(tmp_path)
    reg.reconcile_value_reviews({"ain_1": "rejected_low_value"})
    assert reg.roles["builder"].value_rejected == 1
    assert reg.roles["manager"].value_rejected == 0
    # Latest verdict re-attributes the same item to the manager.
    reg.reconcile_value_reviews({"ain_1": "rejected_wrong_target"})
    assert reg.roles["builder"].value_rejected == 0
    assert reg.roles["manager"].value_rejected == 1


def test_unknown_verdict_is_ignored(tmp_path: Path) -> None:
    reg = _reg(tmp_path)
    reg.reconcile_value_reviews({"ain_1": "not_a_verdict", "ain_2": "accepted"})
    assert reg.roles["builder"].confirmed_value == 1
    total = sum(r.value_rejected for r in reg.roles.values())
    assert total == 0


# ── persistence / tolerance ──────────────────────────────────────────────────


def test_counters_persist_and_reload(tmp_path: Path) -> None:
    reg = _reg(tmp_path)
    reg.reconcile_value_reviews({"ain_1": "accepted", "ain_2": "rejected_risky"})
    reloaded = SubagentRegistry.load(tmp_path)
    assert reloaded.roles["builder"].confirmed_value == 1
    assert reloaded.roles["critic"].value_rejected == 1


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


# ── status invariant ─────────────────────────────────────────────────────────


def test_many_rejections_change_recommendation_never_status(tmp_path: Path) -> None:
    reg = _reg(tmp_path)
    b = reg.roles["builder"]
    b.invocations = 10
    effective = {f"ain_{i}": "rejected_low_value" for i in range(8)}
    reg.reconcile_value_reviews(effective)
    b = reg.roles["builder"]
    assert b.value_rejected == 8
    # Recommendation may sour, but status stays whatever it was (advisory-only).
    assert b.status == "active"
    assert b.recommendation in {"keep", "watch", "pause", "retire"}


def test_empty_snapshot_resets_counters(tmp_path: Path) -> None:
    reg = _reg(tmp_path)
    reg.reconcile_value_reviews({"ain_1": "accepted"})
    assert reg.roles["builder"].confirmed_value == 1
    reg.reconcile_value_reviews({})
    assert reg.roles["builder"].confirmed_value == 0
    assert reg.roles["builder"].value_rejected == 0
