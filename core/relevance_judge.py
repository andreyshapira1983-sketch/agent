"""Relevance judge: whether an answer addresses the asked question, claim by claim (yes / no / idk; score = (yes + idk) / total); None means no score (off, empty, malformed verdict) and the old measure applies. Enabled by AGENT_RELEVANCE_JUDGE=1, otherwise no calls."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

#: How much question and answer text the judge sees; bridge answers run to ~8 000 chars and the rest is usually verification tails.
_QUESTION_CHARS = 6_000
_ANSWER_CHARS = 8_000
_MAX_TOKENS = 3_000

_SYSTEM = """You are an evaluation judge. Output JSON only.
Task: judge whether an assistant's ANSWER addresses the user's QUESTION.
Step 1: split the ANSWER into its distinct statements (max 15; merge trivia).
Step 2: for each statement give verdict "yes" (relevant to what the question asks),
"no" (irrelevant / answers something else), or "idk" (supporting remark, ambiguous).
A statement that says the request could not be done and why IS relevant.
Return JSON: {"statements":[{"s":"...","verdict":"yes|no|idk"}]}"""


@dataclass(frozen=True)
class RelevanceVerdict:
    score: float
    statements: int
    irrelevant: tuple[str, ...]

    def to_log_payload(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 3),
            "statements": self.statements,
            "irrelevant": list(self.irrelevant[:4]),
        }


def judge_enabled() -> bool:
    return (os.getenv("AGENT_RELEVANCE_JUDGE", "") or "").strip().lower() in {
        "1", "true", "yes", "on"}


def parse_verdict(raw: str) -> RelevanceVerdict | None:
    """Judge reply -> score; anything malformed is None."""
    try:
        data = json.loads(raw or "")
    except json.JSONDecodeError:
        return None
    items = data.get("statements") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return None
    verdicts = [(str(i.get("verdict", "")).strip().lower(), str(i.get("s", "")))
                for i in items if isinstance(i, dict)]
    verdicts = [(v, s) for v, s in verdicts if v in {"yes", "no", "idk"}]
    if not verdicts:
        return None
    kept = sum(1 for v, _ in verdicts if v != "no")
    return RelevanceVerdict(
        score=kept / len(verdicts),
        statements=len(verdicts),
        irrelevant=tuple(s[:160] for v, s in verdicts if v == "no"),
    )


def judge_relevance(llm: Any, question: str | None, answer: str | None) -> RelevanceVerdict | None:
    """One judge call; None means no score (off, empty input, malformed reply).

    A failing `llm.complete` is NOT caught here: it propagates, and
    loop_verification._judged_relevance journals it as sensor_failed.
    """
    if llm is None or not judge_enabled():
        return None
    question, answer = (question or "").strip(), (answer or "").strip()
    if not question or not answer:
        return None
    from core.llm import accepted_flags

    user = f"QUESTION:\n{question[:_QUESTION_CHARS]}\n\nANSWER:\n{answer[:_ANSWER_CHARS]}"
    raw = llm.complete(
        system=_SYSTEM, user=user, max_tokens=_MAX_TOKENS, temperature=0.0,
        **accepted_flags(llm.complete, {"json_object": True, "allow_continuation": False}),
    )
    return parse_verdict(raw)
