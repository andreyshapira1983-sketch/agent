"""Only `procedural_memory_update` ends an exam turn: the console tag «[PROC]» is the first
four letters of ANY event starting with «proc», and `procedure_feedback` prints before the end.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def _driver():
    spec = importlib.util.spec_from_file_location("exam_driver", _ROOT / "scripts" / "exam_driver.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_procedure_feedback_does_not_end_the_turn():
    lines = ["[PLN] plan  steps=2", "[PROC] procedure_feedback  episode_id=e1, offered=0"]
    assert not _driver().turn_is_over(lines, 0)


def test_any_other_proc_event_does_not_end_the_turn():
    assert not _driver().turn_is_over(["[PROC] process_restart  reason=probe"], 0)


def test_the_closing_event_ends_it():
    lines = ["[PROC] procedure_feedback  episode_id=e1", "[PROC] procedural_memory_update  episode_id=e1"]
    assert _driver().turn_is_over(lines, 0)
