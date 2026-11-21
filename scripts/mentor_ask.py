"""Put a mentor question into the agent's channel.

Usage: PYTHONIOENCODING=utf-8 PYTHONPATH=. python scripts/mentor_ask.py "question"

The channel carries questions with advisory power only: the agent reads them when
choosing goals and may decline them.
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
