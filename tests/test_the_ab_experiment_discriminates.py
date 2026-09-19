"""The differentiating experiment: lesson OFF vs ON, everything else equal.

Operator design (2026-08-17): «одинаковый класс задачи; A: lesson не
доступен; B: lesson доступен; остальные значимые условия максимально
одинаковы → сравнить defect outcome». One clean discrimination is enough
for the first proof of MECHANISM; observational receipts accumulate after.
The verdict vocabulary is calibrated, never triumphant: effect_observed /
no_discrimination / lesson_insufficient / inverted — and a generation the
instrument never examined (parse failure) is excluded, not counted.
"""
from __future__ import annotations

import json
from pathlib import Path

from core.lesson_ab_experiment import run_lesson_ab_experiment

_CLEAN_TEST = [
    "from core.causal_lesson import Observation",
    "",
    "def test_clean():",
    "    obs = Observation(episode_id='e', trace_id='t', run_id='r')",
    "    assert obs.episode_id == 'e'",
]
_PHANTOM_TEST = [
    "from core.causal_lesson import Observation",
    "",
    "def test_phantom():",
    "    obs = Observation(episode_id='e', trace_id='t', run_id='r', ghost=1)",
    "    assert obs.episode_id == 'e'",
]
_BROKEN_TEST = ["def broken(:"]


class _ScriptedLLM:
    """Clean when the lesson rides the prompt, phantom when it does not."""

    def __init__(self, off_lines=None, on_lines=None) -> None:
        self._off = off_lines or _PHANTOM_TEST
        self._on = on_lines or _CLEAN_TEST

    def complete(self, *, system: str, user: str, **_kw) -> str:
        lines = self._on if "LESSON" in system else self._off
        return json.dumps({
            "task_title": "t", "task_summary": "s",
            "impl_path": "tools/x.py", "test_path": "tests/test_x.py",
            "test_lines": lines, "confidence": 0.9,
        })


def _saved_lesson(tmp_path: Path) -> str:
    from tests.test_the_measurement_has_its_own_writer import _saved_lesson

    return _saved_lesson(tmp_path)


def _run(tmp_path: Path, llm, k: int = 3):
    key = _saved_lesson(tmp_path)
    report = run_lesson_ab_experiment(
        tmp_path, llm, lesson_key=key, k=k,
        impl_path="tools/x.py", quote="# TODO: x",
        evidence_ref="tools/x.py:1", current_content="def x():\n    pass\n",
    )
    return key, report


def test_a_clean_discrimination_is_effect_observed(tmp_path: Path) -> None:
    _key, report = _run(tmp_path, _ScriptedLLM())
    assert report.off.defects == 3 and report.on.defects == 0
    assert report.verdict == "effect_observed"
    # the experiment signs its own measurement, keyed to the lesson
    rows = (tmp_path / "data" / "lesson_measurements.jsonl").read_text(
        encoding="utf-8")
    assert "ab_experiment" in rows and report.measurement_id in rows


def test_both_arms_clean_is_no_discrimination(tmp_path: Path) -> None:
    _key, report = _run(tmp_path, _ScriptedLLM(off_lines=_CLEAN_TEST))
    assert report.verdict == "no_discrimination"


def test_both_arms_dirty_is_lesson_insufficient(tmp_path: Path) -> None:
    _key, report = _run(tmp_path, _ScriptedLLM(on_lines=_PHANTOM_TEST))
    assert report.verdict == "lesson_insufficient"


def test_clean_off_dirty_on_is_inverted_and_said_so(tmp_path: Path) -> None:
    _key, report = _run(tmp_path, _ScriptedLLM(
        off_lines=_CLEAN_TEST, on_lines=_PHANTOM_TEST))
    assert report.verdict == "inverted"


def test_an_unparsed_generation_is_excluded_not_counted(tmp_path: Path) -> None:
    """The instrument never ran — the generation is not a measurement."""
    _key, report = _run(tmp_path, _ScriptedLLM(off_lines=_BROKEN_TEST))
    assert report.off.measured == 0
    assert report.verdict == "insufficient_measurements"


def test_the_lesson_actually_rides_exactly_one_arm(tmp_path: Path) -> None:
    """Both prompts are recorded; the ON arm carries the lesson block, the
    OFF arm does not — the experiment's own honesty check."""
    seen: list[str] = []

    class _SpyLLM(_ScriptedLLM):
        def complete(self, *, system: str, user: str, **kw) -> str:
            seen.append(system)
            return super().complete(system=system, user=user, **kw)

    _run(tmp_path, _SpyLLM(), k=2)
    with_lesson = sum(1 for s in seen if "LESSON" in s)
    assert with_lesson == 2 and len(seen) == 4
