"""Mentor channel: questions to the agent with advisory authority only."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from core.ids import new_id

_REL_PATH = Path("data") / "mentor_questions.jsonl"

# How many open questions one goal choice sees.
_MAX_OPEN = 3


@dataclass(frozen=True)
class MentorQuestion:
    """One mentor question; questions only, the channel carries no answers."""

    id: str
    ts: str
    question: str


def append_question(workspace: Path | str, question: str) -> MentorQuestion:
    """Put a question into the channel; empty text is a refusal, not an empty line."""
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
    """Latest channel questions; a broken file yields silence, not a crash."""
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
    """Block for the goal-choice prompt; its authority is named inside the block."""
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
