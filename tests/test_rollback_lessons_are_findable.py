"""A rolled-back patch must teach the next attempt on the same file.

`recent_self_build_lessons(agent, target)` exists so the Builder is warned not
to repeat a mistake it already made on this exact file. It searches episodes
tagged with ALL of `self-build`, `failed` and the file path.

A `self-apply-run` episode carried no path tag at all — `build_self_build_episode`
reads `result["target_path"]`, which the apply result does not have; it has
`files_changed`. So a rollback was banked as a lesson nobody could ever find.

Measured 2026-08-04 against the agent's own `data/episodic_memory.jsonl`:

    rolled_back -> ['self-build', 'lesson', 'self-apply-run', 'rolled_back', 'failed']
    proposed    -> [..., 'proposed', 'success', 'cli/intent_bridge.py']

and `recent_self_build_lessons(agent, "cli/intent_bridge.py")` returned **0**.
Two of the three live rollbacks are the same file, the same test and the same
assertion — `test_ambiguous_capability_check_still_uses_model_veto`,
"assert True is False". The agent repeated its own mistake because the lesson
was invisible to the lookup that exists precisely to prevent that.
"""
from __future__ import annotations

from typing import Any

from core.self_build_memory import build_self_build_episode, recent_self_build_lessons


class _Episode:
    def __init__(self, tags: tuple[str, ...], summary: str):
        self.tags = tags
        self.summary = summary


class _Store:
    def __init__(self, episodes: list[Any]):
        self.episodes = episodes

    def search_by_tags(self, tags: list[str], limit: int = 3) -> list[Any]:
        wanted = set(tags)
        return [e for e in self.episodes if wanted.issubset(set(e.tags))][:limit]


class _Agent:
    def __init__(self, episodes: list[Any]):
        self.episodic_store = _Store(episodes)


_ROLLBACK = {
    "status": "rolled_back",
    "reason": (
        "targeted tests failed: "
        "tests=tests/test_intent_bridge_capability_check.py::"
        "test_ambiguous_capability_check_still_uses_model_veto; "
        "AssertionError: assert True is False"
    ),
    "approval_id": "ain_212a",
    "files_changed": [
        "cli/intent_bridge.py",
        "tests/test_intent_bridge_capability_check.py",
    ],
    "rollback_status": "restored",
}


def test_a_rollback_is_tagged_with_the_files_it_touched():
    episode = build_self_build_episode("self-apply-run", _ROLLBACK)

    assert "cli/intent_bridge.py" in episode.tags
    assert "tests/test_intent_bridge_capability_check.py" in episode.tags


def test_the_next_attempt_on_that_file_finds_the_lesson():
    """The whole point: the Builder is told what broke here last time."""
    episode = build_self_build_episode("self-apply-run", _ROLLBACK)
    agent = _Agent([_Episode(tuple(episode.tags), episode.summary)])

    lessons = recent_self_build_lessons(agent, "cli/intent_bridge.py")

    assert lessons, "the rollback lesson is invisible to the next attempt"
    assert "targeted tests failed" in lessons[0]


def test_a_successful_apply_is_tagged_too_but_is_not_a_failure_lesson():
    """Tagging is about the file, the lesson filter is about the outcome."""
    episode = build_self_build_episode("self-apply-run", {
        "status": "committed_local",
        "reason": "targeted tests green",
        "files_changed": ["cli/intent_bridge.py"],
    })
    agent = _Agent([_Episode(tuple(episode.tags), episode.summary)])

    assert "cli/intent_bridge.py" in episode.tags
    assert recent_self_build_lessons(agent, "cli/intent_bridge.py") == []


def test_an_apply_with_no_files_still_banks_an_episode():
    episode = build_self_build_episode("self-apply-run", {
        "status": "blocked",
        "reason": "approval required",
    })

    assert episode is not None
    assert "self-build" in episode.tags


def test_a_rollback_banked_before_the_fix_is_still_found():
    """The three already in the live store must not stay invisible forever.

    Their summary names the files even though their tags do not, and rewriting
    the agent's memory to add tags is not something a code fix should do.
    """
    legacy = _Episode(
        ("self-build", "lesson", "self-apply-run", "rolled_back", "failed"),
        "self-apply rolled_back: targeted tests failed: "
        "tests=tests/test_intent_bridge_capability_check.py; "
        "AssertionError: assert True is False "
        "(files=['cli/intent_bridge.py', "
        "'tests/test_intent_bridge_capability_check.py']; rollback=restored)",
    )

    lessons = recent_self_build_lessons(_Agent([legacy]), "cli/intent_bridge.py")

    assert lessons, "a rollback banked before the path tag existed stays lost"


def test_an_unrelated_failure_is_not_dragged_in_by_the_fallback():
    """Matching on the summary must not turn into matching on anything."""
    other = _Episode(
        ("self-build", "lesson", "self-apply-run", "rolled_back", "failed"),
        "self-apply rolled_back: targeted tests failed "
        "(files=['core/planner.py']; rollback=restored)",
    )

    assert recent_self_build_lessons(_Agent([other]), "cli/intent_bridge.py") == []


def test_the_tag_list_does_not_balloon_on_a_wide_patch():
    """A split can touch many files; tags are for lookup, not for a manifest."""
    episode = build_self_build_episode("self-apply-run", {
        "status": "rolled_back",
        "reason": "targeted tests failed",
        "files_changed": [f"core/module_{i}.py" for i in range(40)],
    })

    path_tags = [t for t in episode.tags if str(t).endswith(".py")]
    assert len(path_tags) <= 10, f"{len(path_tags)} path tags on one episode"
