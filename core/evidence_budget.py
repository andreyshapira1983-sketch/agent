"""Evidence Budget — caps context sent to the synthesizer LLM.

Two complementary limits keep the synthesizer prompt lean:

  1. Per-artifact budget (env AGENT_EVIDENCE_FILE_CHARS, default 12 000 chars ≈ 3 k tokens)
     A single large file (README, source module) is intelligently trimmed rather than
     fed whole into the expensive model.

  2. Total evidence budget (env AGENT_EVIDENCE_TOTAL_CHARS, default 32 000 chars ≈ 8 k tokens)
     Many medium-sized artifacts cannot collectively overwhelm the context window.
     The LARGEST artifact is trimmed first, preserving smaller ones intact —
     except for blocks the caller demotes via ``trim_first_labels``, which are
     spent before any other block is touched regardless of size. Recollection
     (long-term memory) is demoted this way: "largest first" is the right rule
     among blocks of the same kind and the wrong one across kinds, because the
     freshly read file is almost always the largest block.

Realtime Intent extraction:
  When a file exceeds the per-artifact budget, we do NOT blindly return the first N chars.
  Instead, the text is split into semantic paragraphs and scored by keyword overlap with the
  CURRENT question. This is the "Realtime Intent Fix" — the excerpt window is shaped by what
  the user actually asked right now, not by file structure.

  Example: README is 40 KB but the question is "how do I configure the budget governor?".
  The function returns the 12 KB of paragraphs that contain budget/governor/config keywords,
  skipping the project description, installation guide, and licence section entirely.

  If no keyword overlap is found (e.g. the question is very short), the function falls back
  to head (70%) + tail (30%) slicing so the caller still has partial context.

Constants
---------
EVIDENCE_FILE_CHARS   = 12_000   override via AGENT_EVIDENCE_FILE_CHARS
EVIDENCE_TOTAL_CHARS  = 32_000   override via AGENT_EVIDENCE_TOTAL_CHARS
"""
from __future__ import annotations

import os
import re
from collections.abc import Set as AbstractSet

# ── configurable limits ────────────────────────────────────────────────────────

EVIDENCE_FILE_CHARS:  int = 12_000   # per-artifact ceiling
EVIDENCE_TOTAL_CHARS: int = 32_000   # total ceiling across all artifacts

# Label under which the `<long_term_memory>` block enters the total budget.
# Defined here, next to the budget it competes in, so the loop and the tests
# name the same block instead of repeating a string literal.
MEMORY_BLOCK_LABEL: str = "long_term_memory"


def _file_chars() -> int:
    try:
        return max(1, int(os.getenv("AGENT_EVIDENCE_FILE_CHARS", str(EVIDENCE_FILE_CHARS))))
    except ValueError:
        return EVIDENCE_FILE_CHARS


def _total_chars() -> int:
    try:
        return max(1, int(os.getenv("AGENT_EVIDENCE_TOTAL_CHARS", str(EVIDENCE_TOTAL_CHARS))))
    except ValueError:
        return EVIDENCE_TOTAL_CHARS


# ── keyword extraction ────────────────────────────────────────────────────────

_STOPWORDS: frozenset[str] = frozenset({
    "a", "an", "the", "is", "it", "in", "on", "at", "of", "to", "and",
    "or", "but", "for", "with", "from", "by", "as", "be", "was", "are",
    "has", "have", "had", "will", "do", "does", "did", "can", "could",
    "i", "we", "you", "he", "she", "they", "me", "my", "your", "our",
    "what", "which", "how", "when", "where", "who", "this", "that",
})

# Matches Latin/Cyrillic words and identifiers
_WORD_RE = re.compile(r"[a-zA-Z\u0400-\u04ff][a-zA-Z\u0400-\u04ff0-9_]*")


def _keywords(text: str) -> frozenset[str]:
    """Return non-trivial lowercase words from *text*."""
    return frozenset(
        m.group().lower()
        for m in _WORD_RE.finditer(text)
        if m.group().lower() not in _STOPWORDS and len(m.group()) > 2
    )


# ── paragraph splitter ────────────────────────────────────────────────────────

def _split_paragraphs(text: str) -> list[str]:
    """Split *text* into semantic chunks.

    A new paragraph starts at:
    - a blank line (one or more consecutive empty lines), OR
    - a Markdown section header (line starting with ``#``).

    Empty chunks are dropped.
    """
    paras: list[str] = []
    current: list[str] = []

    for line in text.splitlines():
        is_header = line.startswith("#")
        is_blank  = not line.strip()

        if is_header:
            if current:
                paras.append("\n".join(current))
            current = [line]
        elif is_blank:
            if current:
                paras.append("\n".join(current))
                current = []
        else:
            current.append(line)

    if current:
        paras.append("\n".join(current))

    return [p for p in paras if p.strip()]


