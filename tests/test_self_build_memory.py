"""Tests for episodic journaling of self-build / self-apply outcomes.

Guards the guarantee the operator asked for: the agent must remember what it
tried and WHY it failed, in its own long-term (episodic) memory — not only in a
transient console log.
"""
from __future__ import annotations

from cli.self_build_memory import (
    build_self_build_episode,
)


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


# ── recent_self_build_lessons (recall past failures before a new attempt) ──────


class _FakeEpisode:
    def __init__(self, summary: str) -> None:
        self.summary = summary


class _RecallStore:
    def __init__(self, episodes) -> None:
        self._episodes = list(episodes)
        self.queried_tags = None
        # Since 2026-08-04 recall may issue a SECOND, broader query to reach
        # rollbacks banked before path tags existed. Keeping only the last one
        # would hide the targeted query that must still happen first.
        self.all_queries: list[list[str]] = []

    def search_by_tags(self, tags, *, limit: int = 5):
        self.queried_tags = list(tags)
        self.all_queries.append(list(tags))
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
    # The targeted query still happens, and it is the FIRST one: the broader
    # legacy sweep only runs when the exact-tag search came back short.
    assert store.all_queries[0] == ["self-build", "failed", "core/self_repair.py"]
    assert all("self-build" in q and "failed" in q for q in store.all_queries)


def test_recent_lessons_is_empty_without_store_or_target() -> None:
    from cli.self_build_memory import recent_self_build_lessons

    assert recent_self_build_lessons(_FakeAgent(None), "core/x.py") == []
    assert recent_self_build_lessons(_FakeAgent(_RecallStore([])), "") == []


def test_recent_lessons_swallows_store_errors() -> None:
    from cli.self_build_memory import recent_self_build_lessons

    class _Boom:
        def search_by_tags(self, tags, *, limit: int = 5):
            raise RuntimeError("disk full")

    assert recent_self_build_lessons(_FakeAgent(_Boom()), "core/x.py") == []


# ── recently_vetoed_self_build_targets (cooldown after a critic veto) ─────────


class _TaggedEpisode:
    def __init__(self, tags, summary: str = "") -> None:
        self.tags = tuple(tags)
        # Real episodes always carry the veto reason (`build_self_build_episode`
        # composes it), and since 2026-08-04 the cooldown reads it: a veto
        # caused by the builder's reply failing to parse is not a verdict on the
        # target. A fixture with no reason at all is not a real shape.
        self.summary = summary


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

    _judged = "self-build critic_veto: confidence 0.02 below threshold 0.60"
    store = _TagFilterStore([
        _TaggedEpisode(("self-build", "critic_veto", "failed", "core/model_router.py"),
                       _judged),
        _TaggedEpisode(("self-build", "proposed", "success", "core/redaction.py")),
        _TaggedEpisode(("self-build", "critic_veto", "failed", "docs/self_build.md"),
                       _judged),
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


def test_recently_vetoed_targets_swallows_store_errors() -> None:
    from cli.self_build_memory import recently_vetoed_self_build_targets

    class _Boom:
        def search_by_tags(self, tags, *, limit: int = 20):
            raise RuntimeError("disk full")

    assert recently_vetoed_self_build_targets(_FakeAgent(_Boom())) == frozenset()
