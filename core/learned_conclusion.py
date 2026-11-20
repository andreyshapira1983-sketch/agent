"""Write what a turn found out (the conclusion, not what it read on the way) to long-term memory, through the usual `remember` path, when the episode is admitted to experience and the answer rests on this turn's source."""
from __future__ import annotations

import re
from typing import Any

# The READ filter "self-knowledge does not go into a subject task" lives in core/self_knowledge.py; names are re-exported for old imports.
from core.self_knowledge import (  # noqa: F401
    asks_about_self,
    is_self_knowledge,
    self_knowledge_off_topic,
)

#: Answer references `[file:…]`, `[topic-only:…]`, `[claim-…]` are addresses, not knowledge.
_CITATION = re.compile(r"\s*\[[^\[\]\n]{1,200}\]")
_CONCLUSION = re.compile(
    r"Conclusion:\s*(.*?)(?:\n\s*(?:Facts|Sources|Confidence|Unverified|Safety)\s*:|$)",
    re.DOTALL | re.IGNORECASE,
)
#: A "not found / do not know" conclusion is honest but not knowledge about the world.
_NOT_FOUND = re.compile(
    r"не\s+удалось|не\s+знаю|не\s+найден|не\s+обнаружен|нет\s+в\s+библиотек|"
    r"could\s+not|not\s+found|unable\s+to|don'?t\s+know",
    re.IGNORECASE,
)
MAX_CONCLUSION_CHARS = 600
_WEB_SOURCE = re.compile(r"(?:web|search|rss|semantic_scholar)[:_]|tool_output:web|https?://", re.IGNORECASE)
#: `admitted-episode`, not "verified": admission confirms the claims rest on what was read this turn, not that the conclusion is correct.
TAGS = ["fact", "conclusion", "admitted-episode"]


def conclusion_of(answer: str) -> str:
    """Conclusion section of the answer without references; empty if absent."""
    m = _CONCLUSION.search(answer or "")
    if not m:
        return ""
    text = _CITATION.sub("", m.group(1))
    return " ".join(text.split())[:MAX_CONCLUSION_CHARS].strip()


def conclusion_memory(episode: Any) -> str | None:
    """Text of the record "question -> conclusion -> sources", or None if there is nothing to write."""
    if not getattr(episode, "usage_eligible", False):
        return None
    sources = [s for s in (getattr(episode, "source_labels", None) or []) if s]
    if not sources:
        return None  # a conclusion without this turn's source is a retelling, not something found out
    # Two worlds, two statuses: the local library is the source of truth; a web-based conclusion stays episode experience and does not enter memory as a fact.
    if any(_WEB_SOURCE.match(s) for s in sources):
        return None
    conclusion = conclusion_of(getattr(episode, "full_answer", "") or "")
    question = " ".join(str(getattr(episode, "question", "") or "").split())
    if not conclusion or not question or _NOT_FOUND.search(conclusion):
        return None
    return (
        f"Вопрос: {question[:300]}\n"
        f"Вывод: {conclusion}\n"
        f"Источники: {', '.join(sources[:5])[:300]}"
    )


def _question_of(content: str) -> str:
    first = (content or "").split("\n", 1)[0]
    return first[len("Вопрос: "):].strip() if first.startswith("Вопрос: ") else ""


def _conclusion_line(content: str) -> str:
    for line in (content or "").splitlines():
        if line.startswith("Вывод: "):
            return line[len("Вывод: "):].strip()
    return ""


#: Named entity of the question (term in parentheses, quotes or guillemets); stable under rephrasing, so the same subject gives the same key.
_NAMED_RELATION_RE = re.compile(
    r"\(([a-zA-Z][a-zA-Z /_-]{2,40})\)"
    r"|'([^']{3,60})'"
    r"|«([^»]{3,60})»"
)
_SOURCE_PATH_RE = re.compile(r"[\w./-]+\.(?:txt|md|py|jsonl|json|csv|pdf)")


def question_key(question: str) -> tuple[tuple[str, ...], tuple[str, ...]] | None:
    """Question key (subject, relation), or None. STRUCTURAL, not similarity-based: contradictions are on average more similar to the original than true rephrasings, so no similarity threshold can exceed 0.67 precision (arXiv 2606.26511). No named relation means no key and the record is untouched: a missed merge keeps an extra record, a wrong merge erases correct knowledge."""
    low = (question or "").lower()
    rel = tuple(sorted({
        (a or b or c).strip()
        for a, b, c in _NAMED_RELATION_RE.findall(low)
        if (a or b or c).strip()
    }))
    if not rel:
        return None
    paths = tuple(sorted(set(_SOURCE_PATH_RE.findall(low))))
    return paths, rel


