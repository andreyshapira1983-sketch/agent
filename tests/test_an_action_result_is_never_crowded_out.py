"""Результат действия виден планировщику, сколько бы ни было прочитано.

Замер 2026-09-24, ночь кампании, три живых случая одного корня: в круге
наблюдения все выводы делили общий предел 16 000 знаков и шли по порядку,
чтения — первыми. Планировщик не увидел вердикт patch_check (22:47, «text
outside blocks») и пошёл искать его по журналам; конспекты писали «вывода
python_probe нет», хотя проба посчитала c/cg = 2.0 (22:43) и корень 1.5214
(22:45); удачный замер «44 эпизода» (23:28) заменила строка «не показан», и
в ответе осталось «замер не удался».
"""
from __future__ import annotations

from types import SimpleNamespace

from core.observation_round import format_observations

_PLAN = SimpleNamespace(steps=[])


def _reads(n: int) -> dict:
    return {f"file:doc{i}.md": {"tool": "file_read", "output": "x" * 7000} for i in range(n)}


def test_a_verdict_after_long_reads_is_shown() -> None:
    """Ломалось здесь: три длинных чтения съедали предел, вердикт шёл последним."""
    artifacts = _reads(3)
    artifacts["patch_check:proposals/selffix/x/edits.txt"] = {
        "tool": "patch_check",
        "output": {"verdict": "red", "why": "text outside blocks", "applied": False},
    }
    block = format_observations(_PLAN, artifacts)
    assert "text outside blocks" in block
    assert "patch_check:proposals/selffix/x/edits.txt] (не показан" not in block


def test_a_computation_is_shown_before_the_reads() -> None:
    artifacts = _reads(4)
    artifacts["python_probe:import math"] = {
        "tool": "python_probe", "output": {"stdout": "c/cg = 2.0\n", "exit_code": 0}}
    block = format_observations(_PLAN, artifacts)
    assert "c/cg = 2.0" in block
    assert block.index("python_probe:import math") < block.index("file:doc0.md")


def test_reads_keep_their_old_budget() -> None:
    """Ломка наоборот: прочитанное режется, как прежде, — лишнего не показываем."""
    block = format_observations(_PLAN, _reads(4))
    assert "file:doc3.md] (не показан" in block
