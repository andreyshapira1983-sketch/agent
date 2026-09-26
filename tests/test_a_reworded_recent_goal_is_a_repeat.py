"""A recent goal said in other words is a repeat — compared without the note tail and cut.

Live run 26.09: 68 wordings of one task «find the code that writes 'goal first: …'»
over the runs. The old check compared the new goal with ledger goals that carried
the ~200-character note tail and were cut at 220 characters; similarity fell below
the threshold and every rewording passed. Replay on the real ledger: old rule 0 of
67 rewordings, new rule 44.
"""
from __future__ import annotations

import json
from pathlib import Path

from core.drive_goal import _problem, repeats_recent_goal

TAIL = (" Итог запиши файлом data/notes/20260926T113823_maintenance_need.md: источник (путь в рабочей "
        "папке или ссылка), дословная цитата строкой с «>», и проверка с числом (расчёт или python_probe). "
        "Пустые скобки-заглушки не годятся — конспект пишется после чтения.")
DONE = ("Найти в рабочей папке код, который порождает сообщение 'goal first: the goal itself is the work' "
        "или 'the goal names pursue_goal as the work', и объяснить, при каком условии этот код возвращает "
        "пустой результат — то есть почему цель, называющая 'pursue_goal' как работу, не продвигается.")
AGAIN = ("Найти в рабочей папке код, который формирует сообщение 'goal first: the goal itself is the work' "
         "(или близкое 'the goal names pursue_goal as the work'), и объяснить, почему при цели, называющей "
         "'pursue_goal' как работу, этот код возвращает пустой результат — то есть проследить по исходникам, "
         "какое условие отсекает такие цели и что именно возвращается наружу.")
OTHER = "В knowledge_library/cs/txt/Mogensen_BasicsOfCompilerDesign.txt найди раздел про LR-разбор и посчитай пример."


def _ledger(root: Path, *goals: str) -> Path:
    (root / "data").mkdir(parents=True, exist_ok=True)
    rows = [{"payload": {"goal": g + TAIL, "result": "completed"}} for g in goals]
    (root / "data" / "campaign_ledger.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    return root


def test_the_reworded_goal_is_refused(tmp_path: Path) -> None:
    root = _ledger(tmp_path, DONE, "Цель не продвигается: другое")
    assert repeats_recent_goal(AGAIN, root)
    assert "повторяет недавнюю" in _problem(AGAIN, root)


def test_a_different_goal_passes(tmp_path: Path) -> None:
    root = _ledger(tmp_path, DONE)
    assert not repeats_recent_goal(OTHER, root)


def test_the_same_quoted_message_marks_the_same_task(tmp_path: Path) -> None:
    root = _ledger(tmp_path, DONE)
    short = "Выпиши строку, где задан текст 'goal first: the goal itself is the work', и её условие."
    assert repeats_recent_goal(short, root)
