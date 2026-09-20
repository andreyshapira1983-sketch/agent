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


def test_the_table_covers_every_tool_that_can_be_planned() -> None:
    """2026-09-20: пять инструментов таблица не знала, и планирование каждого
    объявлялось «действием без довода» ПО ПОСТРОЕНИЮ — 187 обвинений одному
    `python_probe` за сутки. Слепое пятно закрыто; таблица осталась запасным
    путём для планов без доводов."""
    blind = _registered_tools() - set(_TOOL_KEYWORDS)
    assert not blind, f"таблица снова не знает инструментов: {sorted(blind)}"


def test_the_table_names_no_tool_that_does_not_exist() -> None:
    """Обратное направление обвиняло в пропуске шага, которого не бывает:
    запись `self_repair` не соответствовала ни одному инструменту реестра."""
    phantom = set(_TOOL_KEYWORDS) - _registered_tools()
    assert not phantom, f"таблица называет несуществующее: {sorted(phantom)}"


def test_the_step_justifies_itself_or_it_does_not() -> None:
    """Структурная замена: довод шага — у шага, а не в общей прозе."""
    from core.reasoning_action_check import check_by_rationale

    report = check_by_rationale([
        {"tool": "file_read", "rationale": "нужно прочитать раздел про рекурсию"},
        {"tool": "python_probe", "rationale": "проверю"},
        {"tool": "list_dir"},
    ])
    assert report.matched_tools == ("file_read",)
    assert set(report.unjustified_actions) == {"python_probe", "list_dir"}
    assert report.mentioned_but_not_planned == (), "сравнивать с прозой больше нечего"


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
    assert "1208" in head and "54%" in head, (
        "повторный замер 2026-09-20 потерян — его и заменяет структурная проверка"
    )
    assert "observational" in head


def test_the_planner_keeps_the_rationale_it_was_asked_for() -> None:
    """Корень 2026-09-20: планировщик пишет довод к каждому шагу, а конвейер
    его выбрасывал — санитайзер пересобирает шаг из инструмента и аргументов.
    Датчик после этого угадывал довод по общей прозе."""
    import inspect

    from core import planner as planner_mod
    from core import planner_prompt as prompt_mod

    planner = inspect.getsource(planner_mod)
    assert 'step.get("rationale")' in planner, "довод шага снова теряется в конвейере"
    assert '"rationale"' in inspect.getsource(prompt_mod), \
        "у планировщика перестали просить довод"