# ── intent-aware extraction ───────────────────────────────────────────────────

def extract_relevant(text: str, *, question: str, budget: int) -> str:
    """Return a question-relevant excerpt of *text* within *budget* chars.

    Algorithm
    ---------
    1. If ``len(text) <= budget``, return unchanged.
    2. Split text into paragraphs at blank lines / Markdown headers.
    3. Score each paragraph: overlap(para_keywords, question_keywords) / question_keywords.
    4. Always include paragraph 0 (file preamble / module docstring).
    5. Greedily add highest-scoring paragraphs until *budget* is exhausted.
    6. Emit selected paragraphs in original document order with gap notices.
    7. Append a budget notice: ``...[INTENT-BUDGET: X of Y chars]``.

    Fallback (no keyword overlap):
      Return head (70% of budget) + tail (30% of budget) with a gap notice,
      so the synthesizer still has partial context even for very terse questions.

    Parameters
    ----------
    text     : full artifact content (may be megabytes)
    question : the user's current question (drives keyword scoring)
    budget   : maximum chars to return (must be > 0)
    """
    if not text or budget <= 0:
        return text[:budget] if budget > 0 else ""
    if len(text) <= budget:
        return text

    original_len = len(text)
    q_kw = _keywords(question)
    q_size = max(1, len(q_kw))

    paras = _split_paragraphs(text)
    if not paras:
        # Unparseable blob → simple head-truncate
        return text[:budget] + f"\n...[INTENT-BUDGET: {budget} of {original_len} chars; head only]"

    # Score every paragraph
    scored: list[tuple[float, int, str]] = []
    for idx, para in enumerate(paras):
        p_kw  = _keywords(para)
        score = len(q_kw & p_kw) / q_size
        scored.append((score, idx, para))

    any_match = any(s > 0.0 for s, _, _ in scored)

    if not any_match:
        # Fallback: head + tail
        head_budget = int(budget * 0.70)
        tail_budget = budget - head_budget
        head = text[:head_budget]
        tail = text[max(0, original_len - tail_budget):]
        omitted = original_len - head_budget - tail_budget
        gap     = f"\n...[{omitted} chars omitted]...\n" if omitted > 0 and tail not in head else ""
        result  = head + gap + (tail if tail not in head else "")
        notice  = f"\n...[INTENT-BUDGET: {len(result)} of {original_len} chars; no keyword match, head+tail]"
        return result + notice

    # Greedy selection: always keep para[0], then fill by descending score
    selected: set[int] = {0}
    used = len(paras[0]) + 1  # +1 for separator

    for _score, idx, para in sorted(scored, key=lambda t: (-t[0], t[1])):
        if idx in selected:
            continue
        cost = len(para) + 1
        if used + cost > budget:
            continue
        selected.add(idx)
        used += cost

    # Emit in original document order
    ordered = sorted(selected)
    parts: list[str] = []
    prev   = -1
    for idx in ordered:
        if prev >= 0 and idx > prev + 1:
            skipped = idx - prev - 1
            parts.append(f"[... {skipped} section{'s' if skipped > 1 else ''} omitted ...]")
        parts.append(paras[idx])
        prev = idx

    if ordered and ordered[-1] < len(paras) - 1:
        tail_skip = len(paras) - 1 - ordered[-1]
        parts.append(f"[... {tail_skip} section{'s' if tail_skip > 1 else ''} omitted at end ...]")

    body = "\n\n".join(parts)
    # Gap notices and "\n\n" separators push body past budget.
    # Post-trim so the total stays close to the requested limit.
    if len(body) > budget:
        body = body[:budget]
    notice = (
        f"\n...[INTENT-BUDGET: {len(body)} of {original_len} chars; "
        f"top sections by keyword relevance to question]"
    )
    return body + notice


# ── total budget across all artifacts ─────────────────────────────────────────

def _trim_notice(new_len: int, old_len: int, budget: int) -> str:
    """The notice appended to a block trimmed by the total budget.

    One definition, because two places need the exact same string: the trim
    itself, and the test deciding whether a block can still shrink. When that
    test used a constant upper bound instead, blocks that could still give
    chars back were skipped and the budget stayed violated.
    """
    return (
        f"\n...[TOTAL-BUDGET: trimmed to {new_len} of {old_len} chars "
        f"to fit {budget}-char total evidence budget]"
    )


