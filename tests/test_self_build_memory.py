"""Tests for episodic journaling of self-build / self-apply outcomes.

Guards the guarantee the operator asked for: the agent must remember what it
tried and WHY it failed, in its own long-term (episodic) memory — not only in a
transient console log.
"""
from __future__ import annotations

from cli.self_build_memory import (
    build_self_build_episode,
    record_self_build_episode,
)


class _FakeStore:
    def __init__(self) -> None:
        self.saved: list = []

    def save(self, episode) -> None:
        self.saved.append(episode)


class _FakeAgent:
    def __init__(self, store=None) -> None:
        self.episodic_store = store


def test_apply_rollback_is_recorded_as_failed_with_reason() -> None:
    result = {
        "proposal_id": "ain_abc",
        "status": "rolled_back",
        "reason": "targeted tests failed",
        "files_changed": ["core/campaign.py", "core/campaign_support.py"],
        "rollback_status": "restored",
    }
    ep = build_self_build_episode("self-apply-run", result)
    assert ep is not None
    assert ep.outcome == "failed"
    # The WHY must be captured verbatim so it can be recalled later.
    assert "targeted tests failed" in ep.summary
    assert "core/campaign_support.py" in ep.summary
    assert "restored" in ep.summary
    # Protected from eviction + discoverable.
    assert "lesson" in ep.tags
    assert "self-apply-run" in ep.tags
    assert "self-build" in ep.tags


def test_apply_commit_is_recorded_as_success() -> None:
    result = {
        "proposal_id": "ain_ok",
        "status": "committed_local",
        "reason": "applied and committed on branch",
        "files_changed": ["core/campaign.py"],
        "rollback_status": "none",
    }
    ep = build_self_build_episode("self-apply-run", result)
    assert ep is not None
    assert ep.outcome == "success"
    assert "committed_local" in ep.summary
    # rollback=none is noise → not surfaced.
    assert "rollback" not in ep.summary


def test_produce_critic_veto_records_veto_reasons() -> None:
    result = {
        "status": "critic_veto",
        "reason": "empty generated content; confidence 0.00 below threshold 0.60",
        "target_path": "core/planner.py",
        "veto_reasons": ["empty generated content", "no targeted tests specified"],
    }
    ep = build_self_build_episode("self-build-produce", result)
    assert ep is not None
    assert ep.outcome == "failed"
    assert "core/planner.py" in ep.goal
    assert "empty generated content" in ep.summary
    assert "no targeted tests specified" in ep.summary
    assert "core/planner.py" in ep.tags


def test_produce_proposed_is_success() -> None:
    result = {
        "status": "proposed",
        "reason": "Split the campaign engine into a thin wrapper plus a module.",
        "target_path": "core/campaign.py",
        "approval_id": "ain_1b2d",
    }
    ep = build_self_build_episode("self-build-produce", result)
    assert ep is not None
    assert ep.outcome == "success"


def test_produce_no_patch_is_partial() -> None:
    result = {"status": "no_patch", "reason": "no grounded backlog candidate"}
    ep = build_self_build_episode("self-build-produce", result)
    assert ep is not None
    assert ep.outcome == "partial"


def test_unknown_status_defaults_to_partial() -> None:
    ep = build_self_build_episode("self-apply-run", {"status": "weird", "reason": "x"})
    assert ep is not None
    assert ep.outcome == "partial"


def test_record_persists_to_store() -> None:
    store = _FakeStore()
    agent = _FakeAgent(store)
    ok = record_self_build_episode(
        agent,
        kind="self-apply-run",
        result={"status": "rolled_back", "reason": "targeted tests failed",
                "proposal_id": "ain_z"},
    )
    assert ok is True
    assert len(store.saved) == 1
    assert store.saved[0].outcome == "failed"


def test_record_is_noop_without_store() -> None:
    # Missing / None episodic_store must not raise and must report no write.
    assert record_self_build_episode(_FakeAgent(None), kind="self-apply-run",
                                     result={"status": "rolled_back"}) is False
    assert record_self_build_episode(object(), kind="self-build-produce",
                                     result={"status": "no_patch"}) is False


def test_record_swallows_store_errors() -> None:
    class _Boom:
        def save(self, episode) -> None:  # noqa: ARG002
            raise RuntimeError("disk full")

    agent = _FakeAgent(_Boom())
    # Journaling must never break the operator command.
    assert record_self_build_episode(agent, kind="self-apply-run",
                                     result={"status": "rolled_back"}) is False
