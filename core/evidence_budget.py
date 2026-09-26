"""Evidence Budget — caps context sent to the synthesizer LLM.

A per-artifact limit keeps the paragraphs most relevant to the question; a total
limit trims the largest block first, except demoted blocks (memory), which pay first.
"""
from __future__ import annotations

import os
import re
from collections.abc import Mapping
from collections.abc import Set as AbstractSet

# ── configurable limits ────────────────────────────────────────────────────────

# 100 000 знаков ≈ 30 k токенов: самый большой модуль агента доходит до
# синтезатора целиком, иначе ответ не видит прочитанного.
EVIDENCE_FILE_CHARS:  int = 96_000   # per-artifact ceiling
EVIDENCE_TOTAL_CHARS: int = 100_000  # total ceiling across all artifacts

# Ceiling for the agent's own self-documentation (the planner's hint-free
# allowlist); never below the per-file ceiling. The total budget still governs.
EVIDENCE_SELF_DOC_CHARS: int = 96_000

# Label of the `<long_term_memory>` block in the total budget (shared by loop and tests).
MEMORY_BLOCK_LABEL: str = "long_term_memory"


def _file_chars() -> int:
    try:
        return max(1, int(os.getenv("AGENT_EVIDENCE_FILE_CHARS", str(EVIDENCE_FILE_CHARS))))
    except ValueError:
        return EVIDENCE_FILE_CHARS


def _self_doc_chars() -> int:
    try:
        return max(1, int(os.getenv("AGENT_EVIDENCE_SELF_DOC_CHARS",
                                    str(EVIDENCE_SELF_DOC_CHARS))))
    except ValueError:
        return EVIDENCE_SELF_DOC_CHARS


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
    """Split *text* into chunks at blank lines and Markdown headers; drop empty chunks."""
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


# ── the notice grammar ───────────────────────────────────────────────────────

#: Every trimmer notice shares one shape — an ellipsis welded to a bracket — so a new
#: notice is caught by shape, not a blacklist (MIR-097). Unclosed: a notice cut in half.
_FRAMEWORK_NOTICE_RE = re.compile(
    r"(?:\.\.\.|…)\[[^\[\]\n]{0,200}(?:\]|$)"   # ...[NOTICE]  or cut-off ...[NOTI
    r"|\[\.\.\.[^\[\]\n]{0,200}\]"              # [... N sections omitted ...]
)


#: Никогда не наша метка: число («...[1998]») и один символ («...[и]»). «...[typing]»
#: от «...[truncated]» не отличить — ложное срабатывание лишь отвергает утверждение.
_NOT_A_NOTICE_RE = re.compile(r"^(?:\d+|.)$")


def carries_framework_notice(text: str) -> bool:
    """True when a trimmer's notice ended up inside *text* (the claim extractor refuses it)."""
    for match in _FRAMEWORK_NOTICE_RE.finditer(text or ""):
        inner = match.group(0).strip(".…[]").strip()
        if inner and _NOT_A_NOTICE_RE.match(inner):
            continue  # a bibliographic year or a one-character elision
        return True
    return False


# ── intent-aware extraction ───────────────────────────────────────────────────

#: Appended to every trim notice: told only THAT content was cut, the model guessed
#: from the fragment; a "grep, don't guess" hint fixes that.
_TEACH_RECOVERY = (
    "; NOT the whole file — to find what is missing, run grep -n via "
    "shell_exec, then read that exact window"
)


