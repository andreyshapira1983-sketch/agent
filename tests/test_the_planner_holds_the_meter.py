"""The planner holds the provenance meter: causal-use questions read receipts.

Live replays 2026-08-17: with the route fixed, the agent answered the exam
question from raw logs — recurrence it could show, causal use it honestly
could not, because the receipt chain was reachable only through an operator
command. The lab taught the pattern (tools existed but were not in the
planner's map — never chosen); the meter now follows it: a registry tool,
a PLANNER_SYSTEM entry with the epistemic rule (NOT PROVEN is a finished
honest answer, not a failure), and a sanitizer contract.
"""
from __future__ import annotations

from pathlib import Path

from core.planner_prompt import PLANNER_SYSTEM
from tools.lesson_provenance_tool import LessonProvenanceTool


def _saved_lesson(tmp_path: Path) -> str:
    from tests.test_the_measurement_has_its_own_writer import _saved_lesson

    return _saved_lesson(tmp_path)


def test_the_tool_reads_the_chain(tmp_path: Path) -> None:
    key = _saved_lesson(tmp_path)
    out = LessonProvenanceTool(workspace_root=str(tmp_path)).run()
    assert out["reports"], "no chains rendered"
    mine = next(r for r in out["reports"] if r["lesson_key"] == key)
    assert mine["verdict"] == "CAUSAL USE NOT PROVEN"
    assert {lk["name"] for lk in mine["links"]} == {
        "derived_from", "injected", "acted", "measured"}
    assert key in out["rendered"]


def test_one_key_filters_the_reading(tmp_path: Path) -> None:
    key = _saved_lesson(tmp_path)
    out = LessonProvenanceTool(workspace_root=str(tmp_path)).run(
        lesson_key=key)
    assert [r["lesson_key"] for r in out["reports"]] == [key]


def test_an_unknown_key_is_an_honest_not_found(tmp_path: Path) -> None:
    out = LessonProvenanceTool(workspace_root=str(tmp_path)).run(
        lesson_key="cclaim_ghost")
    assert out["reports"][0]["state"] == "not_found"
    assert out["reports"][0]["verdict"] == "CAUSAL USE NOT PROVEN"


def test_an_empty_store_is_a_reading_not_an_error(tmp_path: Path) -> None:
    out = LessonProvenanceTool(workspace_root=str(tmp_path)).run()
    assert out["reports"] == []
    assert "no lessons" in out["rendered"]


def test_the_tool_is_in_the_agents_hands() -> None:
    """Registered at birth — the lab's lesson: a tool outside the registry
    does not exist for the loop."""
    from app import bootstrap

    src = Path(bootstrap.__file__).read_text(encoding="utf-8")
    assert "LessonProvenanceTool(" in src


def test_the_meter_is_in_the_planners_map() -> None:
    assert "lesson_provenance(" in PLANNER_SYSTEM


def test_the_map_teaches_receipts_not_prose() -> None:
    idx = PLANNER_SYSTEM.find("lesson_provenance(")
    section = PLANNER_SYSTEM[idx:idx + 1400]
    assert "NOT PROVEN" in section
    assert "receipt" in section.lower()


def test_the_sanitizer_admits_the_meter() -> None:
    """The lab's live lesson (проба №3): a planned step died with «no
    sanitiser, dropped» — the hand had no pass. The meter gets one at birth."""
    from core.step_sanitizer import sanitize_step

    warnings: list[str] = []
    step = sanitize_step(
        "lesson_provenance", {"lesson_key": "cclaim_x"}, None, 0, warnings)
    assert step is not None
    assert step["tool"] == "lesson_provenance"
    assert step["arguments"] == {"lesson_key": "cclaim_x"}
    assert not warnings


def test_the_sanitizer_drops_a_novel_argument() -> None:
    from core.step_sanitizer import sanitize_step

    warnings: list[str] = []
    step = sanitize_step(
        "lesson_provenance",
        {"lesson_key": "k", "write_to": "/etc"}, None, 0, warnings)
    assert step is not None
    assert step["arguments"] == {"lesson_key": "k"}


def test_the_sanitizer_clamps_a_monster_key() -> None:
    from core.step_sanitizer import sanitize_step

    warnings: list[str] = []
    step = sanitize_step(
        "lesson_provenance", {"lesson_key": "x" * 5000}, None, 0, warnings)
    assert step is None
    assert warnings