def apply_total_budget(
    blocks: list[tuple[str, str]],
    *,
    trim_first_labels: AbstractSet[str] | None = None,
) -> tuple[list[tuple[str, str]], bool]:
    """Trim evidence blocks until their total fits in AGENT_EVIDENCE_TOTAL_CHARS.

    Strategy: trim the **largest** block first (the one wasting the most tokens).
    This preserves all smaller blocks intact and avoids cascading truncation.

    Blocks whose label appears in *trim_first_labels* are **demoted**: they are
    spent before any other block is touched, largest demoted block first, down
    to the content floor. Only when no demoted block can shrink further does a
    normal block get trimmed. This is what keeps recollection from outranking
    the file the agent just read: memory is smaller than a fresh source file, so
    "largest first" alone would always cut the fresh evidence and never memory.

    Parameters
    ----------
    blocks : list of (label, formatted_content)
    trim_first_labels : labels to spend before anything else (order within the
        group is still largest-first). Unknown labels are ignored.

    Returns
    -------
    (trimmed_blocks, was_trimmed)
        was_trimmed is True when at least one block was shortened.
    """
    budget = _total_chars()
    total  = sum(len(c) for _, c in blocks)
    if total <= budget:
        return blocks, False

    result = list(blocks)
    # Keep original content for each block so repeated trims slice the source,
    # not the already-trimmed-with-notice string.
    originals = [c for _, c in result]
    sizes     = [len(c) for c in originals]
    was_trimmed = False

    # Upper-bound notice overhead used when sizing the cut: over-reserving
    # only makes a trim slightly deeper, never leaves the budget violated.
    # Notice = "\n...[TOTAL-BUDGET: trimmed to NNNNN of NNNNN chars to fit NNNNN-char total evidence budget]"
    _NOTICE_OVERHEAD = 120
    _MIN_CONTENT     = 50          # never cut a block below this many chars

    # A bare string is an iterable of characters; treating "memory" as six
    # one-letter labels would silently demote nothing.
    if isinstance(trim_first_labels, str):
        trim_first_labels = {trim_first_labels}
    demoted = frozenset(trim_first_labels or ())

    def _smallest_possible(index: int) -> int:
        """Size this block would have if trimmed as far as the floor allows."""
        old = len(originals[index])
        return _MIN_CONTENT + len(_trim_notice(_MIN_CONTENT, old, budget))

    prev_total = sum(sizes) + 1  # sentinel to detect non-progress
    while sum(sizes) > budget:
        current_total = sum(sizes)
        if current_total >= prev_total:
            break  # safety: can't make further progress, avoid infinite loop
        prev_total = current_total

        excess = current_total - budget
        # A block that is already as small as trimming can make it gives
        # nothing back; keeping it in the pool would stall the loop on the
        # no-progress guard and leave the budget violated. Measured against the
        # real notice length, not the padded reserve above — the difference is
        # ~30 chars per block, which is exactly the window where a block that
        # could still shrink used to be skipped.
        candidates = [
            i for i in range(len(sizes)) if sizes[i] > _smallest_possible(i)
        ]
        if not candidates:
            break
        preferred = [i for i in candidates if result[i][0] in demoted]
        biggest = max(preferred or candidates, key=lambda i: sizes[i])

        old_len   = len(originals[biggest])
        # new_len must be small enough that (new_len + notice_overhead) fits
        # the required reduction.  Keep at least _MIN_CONTENT chars of content.
        target    = old_len - excess - _NOTICE_OVERHEAD
        new_len   = max(_MIN_CONTENT, target)
        label     = result[biggest][0]
        notice    = _trim_notice(new_len, old_len, budget)
        result[biggest] = (label, originals[biggest][:new_len] + notice)
        sizes[biggest]  = new_len + len(notice)
        was_trimmed = True

    return result, was_trimmed


# ── convenience: apply per-artifact limit ────────────────────────────────────

def budget_file_content(content: str, *, question: str = "") -> str:
    """Apply AGENT_EVIDENCE_FILE_CHARS budget to a single file artifact.

    If the content fits, return unchanged. Otherwise call extract_relevant()
    with the per-file limit and the current question.
    """
    limit = _file_chars()
    if len(content) <= limit:
        return content
    return extract_relevant(content, question=question, budget=limit)