def same_question(a: str, b: str) -> bool:
    """Same subject and relation, structurally, without similarity measures."""
    ka = question_key(a)
    return ka is not None and ka == question_key(b)


def superseded_by(new_content: str, records: list[Any]) -> tuple[list[Any], bool]:
    """Earlier conclusions on the SAME question and whether this conclusion already exists; a different new conclusion replaces the old one, which goes to the archive, not erased (a near-identical correction would otherwise be rejected as a duplicate)."""
    question = _question_of(new_content)
    fresh = _conclusion_line(new_content)
    same = [r for r in records
            if "conclusion" in (getattr(r, "tags", None) or [])
            and question and same_question(_question_of(getattr(r, "content", "")), question)]
    if any(_conclusion_line(r.content) == fresh for r in same):
        return [], True
    return same, False


#: Links 3-4 of the chain "found on the web -> verified -> decided to keep -> recorded with provenance". Qualification is this turn's check: one claim with a verbatim quote has the checker verdict "verified" and its evidence is an OPENED page (`web_page:<url>`). Taken from the verification report, not the answer text, because `[verified:…]` is stripped from the answer (core/answer_format._strip_verification_markers). Search results without an opened page qualify nothing.
WEB_TAGS = ["fact", "conclusion", "web-knowledge"]
_QUOTE = re.compile(r"«([^«»\n]{12,400})»|“([^“”\n]{12,400})”|\"([^\"\n]{12,400})\"")
_OWN_WRAPPER = "\n\nThis is your own chosen goal."


def _verified_web_quote(verification: Any, chain: Any) -> tuple[str, str]:
    """(quote, url) from a verified claim about a web page, or ("", "")."""
    pages = {ev.id: ev.source_id[len("web_page:"):]
             for ev in (getattr(chain, "evidences", None) or [])
             if str(getattr(ev, "source_id", "")).startswith("web_page:")}
    for chunk in (getattr(verification, "chunks", None) or []):
        if chunk.verdict != "verified":
            continue
        url = next((pages[i] for i in chunk.matched_evidence_ids if i in pages), "")
        quote = _QUOTE.search(chunk.text or "")
        if url and quote:
            return next(g for g in quote.groups() if g), url
    return "", ""


def web_knowledge_reason(episode: Any, verification: Any = None, chain: Any = None) -> str:
    """Why web knowledge was NOT recorded, or empty; a silent refusal cannot be debugged."""
    if not getattr(episode, "usage_eligible", False):
        return "episode not admitted to experience"
    labels = [str(s) for s in (getattr(episode, "source_labels", None) or [])]
    if not any(s.startswith("web_fetch:http") for s in labels):
        return "no page was opened (web_fetch)"
    if verification is None or chain is None:
        return "no verification report at write time"
    pages = sum(1 for ev in (getattr(chain, "evidences", None) or [])
                if str(getattr(ev, "source_id", "")).startswith("web_page:"))
    verified = sum(1 for c in (getattr(verification, "chunks", None) or []) if c.verdict == "verified")
    if not _verified_web_quote(verification, chain)[0]:
        return f"no verified claim quoting a page (pages={pages}, verified_chunks={verified})"
    return ""


def web_knowledge_memory(episode: Any, verification: Any = None, chain: Any = None) -> str | None:
    """Record "question -> conclusion -> quote -> url and date", or None when the web is not qualified."""
    if not getattr(episode, "usage_eligible", False):
        return None
    labels = [str(s) for s in (getattr(episode, "source_labels", None) or [])]
    if not any(s.startswith("web_fetch:http") for s in labels):
        return None  # page was not opened: a retelling of search results, not a source
    quote, url = _verified_web_quote(verification, chain)
    if not quote:
        return None  # no quote confirmed against the page: nothing to keep
    answer = getattr(episode, "full_answer", "") or ""
    conclusion = conclusion_of(answer)
    question = " ".join(str(getattr(episode, "question", "") or "").split(_OWN_WRAPPER)[0].split())
    if not url or not conclusion or not question or _NOT_FOUND.search(conclusion):
        return None
    read_on = str(getattr(episode, "created_at", "") or "")[:10]
    return (
        f"Вопрос: {question[:300]}\n"
        f"Вывод: {conclusion}\n"
        f"Цитата: «{quote}»\n"
        f"Источник: {url} (прочитан {read_on})"
    )
