"""Положить вопрос наставника в канал агента.

Запуск: PYTHONIOENCODING=utf-8 PYTHONPATH=. python scripts/mentor_ask.py "вопрос"

Канал несёт ВОПРОСЫ с совещательной властью: агент читает их при выборе целей
и вправе отклонить. Штатный писатель существует, потому что читатель без
писателя — механизм, недостижимый ни одним действием (урок MIR-166).
Разбор и границы: MIR-178.
"""
from __future__ import annotations

import sys
from pathlib import Path

from core.mentor_channel import append_question, open_questions


def main(argv: list[str]) -> int:
    if len(argv) != 1 or not argv[0].strip():
        print(__doc__, file=sys.stderr)
        return 2
    row = append_question(Path(), argv[0])
    print(f"вопрос положен: {row.id}")
    for q in open_questions(Path()):
        print(f"  открыт: [{q.id}] {q.question[:80]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
