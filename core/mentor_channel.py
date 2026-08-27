"""Канал наставника: вопросы к агенту с совещательной властью.

Форма задана оператором 2026-08-27: «задавать ему вопросы и пытаться его
учить, при этом не вмешиваясь в его работу». Поэтому канал несёт ВОПРОСЫ, а не
ответы, и кладёт их туда, где агент выбирает себе цели, — с явно низшей
властью: взять вопрос в работу или отклонить решает он, и отказ сам по себе
сигнал наставнику.

Граница невмешательства: наставник пишет ТОЛЬКО сюда (и в вердикты заявок с
причинами). В память агента, в его состояние, в его прогон — никогда. Учёба
идёт через его собственные органы: вопрос он прочтёт своим выбором целей.

Писатель — `scripts/mentor_ask.py`: канал с читателем и без штатного писателя
был бы механизмом, недостижимым ни одним действием (урок MIR-166).

Замер и границы: MIR-178.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from core.ids import new_id

_REL_PATH = Path("data") / "mentor_questions.jsonl"

#: Сколько открытых вопросов видит один выбор цели. Наставник, задающий
#: двадцать вопросов разом, — это уже не наставник, а расписание.
_MAX_OPEN = 3


@dataclass(frozen=True)
class MentorQuestion:
    """Один вопрос наставника. Только вопрос — ответов канал не носит."""

    id: str
    ts: str
    question: str


def append_question(workspace: Path | str, question: str) -> MentorQuestion:
    """Положить вопрос в канал. Пустой текст — отказ, а не пустая строка."""
    text = str(question or "").strip()
    if not text:
        raise ValueError("a mentor question must carry text")
    row = MentorQuestion(
        id=new_id("mq"),
        ts=datetime.now(timezone.utc).isoformat(),
        question=text,
    )
    path = Path(workspace) / _REL_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row.__dict__, ensure_ascii=False) + "\n")
    return row


def open_questions(workspace: Path | str, limit: int = _MAX_OPEN) -> tuple[MentorQuestion, ...]:
    """Последние вопросы канала; битый файл — тишина, не падение и не выдумка."""
    path = Path(workspace) / _REL_PATH
    if not path.is_file():
        return ()
    rows: list[MentorQuestion] = []
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            try:
                data = json.loads(raw)
            except ValueError:
                continue
            text = str(data.get("question") or "").strip()
            if text:
                rows.append(MentorQuestion(
                    id=str(data.get("id") or ""),
                    ts=str(data.get("ts") or ""),
                    question=text,
                ))
    except OSError:
        return ()
    return tuple(rows[-limit:])


def mentor_block(questions: tuple[MentorQuestion, ...], *, max_chars: int = 900) -> str:
    """Блок для подсказки выбора цели. Власть названа в самом блоке."""
    if not questions:
        return ""
    parts = [
        "<mentor_questions>",
        ("Questions from your mentor (advisory — you are free to decline any "
         "of them; declining is itself an answer):"),
        *(f"- [{q.id}] {q.question}" for q in questions),
        "</mentor_questions>",
    ]
    block = "\n".join(parts)
    while len(block) > max_chars and len(parts) > 3:
        parts.pop(-2)
        block = "\n".join(parts)
    return block
