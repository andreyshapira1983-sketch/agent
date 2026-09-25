"""Reflection names a weak spot; can the agent tell whether it is real?

Measured on live traffic 2026-08-20, then **re-measured after the first
reading turned out to be wrong** — the correction is the useful part.

What the logs show: of the twelve weak spots reflection's lessons named, ten
do not exist as files — core/reasoning.py, core/citation.py,
core/user_contract.py and others, plausible module names the model invented.
Only tools/file_read.py and core/__init__.py were real.

What that does NOT show: that the current tree accepts them. Every one of the
ten is from 2026-08-15, the day commit `010bdd2` added `_checked_focus_area`
to core/reflection.py — "a lesson may not point at a file that does not
exist". The first test below proves by intervention that a path-shaped
phantom is now blanked and its `repair` intent downgraded to `monitor`.
Historical logs, current code: a different question.

The residual is narrower and still live. The guard can only question a name
that LOOKS like a path. `"memory subsystem"` is unresolvable by shape, passes
through untouched, and reaches the planner — which answers it with very
nearly the default source list. That is what the strict xfail banked.

CLOSED 2026-09-25, by two published practices, opened before the change:
reflection grounding (arXiv 2603.07670, section 4.3: a reflection must cite
concrete episodes) and BM25 file retrieval from a natural-language problem
(SWE-bench, section 4.1). A weak spot is studied only with its evidence (the
error pattern the lesson came from); that evidence is the BM25 query for the
code files to read (`core/weak_spot_retrieval.py`). A topic with no evidence
is not studied at all, so the default list no longer stands in for it.
"""
from __future__ import annotations

from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]


def _plan_for(focus_areas: list[str], workspace: Path):
    from core.learning_planner import LearningPlanner

    goal = "reflection: study weak areas — " + ", ".join(focus_areas)
    return LearningPlanner().plan(workspace=workspace, goal=goal, limit=5)


def test_a_real_weak_spot_reaches_the_plan() -> None:
    """Boundary pin: when the name is real the signal does cross."""
    plan = _plan_for(["tools/file_read.py"], _REPO)
    assert "tools/file_read.py" in list(plan.source_paths)


def test_a_path_shaped_phantom_is_refused_before_it_becomes_a_goal() -> None:
    """The half that is fixed, proven at the guard rather than by reading it."""
    from core.reflection import _checked_focus_area

    assert not (_REPO / "core/reasoning.py").is_file(), (
        "core/reasoning.py exists now — re-measure before trusting this test"
    )
    assert _checked_focus_area("core/reasoning.py", "repair") == ("", "monitor")
    assert _checked_focus_area("tools/file_read.py", "repair") == (
        "tools/file_read.py",
        "repair",
    )


def _lesson(focus: str, pattern=None):
    from core.reflection import Lesson

    return Lesson(insight="x", action="learn_more", focus_area=focus, pattern=pattern)


def _timeout_pattern():
    from core.reflection import ErrorPattern

    return ErrorPattern(event_type="tool_result_error", tool_name="web_fetch",
                        sample_message="ReadTimeout: HTTPSConnectionPool read timed out",
                        count=3, trace_ids=["trace_a", "trace_b"])


def _engine(tmp_path: Path):
    from core.reflection import ReflectionEngine

    return ReflectionEngine(workspace=_REPO, persistent_memory=None, llm=None,
                            log_dir=tmp_path)


def test_a_weak_spot_with_evidence_changes_what_is_studied(tmp_path: Path) -> None:
    from core.reflection import ReflectionConfig

    warnings: list[str] = []
    plan = _engine(tmp_path)._build_learning_plan(
        [_lesson("network fetching reliability", _timeout_pattern())],
        ReflectionConfig(learning_limit=5), warnings)
    bare = list(_plan_for([], _REPO).source_paths)
    assert plan is not None and "tools/web_fetch.py" in plan.source_paths, plan
    assert list(plan.source_paths) != bare


def test_a_weak_spot_without_evidence_is_not_studied(tmp_path: Path) -> None:
    """A topic the model named with no error pattern behind it — the old case
    'planner interface design' — no longer gets the default list."""
    from core.reflection import ReflectionConfig

    warnings: list[str] = []
    plan = _engine(tmp_path)._build_learning_plan(
        [_lesson("planner interface design")], ReflectionConfig(learning_limit=5), warnings)
    assert plan is None
    assert any("without evidence" in w for w in warnings), warnings