def extract_relevant(text: str, *, question: str, budget: int) -> str:
    """Return a question-relevant excerpt of *text* within *budget* chars.

    Paragraphs are ranked by keyword overlap with *question* (paragraph 0 always kept)
    and emitted in document order; with no overlap, falls back to head 70% + tail 30%.
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
        return text[:budget] + f"\n...[INTENT-BUDGET: {budget} of {original_len} chars; head only]"

    scored: list[tuple[float, int, str]] = []
    for idx, para in enumerate(paras):
        p_kw  = _keywords(para)
        score = len(q_kw & p_kw) / q_size
        scored.append((score, idx, para))

    any_match = any(s > 0.0 for s, _, _ in scored)

    if not any_match:
        head_budget = int(budget * 0.70)
        tail_budget = budget - head_budget
        head = text[:head_budget]
        tail = text[max(0, original_len - tail_budget):]
        omitted = original_len - head_budget - tail_budget
        gap     = f"\n...[{omitted} chars omitted]...\n" if omitted > 0 and tail not in head else ""
        result  = head + gap + (tail if tail not in head else "")
        notice  = (
            f"\n...[INTENT-BUDGET: {len(result)} of {original_len} chars; "
            f"no keyword match, head+tail{_TEACH_RECOVERY}]"
        )
        return result + notice

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
    # Gap notices and separators can push body past budget.
    if len(body) > budget:
        body = body[:budget]
    notice = (
        f"\n...[INTENT-BUDGET: {len(body)} of {original_len} chars; "
        f"top sections by keyword relevance to question{_TEACH_RECOVERY}]"
    )
    return body + notice


# ── total budget across all artifacts ─────────────────────────────────────────

def _trim_notice(new_len: int, old_len: int, budget: int) -> str:
    """The notice appended to a block trimmed by the total budget."""
    return (
        f"\n...[TOTAL-BUDGET: trimmed to {new_len} of {old_len} chars "
        f"to fit {budget}-char total evidence budget]"
    )


def _drop_notice(old_len: int) -> str:
    """The notice left where a block was dropped whole."""
    return (
        f"[TOTAL-BUDGET: dropped whole — {old_len} chars did not fit; "
        f"content unavailable this turn, nothing here is quotable]"
    )


#: Never cut a block below this many chars.
_MIN_CONTENT = 50


def _block_floor(
    label: str,
    relaxed: bool,
    *,
    demoted: AbstractSet[str],
    useful_floors: Mapping[str, int],
    fair_min: int,
) -> int:
    """Smallest content size a block may be trimmed to on this pass."""
    if label in demoted:
        useful = useful_floors.get(label)
        if relaxed or useful is None:
            return _MIN_CONTENT
        return max(_MIN_CONTENT, useful)
    return _MIN_CONTENT if relaxed else fair_min


def apply_total_budget(
    blocks: list[tuple[str, str]],
    *,
    trim_first_labels: AbstractSet[str] | None = None,
    min_useful: Mapping[str, int] | None = None,
) -> tuple[list[tuple[str, str]], bool]:
    """Trim evidence blocks until their total fits AGENT_EVIDENCE_TOTAL_CHARS.

    Blocks in *trim_first_labels* are spent first, so memory never outranks the file
    just read; *min_useful* maps a label to the size below which it is dropped whole.
    """
    budget = _total_chars()
    total  = sum(len(c) for _, c in blocks)
    if total <= budget:
        return blocks, False

    result = list(blocks)
    # Repeated trims slice the original, not the already-trimmed string.
    originals = [c for _, c in result]
    sizes     = [len(c) for c in originals]
    was_trimmed = False

    # Upper bound on notice length; over-reserving only makes a trim slightly deeper.
    _NOTICE_OVERHEAD = 120

    # Pass 1 keeps non-demoted blocks at a fair share, so the overflow cascades instead
    # of sinking the largest block (the file just read) to the floor (MIR-073).
    # Pass 2 uses the absolute floor for everyone.
    _fair_min = max(_MIN_CONTENT, budget // (2 * max(1, len(blocks))))

    # A bare string would iterate as one-letter labels and demote nothing.
    if isinstance(trim_first_labels, str):
        trim_first_labels = {trim_first_labels}
    demoted = frozenset(trim_first_labels or ())
    _useful_floors = dict(min_useful or {})

    # Content chars each block keeps now; a block picked twice must not re-grow.
    kepts = list(sizes)

    def _floor_for(index: int, relaxed: bool) -> int:
        return _block_floor(
            result[index][0], relaxed,
            demoted=demoted, useful_floors=_useful_floors, fair_min=_fair_min,
        )

    def _smallest_possible(index: int, relaxed: bool) -> int:
        floor = _floor_for(index, relaxed)
        return floor + len(_trim_notice(floor, len(originals[index]), budget))

    for relaxed in (False, True):
        prev_total = sum(sizes) + 1  # sentinel to detect non-progress
        while sum(sizes) > budget:
            current_total = sum(sizes)
            if current_total >= prev_total:
                break  # safety: can't make further progress, avoid infinite loop
            prev_total = current_total

            excess = current_total - budget
            # A block at its smallest possible size would stall the loop; measured
            # with the real notice length, not the padded reserve.
            candidates = [
                i for i in range(len(sizes))
                if sizes[i] > _smallest_possible(i, relaxed)
            ]
            if not candidates:
                break
            preferred = [i for i in candidates if result[i][0] in demoted]
            biggest = max(preferred or candidates, key=lambda i: sizes[i])

            old_len   = len(originals[biggest])
            # Based on the current kept length, never below this pass's floor.
            target    = kepts[biggest] - excess - _NOTICE_OVERHEAD
            new_len   = max(_floor_for(biggest, relaxed), target)
            label     = result[biggest][0]
            # If the smallest whole item no longer fits, the block keeps nothing usable
            # yet still costs its notice: drop it whole, saying so in the prompt.
            floor_useful = _useful_floors.get(label)
            if floor_useful is not None and new_len < floor_useful:
                dropped = _drop_notice(len(originals[biggest]))
                result[biggest] = (label, dropped)
                sizes[biggest]  = len(dropped)
                kepts[biggest]  = 0
                was_trimmed = True
                continue
            notice    = _trim_notice(new_len, old_len, budget)
            result[biggest] = (label, originals[biggest][:new_len] + notice)
            sizes[biggest]  = new_len + len(notice)
            kepts[biggest]  = new_len
            was_trimmed = True
        if sum(sizes) <= budget:
            break

    return result, was_trimmed


# ── convenience: apply per-artifact limit ────────────────────────────────────

def budget_file_content(
    content: str, *, question: str = "", self_documentation: bool = False,
) -> str:
    """Apply the per-artifact budget to a single file artifact.

    ``self_documentation`` raises the ceiling to AGENT_EVIDENCE_SELF_DOC_CHARS; the
    caller decides, since this leaf module imports nothing from ``core`` (INV-1).
    """
    limit = _self_doc_chars() if self_documentation else _file_chars()
    if len(content) <= limit:
        return content
    return extract_relevant(content, question=question, budget=limit)


# ── The long-term-memory block: shared vocabulary with its builder ──────────
# Shared with the block's builder (loop_methods2 imports these); this module is a leaf.
MEMORY_OPEN_TAG: str = "<long_term_memory>"
MEMORY_CLOSE_TAG: str = "</long_term_memory>"


_TRIM_NOTICE_RE = re.compile(
    r"\n\.\.\.\[TOTAL-BUDGET: trimmed to (\d+) of (\d+) chars "
)

# A block dropped whole reports kept=0 through the same reader as a trimmed one.
_DROP_NOTICE_RE = re.compile(
    r"\[TOTAL-BUDGET: dropped whole — (\d+) chars did not fit"
)


def total_trims(blocks: list[tuple[str, str]]) -> list[tuple[str, int, int]]:
    """(label, kept_chars, original_chars) for every block the total budget trimmed or dropped."""
    out: list[tuple[str, int, int]] = []
    for label, content in blocks:
        matches = list(_TRIM_NOTICE_RE.finditer(content))
        if matches:
            last = matches[-1]
            out.append((label, int(last.group(1)), int(last.group(2))))
            continue
        drop_match = None
        for match in _DROP_NOTICE_RE.finditer(content):
            drop_match = match
        if drop_match is not None:
            out.append((label, 0, int(drop_match.group(1))))
    return out


def rebuild_trimmed_memory(
    trimmed: str,
    original: str,
    record_lines: list[tuple[str, str]],
) -> tuple[str, set[str]]:
    """Rebuild a char-sliced `<long_term_memory>` block from whole records.

    The cut length comes from the budget's notice and record boundaries from
    *record_lines* (the ``(id, line)`` pairs the block was built from) — re-deriving
    either by scanning or pattern proved wrong. Only records whose whole line fits
    survive. Returns ``("", set())`` when none does or the block cannot be accounted
    for (fail closed).
    """
    # A drop notice is accounted-for content: keep it, or "dropped" reads as "never existed".
    drop = _DROP_NOTICE_RE.search(trimmed)
    if drop is not None and int(drop.group(1)) == len(original):
        return trimmed, set()

    # The budget's notice is the LAST match: a record may quote an older one.
    notice_match = None
    for match in _TRIM_NOTICE_RE.finditer(trimmed):
        notice_match = match
    if notice_match is None or int(notice_match.group(2)) != len(original):
        return "", set()
    kept_chars = int(notice_match.group(1))
    notice = trimmed[notice_match.start():]
    if kept_chars > len(original) or not trimmed.startswith(original[:kept_chars]):
        return "", set()

    prefix = f"{MEMORY_OPEN_TAG}\n"
    body = "\n".join(line for _, line in record_lines)
    if not record_lines or original != f"{prefix}{body}\n{MEMORY_CLOSE_TAG}":
        return "", set()

    survivors: list[str] = []
    end_of_last = 0
    offset = len(prefix)
    for record_id, line in record_lines:
        line_end = offset + len(line)
        if line_end > kept_chars:
            break
        survivors.append(record_id)
        end_of_last = line_end
        offset = line_end + 1          # the newline joining the records
    if not survivors:
        return "", set()
    return (
        original[:end_of_last] + notice + f"\n{MEMORY_CLOSE_TAG}",
        set(survivors),
    )
