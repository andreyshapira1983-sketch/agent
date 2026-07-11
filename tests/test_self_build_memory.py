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


# ── recent_self_build_lessons (recall past failures before a new attempt) ──────


class _FakeEpisode:
    def __init__(self, summary: str) -> None:
        self.summary = summary


class _RecallStore:
    def __init__(self, episodes) -> None:
        self._episodes = list(episodes)
        self.queried_tags = None

    def search_by_tags(self, tags, *, limit: int = 5):
        self.queried_tags = list(tags)
        return self._episodes[:limit]


def test_recent_lessons_returns_past_failure_summaries() -> None:
    from cli.self_build_memory import recent_self_build_lessons

    store = _RecallStore([
        _FakeEpisode("self-apply rolled_back: targeted tests failed: "
                     "ImportError: cannot import name '_ToolRun'"),
    ])
    agent = _FakeAgent(store)
    lessons = recent_self_build_lessons(agent, "core/self_repair.py")
    assert len(lessons) == 1
    assert "_ToolRun" in lessons[0]
    # Query is scoped to failed self-build lessons for the exact target.
    assert "self-build" in store.queried_tags
    assert "failed" in store.queried_tags
    assert "core/self_repair.py" in store.queried_tags


def test_recent_lessons_is_empty_without_store_or_target() -> None:
    from cli.self_build_memory import recent_self_build_lessons

    assert recent_self_build_lessons(_FakeAgent(None), "core/x.py") == []
    assert recent_self_build_lessons(_FakeAgent(_RecallStore([])), "") == []


def test_recent_lessons_swallows_store_errors() -> None:
    from cli.self_build_memory import recent_self_build_lessons

    class _Boom:
        def search_by_tags(self, tags, *, limit: int = 5):  # noqa: ARG002
            raise RuntimeError("disk full")

    assert recent_self_build_lessons(_FakeAgent(_Boom()), "core/x.py") == []


# ── recently_vetoed_self_build_targets (cooldown after a critic veto) ─────────


class _TaggedEpisode:
    def __init__(self, tags) -> None:
        self.tags = tuple(tags)


class _TagFilterStore:
    """search_by_tags with the real AND semantics (all required tags present)."""

    def __init__(self, episodes) -> None:
        self._episodes = list(episodes)
        self.queried_tags = None

    def search_by_tags(self, tags, *, limit: int = 5):
        self.queried_tags = list(tags)
        required = frozenset(tags)
        return [e for e in self._episodes if required <= set(e.tags)][:limit]


def test_recently_vetoed_targets_extracts_path_from_veto_episodes() -> None:
    from cli.self_build_memory import recently_vetoed_self_build_targets

    store = _TagFilterStore([
        _TaggedEpisode(("self-build", "critic_veto", "failed", "core/model_router.py")),
        _TaggedEpisode(("self-build", "proposed", "success", "core/redaction.py")),
        _TaggedEpisode(("self-build", "critic_veto", "failed", "docs/self_build.md")),
    ])
    got = recently_vetoed_self_build_targets(_FakeAgent(store))
    assert got == frozenset({"core/model_router.py", "docs/self_build.md"})
    # Scoped to vetoed self-build episodes only.
    assert "self-build" in store.queried_tags
    assert "critic_veto" in store.queried_tags


def test_recently_vetoed_targets_ignores_non_path_tags() -> None:
    from cli.self_build_memory import recently_vetoed_self_build_targets

    store = _TagFilterStore([
        # a veto episode with no path-like tag yields nothing (not a crash).
        _TaggedEpisode(("self-build", "critic_veto", "failed")),
    ])
    assert recently_vetoed_self_build_targets(_FakeAgent(store)) == frozenset()


def test_recently_vetoed_targets_is_empty_without_store() -> None:
    from cli.self_build_memory import recently_vetoed_self_build_targets

    assert recently_vetoed_self_build_targets(_FakeAgent(None)) == frozenset()


def test_recently_vetoed_targets_swallows_store_errors() -> None:
    from cli.self_build_memory import recently_vetoed_self_build_targets

    class _Boom:
        def search_by_tags(self, tags, *, limit: int = 20):  # noqa: ARG002
            raise RuntimeError("disk full")

    assert recently_vetoed_self_build_targets(_FakeAgent(_Boom())) == frozenset()
