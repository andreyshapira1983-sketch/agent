"""The reasoning↔action sensor's blind spots, pinned as measured facts.

Knowledge transfer only (operator ruling 2026-08-19: «перенести только
измерение… поведение не меняем»). The detector keeps behaving exactly as
before; what lands here is what a live measurement showed about the value
of its output, so nobody reads its signal as a defect again without
suspecting the sensor first.

Measured over every `planner` event in logs/ — 268 real turns carrying both
a reasoning text and a plan: the detector FIRES on 190 of them (71 %). That
is a firing rate, not an error rate: two accusations were read by hand and
both were false, no ground truth was labelled, and the true false-positive
rate over the other 188 is UNKNOWN. The case against enforcement rests on
the structural half below, which needs no such rate. First measured 2026-08-05 in
wip/mir-015-structural-justification (108 turns, 44 firings), a branch that
never merged; re-measured against main before this file was written.

These tests pin the STRUCTURAL half — the part that is a fact about the
code rather than about a sample of logs. When one of them fails, the sensor
was changed: update the module docstring's measurement in the same commit,
because a silently improved sensor makes every past reading unreadable.
"""
from __future__ import annotations

import re
from pathlib import Path

from core.reasoning_action_check import _TOOL_KEYWORDS

_REPO = Path(__file__).resolve().parents[1]


def _registered_tools() -> set[str]:
    """Tool names the agent actually ships, read from its own registry."""
    src = (_REPO / "app" / "bootstrap.py").read_text(encoding="utf-8")
    classes = set(re.findall(r"registry\.register\((\w+)\(", src))
    names: set[str] = set()
    for path in _REPO.glob("tools/*.py"):
        text = path.read_text(encoding="utf-8", errors="replace")
        for cls in re.findall(r"^class (\w+)\(Tool\)", text, re.MULTILINE):
            if cls not in classes:
                continue
            m = re.search(r'^\s+name\s*=\s*"([\w_]+)"', text, re.MULTILINE)
            if m:
                names.add(m.group(1))
    return names


def test_the_table_does_not_cover_the_registry() -> None:
    """Tools the agent can plan but the sensor cannot recognise: planning one
    is flagged 'unjustified' by construction, not by any real defect."""
    blind = _registered_tools() - set(_TOOL_KEYWORDS)
    assert blind, (
        "the keyword table now covers every registered tool — the measured "
        "blind spot is gone; re-measure and update the module docstring"
    )
    assert {"file_write", "python_probe", "lesson_provenance"} <= blind, (
        f"the measured blind tools changed; now blind: {sorted(blind)}"
    )


def test_the_table_names_tools_that_do_not_exist() -> None:
    """The reverse direction accuses the planner of omitting a step it cannot
    produce: this entry matches no registered tool. (`spawn_subagent` was
    wrongly named a phantom in the first draft of this record and is in fact
    registered — the error was caught by this very test.)"""
    phantom = set(_TOOL_KEYWORDS) - _registered_tools()
    assert phantom == {"self_repair"}, (
        f"the measured phantom entry changed; now phantom: {sorted(phantom)}"
    )


def test_the_file_read_keyword_still_carries_its_trailing_space() -> None:
    """The single most quoted defect of the table: `"read "` cannot match
    "reading core/loop.py", so a plainly argued read is called unjustified."""
    assert "read " in _TOOL_KEYWORDS["file_read"]


def test_the_measurement_is_recorded_where_the_code_lives() -> None:
    """A number in a chat log is not a record. The module must carry it."""
    doc = (_REPO / "core" / "reasoning_action_check.py").read_text(encoding="utf-8")
    head = doc[:doc.find('"""', 3)]
    assert "268" in head and "190" in head, (
        "the module docstring lost its measurement — restore it or re-measure"
    )
    assert "observational" in head
