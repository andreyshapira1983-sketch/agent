"""Reconcile a new conclusion with memory BEFORE writing it (Mem0 update phase).

    The model picks one operation per new fact, given BM25-selected similar records:
    ADD (no equivalent), UPDATE (merge into one conclusion under the old question),
    DELETE (contradiction; the old one is archived), NOOP (already present).
    Everything is reversible; any model failure or unusable answer means ADD."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

#: How many similar conclusions to show the model (s in Mem0's Algorithm 1).
TOP_S = 5
#: Longer than this is a pasted letter, not a question: ask for a title.
LONG_QUESTION_CHARS = 200
#: Title length limit.
MAX_TITLE_CHARS = 160
#: A record is shown to the model whole: a tail cut at 700 chars was lost on merge.
_PREVIEW_CHARS = 1300
#: Merged conclusion limit: two conclusions of 600 chars.
MAX_MERGED_CHARS = 1200
_OPERATIONS = ("ADD", "UPDATE", "DELETE", "NOOP")

SYSTEM = """You maintain an agent's long-term memory of conclusions. A NEW conclusion is about to be stored.
Below are EXISTING conclusions that look similar. Choose exactly one operation (Mem0 update phase):
- ADD: no existing conclusion states the same fact; the new one is new knowledge.
- UPDATE: the new one is about the same thing as ONE existing conclusion and adds complementary information; you then write "merged": ONE conclusion that keeps EVERYTHING from BOTH (e.g. "Likes cheese pizza" + "Loves chicken pizza" -> "Loves cheese and chicken pizza"). Never drop a fact, number, section, page or quote from either; drop only repeated wording; at most 1100 characters.
- DELETE: the new one contradicts ONE existing conclusion about the same thing.
- NOOP: the existing conclusion already says the same thing; the new one adds nothing (different wording is still NOOP).
Two conclusions about different questions (different theorem, different section, different file, different case) are ADD even if they share words.
If the QUESTION given is long (a letter or pasted text), also write "title": one short question (max 150 characters, same language) naming what was actually asked; keep file paths and any names in parentheses or quotes exactly as written. Otherwise "title": null.
Answer with ONE JSON object only:
{"operation": "ADD|UPDATE|DELETE|NOOP", "target": "<id of the existing conclusion, or null>", "merged": "<for UPDATE only: the merged conclusion, else null>", "title": "<short question or null>"}"""


@dataclass(frozen=True)
class Consolidation:
    """Decision on a new conclusion."""

    operation: str               # ADD | UPDATE | DELETE | NOOP
    target_id: str | None = None
    merged: str | None = None
    title: str | None = None
    reason: str = ""


def similar_conclusions(content: str, records: list[Any], top_s: int = TOP_S) -> list[Any]:
    """Up to top_s earlier CONCLUSIONS similar to the new one, by BM25; only those sharing words."""
    from core.bm25 import bm25_scores, term_counts, tokens

    pool = [r for r in records if "conclusion" in (getattr(r, "tags", None) or [])]
    if not pool:
        return []
    from core.memory_embeddings import fused_relevance

    texts = [_without_sources(str(r.content)) for r in pool]
    lexical = bm25_scores(tokens(_without_sources(content)), [term_counts(t, []) for t in texts])
    # Mem0 picks by vectors; without meaning, pick by shared words.
    fused, semantic = fused_relevance(_without_sources(content), texts, lexical)
    ranked = sorted(range(len(pool)), key=lambda i: -fused[i])
    return [pool[i] for i in ranked if semantic is not None or lexical[i] > 0][:top_s]


def _without_sources(content: str) -> str:
    keep = [ln for ln in (content or "").splitlines()
            if not ln.startswith(("Источники:", "Источник:"))]
    return "\n".join(keep)


def needs_title(question: str) -> bool:
    return len(" ".join((question or "").split())) > LONG_QUESTION_CHARS


def consolidate(llm: Any, content: str, question: str, similar: list[Any]) -> Consolidation:
    """Ask the model; any failure means ADD."""
    # A None or non-string question used to crash split.
    question = question if isinstance(question, str) else ("" if question is None else str(question))
    long_q = needs_title(question)
    if llm is None or (not similar and not long_q):
        return Consolidation("ADD", reason="nothing similar stored")
    listing = "\n\n".join(
        f"[{r.id}]\n{_without_sources(str(r.content))[:_PREVIEW_CHARS]}" for r in similar
    ) or "(none)"
    user = (
        f"QUESTION ({'long — give a title' if long_q else 'short — title null'}):\n"
        f"{' '.join(question.split())[:1500]}\n\n"
        f"NEW conclusion:\n{_without_sources(content)[:_PREVIEW_CHARS]}\n\n"
        f"EXISTING similar conclusions:\n{listing}"
    )
    try:
        raw = llm.complete(SYSTEM, user, max_tokens=900, temperature=0.0)
    except Exception as exc:  # noqa: BLE001 — сбой модели не должен терять вывод
        return Consolidation("ADD", reason=f"model call failed: {type(exc).__name__}")
    from core.plan_parsing import extract_json_object

    data = extract_json_object(raw if isinstance(raw, str) else str(raw)) or {}
    op = str(data.get("operation") or "").strip().upper()
    title = data.get("title")
    title = " ".join(str(title).split())[:MAX_TITLE_CHARS] if long_q and title else None
    if op not in _OPERATIONS:
        return Consolidation("ADD", title=title, reason="unreadable model answer")
    target = data.get("target")
    ids = {r.id for r in similar}
    # A list or dict in target used to raise TypeError instead of ADD.
    if op != "ADD" and (not isinstance(target, str) or target not in ids):
        return Consolidation("ADD", title=title, reason=f"{op} named no known record ({target!r})")
    merged = " ".join(str(data.get("merged") or "").split())[:MAX_MERGED_CHARS] or None
    if op == "UPDATE" and not merged:
        return Consolidation("ADD", title=title, reason="UPDATE without merged text: both kept")
    return Consolidation(
        op, target_id=target if op != "ADD" else None,
        merged=merged if op == "UPDATE" else None, title=title, reason="model",
    )


#: Merge gate: rewriting memory continuously degrades it (arXiv 2605.12978), so merges pass a gate.
#: A merged text that lost any number (sections, pages, lines, formulas) of the old or new record is not written; both records stay.
#: Words of the questions are not compared: the model decides the subject, the gate checks that nothing was dropped.
_NUMBER = re.compile(r"\d+(?:[.,:/-]\d+)*")
_NESTED = re.compile(r"^\s*Вопрос:.*?Вывод:\s*", re.DOTALL)


def _numbers(text: str) -> set[str]:
    """Numbers of the CONCLUSION: the "Вопрос: ..." line is taken from the old record by design."""
    body = "\n".join(ln for ln in _without_sources(text or "").splitlines() if not ln.startswith("Вопрос:"))
    return set(_NUMBER.findall(body))


def gate(decision: Consolidation, new_content: str, candidates: list[Any]) -> Consolidation:
    """UPDATE only over a shown candidate and without losing numbers; otherwise ADD."""
    if decision.operation not in ("UPDATE", "DELETE"):
        return decision
    target = next((r for r in candidates if getattr(r, "id", None) == decision.target_id), None)
    if target is None:
        return Consolidation("ADD", title=decision.title,
                             reason=f"gate: {decision.operation} target is not among the shown candidates")
    if decision.operation == "UPDATE":
        lost = sorted((_numbers(str(target.content)) | _numbers(new_content)) - _numbers(decision.merged or ""))
        if lost:
            return Consolidation("ADD", title=decision.title,
                                 reason=f"gate: the merge would drop {', '.join(lost[:5])}; both kept")
    return decision


def merged_content(new_content: str, target_content: str, merged: str) -> str:
    """Merged record: the old record's question, the merged conclusion, sources of both.

        The question comes from the old record so its key does not drift; nested "Вопрос: ... Вывод: ..." heads the model repeats are stripped."""
    while _NESTED.match(merged or ""):
        merged = _NESTED.sub("", merged, count=1)
    def line(content: str, prefix: str) -> str:
        return next((ln[len(prefix):].strip() for ln in (content or "").splitlines()
                     if ln.startswith(prefix)), "")

    sources: list[str] = []
    for content in (target_content, new_content):
        for s in line(content, "Источники: ").split(","):
            if s.strip() and s.strip() not in sources:
                sources.append(s.strip())
    head = (target_content or "").split("\n", 1)[0]
    out = f"{head}\nВывод: {merged}"
    return out + (f"\nИсточники: {', '.join(sources)[:300]}" if sources else "")


def with_title(content: str, title: str | None) -> str:
    """Replace the "Вопрос: ..." line with a title; the rest unchanged."""
    if not title:
        return content
    head, sep, rest = (content or "").partition("\n")
    if not head.startswith("Вопрос: "):
        return content
    return f"Вопрос: {title}{sep}{rest}"
