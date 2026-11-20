"""Verifier text utilities: chunk splitting, citation parsing and matching,
and statistical-claim detection.
"""
from __future__ import annotations

import re
from typing import Any

from core.evidence import Evidence, ProvenanceChain

from .verifier_models import Citation, VerificationReport
from .verifier_patterns import (
    _BARE_LIST_MARKER_RE,
    _CITATION_BODY_TOKEN_RE,
    _CITATION_RE,
    _MAX_EXCERPT_FOR_NLI,
    _MD_HEADING_RE,
    _MIN_TOKEN_LEN,
    _NLI_SYSTEM,
    _NO_TOKEN_FALLBACK_PREFIXES,
    _OUTPUT_CONTRACT_HEADER_RE,
    _SENTENCE_SPLIT_RE,
    _STAT_FIGURE_MIN_LEN,
    _STAT_FIGURE_RE,
    _STAT_TRIGGER_RE,
    _SUBAGENT_META_RE,
    _TOKEN_STOPWORDS,
    CITATION_PREFIXES,
)


def _normalise_figure(fig: str) -> str:
    s = fig.lower()
    s = re.sub(r"\s+", "", s)
    return s.replace("\u2013", "-").replace("\u2014", "-")


#: Currency signs glued to a number. A claim's figure is extracted with them ("$0,171") while tools print the amount as a word ("usd 0.171"), so a verbatim match never happened.
_CURRENCY_GLUE = "$€£₽¥"
_UNIT_WORD_RE = re.compile(r"(?:usd|eur|gbp|долл\w*|руб\w*|rub)\.?$")


def _figure_variants(fig: str) -> set[str]:
    """Spellings of the SAME number that verification must accept.

        Two cases: a glued currency sign ("$0,171" vs "usd 0.171") and a decimal comma ("0,171" vs "0.171"). A comma may also be a thousands separator ("1,500"), so both readings are accepted, with a dot and without a separator. More SPELLINGS of the same number, never another number."""
    base = _normalise_figure(fig)
    if not base:
        return set()
    out = {base, base.lstrip(_CURRENCY_GLUE)}
    # A unit as a word ("руб", "usd") acts like a glued sign: evidence often has the number without it. The same number is accepted, not another.
    unitless = _UNIT_WORD_RE.sub("", base)
    if unitless and unitless != base and any(ch.isdigit() for ch in unitless):
        out.add(unitless)
    for value in list(out):
        if "," in value:
            out.add(value.replace(",", "."))
            out.add(value.replace(",", ""))
        elif "." in value:
            out.add(value.replace(".", ","))
    return {value for value in out if value}


def extract_statistical_figures(text: str) -> list[str]:
    """Numbers the chunk CLAIMS. The citation address is not included.

        The citation marker is stripped before parsing; otherwise digits from the address path (dates, article numbers) became claim numbers and a correct citation was demoted to `topic_supported_but_claim_unverified`."""
    if not text:
        return []
    text = _CITATION_RE.sub(" ", text)
    if not _STAT_TRIGGER_RE.search(text):
        return []
    figures: list[str] = []
    seen: set[str] = set()
    for m in _STAT_FIGURE_RE.finditer(text):
        raw = m.group(0).strip()
        bare_num = re.fullmatch(r"\d+", raw)
        if bare_num and len(raw) < _STAT_FIGURE_MIN_LEN:
            continue
        norm = _normalise_figure(raw)
        if norm in seen:
            continue
        seen.add(norm)
        figures.append(raw)
    return figures


def is_statistical_claim(text: str) -> bool:
    return bool(text and _STAT_TRIGGER_RE.search(text))


#: Tolerance for an APPROXIMATE number: "about 20" with 19.8 in evidence is right though a literal "20" is absent. Exempting approximations entirely would let "about 20" pass at 500, so a computed tolerance is used.
_APPROXIMATION_TOLERANCE = 0.1


def _numbers_in(text: str) -> list[float]:
    out: list[float] = []
    for match in re.finditer(r"\d+(?:[.,]\d+)?", text or ""):
        try:
            out.append(float(match.group(0).replace(",", ".")))
        except ValueError:
            continue
    return out


def _approximately_supported(excerpt: str, figure: str) -> bool:
    """Whether the evidence has a number within tolerance of the claimed one."""
    claimed = _numbers_in(figure)
    if not claimed:
        return False
    target = claimed[0]
    if target == 0:
        return any(abs(value) <= _APPROXIMATION_TOLERANCE for value in _numbers_in(excerpt))
    return any(
        abs(value - target) / abs(target) <= _APPROXIMATION_TOLERANCE
        for value in _numbers_in(excerpt)
    )


def _found_whole(variant: str, excerpt_norm: str) -> bool:
    """The number in evidence as a whole, not part of another: "2500" not inside "12500". The boundary is a non-digit directly left/right."""
    lead = r"(?<!\d)" if variant[:1].isdigit() else ""
    tail = r"(?!\d)" if variant[-1:].isdigit() else ""
    return re.search(lead + re.escape(variant) + tail, excerpt_norm) is not None


def _excerpt_supports_figures(
    excerpt: str, figures: list[str], *, approximate: bool = False
) -> bool:
    if not figures:
        return True
    if not excerpt:
        return False
    excerpt_norm = _normalise_figure(excerpt)
    for figure in figures:
        if any(_found_whole(v, excerpt_norm) for v in _figure_variants(figure)):
            continue
        if approximate and _approximately_supported(excerpt, figure):
            continue
        return False
    return True


def _output_contract_header_name(text: str) -> str | None:
    stripped = (text or "").strip()
    match = _OUTPUT_CONTRACT_HEADER_RE.match(stripped)
    if not match:
        return None
    return match.group(1).casefold()


def is_structural_chunk(text: str) -> bool:
    if not text or not text.strip():
        return True
    stripped = text.strip()
    # A code fence is a sentence, not a claim: outside the arithmetic of evidence. Prose about code stays a claim and is checked as before.
    if stripped.startswith("```"):
        return True
    if _output_contract_header_name(stripped) is not None:
        return True
    if _MD_HEADING_RE.match(stripped) and "[" not in stripped:
        return True
    return bool(_BARE_LIST_MARKER_RE.match(stripped))


def parse_citations(text: str) -> list[Citation]:
    cits: list[Citation] = []
    for m in _CITATION_RE.finditer(text):
        prefix = m.group(1)
        body = (m.group(2) or "").strip()
        cits.append(Citation(prefix=prefix, body=body, raw=m.group(0), expected_kind=CITATION_PREFIXES[prefix]))
    return cits


def _is_citation_only_chunk(text: str) -> bool:
    cits = parse_citations(text)
    if not cits:
        return False
    remainder = text
    for cit in cits:
        remainder = remainder.replace(cit.raw, "")
    return not remainder.strip(" \t\r\n.,;:-")


def _merge_citation_only_chunks(chunks: list[str]) -> list[str]:
    merged: list[str] = []
    for chunk in chunks:
        if _is_citation_only_chunk(chunk) and merged and not is_structural_chunk(merged[-1]):
            merged[-1] = f"{merged[-1].rstrip()} {chunk.strip()}"
            continue
        merged.append(chunk)
    return merged


#: A whole code fence, lazy and non-catastrophic: ``` ... ``` via (?s).
_FENCE_BLOCK_RE = re.compile(r"(?s)(```.*?```)")


def split_into_chunks(answer: str) -> list[str]:
    """Prose is cut into sentences; a code fence is one atomic chunk.

        Code is a sentence, not a claim about the world; its judge is tests."""
    if not answer or not answer.strip():
        return []
    chunks: list[str] = []
    for part in _FENCE_BLOCK_RE.split(answer):
        if not part.strip():
            continue
        if part.lstrip().startswith("```"):
            chunks.append(part.strip())
        else:
            chunks.extend(
                p.strip() for p in _SENTENCE_SPLIT_RE.split(part) if p.strip()
            )
    return chunks


def extract_unresolved_web_urls(report: VerificationReport) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for chunk in report.chunks:
        if chunk.verdict != "cited_but_unmatched":
            continue
        for cit in chunk.citations:
            if cit.prefix != "web":
                continue
            url = cit.body.strip()
            if not url:
                continue
            lowered = url.lower()
            if not (lowered.startswith(("http://", "https://"))):
                continue
            if url in seen:
                continue
            seen.add(url)
            ordered.append(url)
    return ordered


def _tokenise_citation_body(body: str) -> list[str]:
    if not body:
        return []
    raw = [t for t in _CITATION_BODY_TOKEN_RE.split(body.lower()) if t]
    return [t for t in raw if len(t) >= _MIN_TOKEN_LEN and t not in _TOKEN_STOPWORDS]


def _normalise_path(text: str) -> str:
    """One spelling for one file: POSIX separators, no `./`, no trailing slash."""
    out = text.replace("\\", "/").strip().casefold()
    while out.startswith("./"):
        out = out[2:]
    while "//" in out:
        out = out.replace("//", "/")
    return out.rstrip("/")


#: `path:208` or `path:200-215` — the line window a `file_read` label carries.
_LINE_WINDOW_SUFFIX_RE = re.compile(r":\d+(?:-\d+)?$")


def _split_window(text: str) -> tuple[str, str]:
    """`core/x.py:200-215` -> (`core/x.py`, `:200-215`); no window -> (`path`, ``)."""
    m = _LINE_WINDOW_SUFFIX_RE.search(text)
    if m is None:
        return text, ""
    return text[: m.start()], m.group(0)


def same_file(cited: str, source_id: str, *, same_window: bool = False) -> bool:
    """Do a citation body and an evidence label name the SAME file?

    Not resolved against the workspace root on purpose: the root is not
    known here, and it is not needed. The sanitiser drops absolute paths from
    tool arguments (`core/step_sanitizer.py`), so a label is always
    relative; only the CITATION varies, and a suffix test settles that
    without new plumbing.

    A line window (`:200-215`) names a part of the file, not another file:
    `[file:core/x.py]` matches evidence `file:core/x.py:200-215` and the
    other way round. With `same_window=True` both windows must also agree —
    the caller's first pass, so a citation of lines 200-215 is matched to
    that window before it falls back to any other read of the same file.
    """
    a, a_window = _split_window(_normalise_path(cited))
    b, b_window = _split_window(
        _normalise_path(source_id.split(":", 1)[-1] if ":" in source_id else source_id)
    )
    if not a or not b:
        return False
    if same_window and a_window != b_window:
        return False
    return a == b or a.endswith("/" + b) or b.endswith("/" + a)


def runtime_evidence_pool() -> list[Evidence]:
    """What this process measured about itself, as citable Evidence.

    Built on demand and NOT folded into the provenance chain. The first
    attempt did fold it, and 21 tests said why that is wrong: the chain is
    counted and ordered, and its contracts are real — a failed step yields
    no evidence, a zero-step plan yields an EMPTY chain. Five always-present
    entries break both, and they also destroy `chain_was_empty`, which
    separates "the agent looked and found nothing" from "did not look".
    """
    from core.evidence import make_evidence
    from core.run_context import current_run
    from core.runtime_self import process_facts, shown_facts

    # Plus everything the `<runtime_self>` block showed the model IN THIS run: the hint allows citing every field of it (`core/runtime_self.py`).
    run_id = str(getattr(current_run(), "run_id", "") or "")
    facts = {**shown_facts(run_id), **process_facts()}
    return [
        make_evidence(
            kind="runtime",
            source_id=f"runtime:{key}",
            obtained_via="process_self_measurement",
            claim=f"This run's {key}",
            excerpt=str(value),
        )
        for key, value in facts.items()
    ]


def truth_excerpt(text: str) -> str:
    """Excerpt WITHOUT the experience question: everything after the marker is code the turn wrote itself.

        Split so the literal gate does not count the turn's own variable names as invented, and so the experience question inside a shared excerpt does not confirm its own answer."""
    from core.evidence import QUESTION_CODE_MARKER

    return str(text or "").split(QUESTION_CODE_MARKER, 1)[0]


def _search_output_evidence(chain: ProvenanceChain) -> list:
    """A `find_in_files` result is also evidence about the files named in it.

        Same pattern as `match_citation` adding `web_search_hit` for `web` citations. A search lists all hits in one `tool_output:find_in_files` evidence, which a `file:` citation did not reach, so correct citations of lines found by search were cut as invented."""
    return [ev for ev in chain.by_kind("tool_output")  # type: ignore[arg-type]
            if getattr(ev, "obtained_via", "") == "find_in_files"]


def _search_names_file(evidences: list, cited_body: str):
    """The search evidence whose output names THIS file, or None.

        Search output lists hits as "path:line: text"; the path itself is compared, not a substring (`core/x.py` must not match `core/xyz.py`)."""
    cited = str(cited_body).split(":", 1)[0].strip().replace("\\", "/").lower()
    if not cited:
        return None
    for ev in evidences:
        haystack = str(getattr(ev, "excerpt", "") or "").replace("\\", "/").lower()
        if cited in haystack:
            return ev
    return None


def _candidates_for(citation: Citation, chain: ProvenanceChain) -> list:
    """Evidence among which this citation is looked up.

        `runtime` is measured, not gathered: the process reading its own interpreter, version, pid and cwd. `web` adds internet search hits and `file` adds file-search output: both are evidence about the citation's subject obtained other than by direct opening."""
    candidates = list(chain.by_kind(citation.expected_kind))  # type: ignore[arg-type]
    extra: list = []
    if citation.prefix == "runtime":
        extra = runtime_evidence_pool()
    elif citation.prefix == "web":
        extra = list(chain.by_kind("web_search_hit"))  # type: ignore[arg-type]
    elif citation.prefix == "file":
        extra = _search_output_evidence(chain)
    if not extra:
        return candidates
    seen = {ev.id for ev in candidates}
    return candidates + [ev for ev in extra if ev.id not in seen]


def match_citation(citation: Citation, chain: ProvenanceChain) -> Evidence | None:
    candidates = _candidates_for(citation, chain)
    if not candidates:
        return None
    if not citation.body:
        return candidates[0]
    body_lower = citation.body.lower()
    if citation.prefix == "file":
        # A file citation is compared AS A PATH: one file may be written
        # `x.txt`, `./x.txt` or with the workspace prefix, and those are the
        # same file. The window is decided first: when two reads of one file
        # are in the chain, `[file:x.py:200-215]` must land on lines 200-215,
        # not on whichever read came first.
        for ev in candidates:
            if same_file(citation.body, ev.source_id, same_window=True):
                return ev
        for ev in candidates:
            if same_file(citation.body, ev.source_id):
                return ev
        hit = _search_names_file(_search_output_evidence(chain), citation.body)
        if hit is not None:
            return hit
    for ev in candidates:
        # Every other prefix keeps the substring rule — a web query or a
        # memory id is a string, not a path.
        if citation.prefix == "file":
            continue
        if body_lower in ev.source_id.lower():
            return ev
    if citation.prefix in _NO_TOKEN_FALLBACK_PREFIXES:
        if citation.prefix == "web":
            body_lower_full = citation.body.lower()
            body_is_url = body_lower_full.startswith(("http://", "https://")) or "://" in body_lower_full
            if not body_is_url:
                search_only = [ev for ev in candidates if ev.kind == "web_search_hit"]
                if search_only:
                    body_tokens = _tokenise_citation_body(citation.body)
                    if body_tokens:
                        best: Evidence | None = None
                        best_score = 0
                        for ev in search_only:
                            sid_lower = ev.source_id.lower()
                            score = sum(1 for tok in body_tokens if tok in sid_lower)
                            if score > best_score:
                                best = ev
                                best_score = score
                        if best_score >= 1:
                            return best
        return None
    body_tokens = _tokenise_citation_body(citation.body)
    if not body_tokens:
        return None
    best: Evidence | None = None
    best_score = 0
    for ev in candidates:
        sid_lower = ev.source_id.lower()
        score = sum(1 for tok in body_tokens if tok in sid_lower)
        if score > best_score:
            best = ev
            best_score = score
    if best_score >= 1:
        return best
    return None


def best_run_of_source(ev: Evidence, chain: ProvenanceChain, claim: str) -> Evidence:
    """Of the runs of ONE source, the one that names the claim's literals.

        A tool has one address for all calls and `match_citation` returns the first. Evidence with ANOTHER address is excluded: foreign text does not launder a value."""
    from .verifier_absence import literals_absent_from_excerpt

    runs = [claim_view(e, claim) for e in chain.evidences if e.source_id == ev.source_id and e.kind == ev.kind]
    if len(runs) < 2:
        return claim_view(ev, claim)
    return min(runs, key=lambda e: len(literals_absent_from_excerpt(claim, e.excerpt or "", e.source_id or "")))


#: Window around a claim word and the ceiling of the excerpt assembled for a claim.
_VIEW_RADIUS = 300
_VIEW_BUDGET = 2400
_VIEW_MAX_HITS = 40
_ANCHOR_RE = re.compile(r"\d+(?:[.,\-]\d+)*|[^\W\d_]{3,}")


def _anchor_hits(claim: str, low: str) -> dict[str, list[int]]:
    """Where the claim's numbers and words stand in the text (a number in all its spellings)."""
    hits: dict[str, list[int]] = {}
    for anchor in {m.group(0).lower() for m in _ANCHOR_RE.finditer(_CITATION_RE.sub(" ", claim))}:
        forms = _figure_variants(anchor) if anchor[0].isdigit() else {anchor}
        found = sorted({m.start() for form in forms for m in re.finditer(re.escape(form), low)})
        if found:
            hits[anchor] = found[:_VIEW_MAX_HITS]
    return hits


def _best_windows(hits: dict[str, list[int]], size: int) -> list[tuple[int, int]]:
    """Windows jointly covering the most DISTINCT claim words; rare words and numbers weigh more."""
    from bisect import bisect_left

    weight = {a: (3.0 if a[0].isdigit() else 1.0) / len(pos) for a, pos in hits.items()}
    span = 2 * _VIEW_RADIUS

    def count(anchor: str, lo: int) -> int:
        # Whole words only: a word cut by the window edge is not in the excerpt.
        pos = hits[anchor]
        return bisect_left(pos, lo + span - len(anchor) - 1) - bisect_left(pos, lo)

    left = sorted(hits)
    windows: set[tuple[int, int]] = set()
    while left and len(windows) * span < _VIEW_BUDGET:
        starts = sorted({max(0, p - _VIEW_RADIUS) for a in left for p in hits[a]})
        # On a tie, the window with more claim words by count (a dense spot, not an edge).
        lo = max(starts, key=lambda s: (sum(weight[a] for a in left if count(a, s)),
                                        sum(count(a, s) for a in left)))
        covered = [a for a in left if count(a, lo)]
        if not covered:
            break
        windows.add((lo, min(size, lo + span)))
        left = [a for a in left if a not in covered]
    return sorted(windows)


def claim_view(ev: Evidence, claim: str) -> Evidence:
    """Evidence with an excerpt from the places in the WHOLE source text where claim words stand.

        The text is verbatim: what is not in the source is not here (the first 800 chars of a page are menu)."""
    from dataclasses import replace

    text = ev.source_text
    if not text or text == ev.excerpt:
        return ev
    if len(text) <= _VIEW_BUDGET:
        return replace(ev, excerpt=text + ("...[truncated]" if ev.excerpt.endswith("...[truncated]") else ""))
    windows = _best_windows(_anchor_hits(claim, text.lower()), len(text)) or [(0, 2 * _VIEW_RADIUS)]
    return replace(ev, excerpt="\n…\n".join(text[lo:hi] for lo, hi in windows) + "...[truncated]")


def _semantic_nli_check(claim: str, excerpt: str, llm: Any, *, max_chars: int = _MAX_EXCERPT_FOR_NLI) -> bool:
    try:
        prompt = f"Source excerpt:\n{excerpt[:max_chars]}\n\nClaim: {claim[:300]}\n\nDoes the source excerpt support the claim? Answer yes or no."
        answer = llm.complete(system=_NLI_SYSTEM, user=prompt, max_tokens=4, temperature=0.0)
        return answer.strip().lower().startswith("yes")
    # FAIL CLOSED, deliberately: this is the NLI support check, and an
    # unreachable model must never be read as "the source supports the
    # claim". `False` means unsupported, which is the safe verdict —
    # the opposite default would launder unverified claims on any outage.
    except Exception:  # noqa: BLE001
        return False


def _independent(ev: Evidence) -> bool:
    """Same rule as every citation road (core.evidence.evidence_is_independent)."""
    from core.evidence import evidence_is_independent

    return evidence_is_independent(ev)


def _find_semantic_support(claim: str, chain: ProvenanceChain, llm: Any) -> Evidence | None:
    # Uncited support may come only from INDEPENDENT evidence: the agent's own memory or files cannot verify a sentence just because the model agrees.
    candidates = sorted((e for e in chain.evidences if _independent(e)),
                        key=lambda e: e.confidence, reverse=True)
    for ev in candidates:
        if not ev.excerpt:
            continue
        if _semantic_nli_check(claim, ev.excerpt, llm):
            return ev
    return None


def _find_structured_support(claim: str, chain: ProvenanceChain) -> Evidence | None:
    from core.structured_facts import claim_supported_by, extract_facts
    candidates = [ev for ev in chain.evidences if ev.kind == "tool_output" and _independent(ev)]
    candidates.sort(key=lambda e: e.confidence, reverse=True)
    for ev in candidates:
        if not ev.excerpt:
            continue
        facts = extract_facts(ev.excerpt)
        if facts.is_empty():
            continue
        if claim_supported_by(claim, facts):
            return ev
    return None


def _tool_citation_for(ev: Evidence) -> str:
    sid = ev.source_id or ""
    body = sid[len("tool_output:"):] if sid.startswith("tool_output:") else sid or "structured"
    return f"[verified:tool:{body}]"


def _is_derivative_subagent_evidence(ev: Evidence) -> bool:
    """Is this evidence merely a sub-agent's own synthesis (a witness)?

    The parent loop embeds only a sub-agent's *answer text* (via
    ``SubAgentRunResult.to_evidence_text()``) into its provenance chain —
    never the sub-agent's raw file / web evidences. So the parent can never
    independently inspect what a sub-agent looked at; it holds only the
    child's prose conclusion.
    """
    excerpt = ev.excerpt or ""
    if _SUBAGENT_META_RE.search(excerpt) is not None:
        return True
    sid = ev.source_id or ""
    return bool("subagent_" in sid or sid == "tool_output:spawn_subagent")




# ── R2/R3 (2026-08-13, probe_r1) ──────────────────────────────────────────────

_COUNT_WORDS: dict[str, int] = {
    "один": 1, "одна": 1, "две": 2, "два": 2, "три": 3, "четыре": 4,
    "пять": 5, "шесть": 6, "семь": 7, "восемь": 8, "девять": 9, "десять": 10,
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}


#: An item the sentence itself rejected is excluded, not enumerated.
_ENUM_EXCLUDED_RE = re.compile(
    r"не\s+подход|не\s+подошл|не\s+входит|исключ|not\s+match|excluded",
    re.IGNORECASE,
)
#: An item that is entirely a connective word, not a list member: "(for example, apple, pear)" lists two fruits, not three.
_DISCOURSE_ONLY_RE = re.compile(
    r"^(?:например|в\s+частности|то\s+есть|скажем|в\s+том\s+числе|а\s+именно|"
    r"e\.g\.?|i\.e\.?|for\s+example|for\s+instance|namely)$",
    re.IGNORECASE,
)
#: Digit group inside a fraction ("0.6", "0,6", "1.5"): a decimal is one token, its fractional part is never a count.
#: A list that declares itself incomplete ("... and others") cannot refute "41 files".
_PARTIAL_LIST_RE = re.compile(
    r"(?:\bи\s+(?:др|пр)\.?|\bи\s+т\.?\s*(?:д|п)\.?|\bи\s+так\s+далее|\bи\s+прочие|"
    r"\betc\.?|\band\s+so\s+on|\band\s+others|…|\.\.\.)\s*$",
    re.IGNORECASE,
)
_DECIMAL_PART_BEFORE_RE = re.compile(r"\d[.,]$")
_DECIMAL_PART_AFTER_RE = re.compile(r"^[.,]\d")


#: A number followed by a month or year, or preceded by a range dash, is a DATE, not a claimed count.
_DATE_LIKE_NUMBER_RE = re.compile(
    r"(?:[–—-]\s*)?\b(\d{1,2})\s+(?:"
    r"январ|феврал|март|апрел|ма[йя]|июн|июл|август|сентябр|октябр|ноябр|декабр|"
    r"jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*|"
    r"\b\d{1,2}\s+\d{4}\b|\b\d{1,2}[.:]\d{2}\b",
    re.IGNORECASE,
)


def _number_is_a_date(text: str, m: Any) -> bool:
    """The matched number sits in a date: a month follows, a range dash
    precedes, or a year / clock time follows."""
    start = max(0, m.start() - 3)
    window = text[start:m.start() + 24]
    if _DATE_LIKE_NUMBER_RE.search(window):
        return True
    before = text[max(0, m.start() - 3):m.start()]
    return bool(re.search(r"[–—-]\s*$", before))


#: A clarifying parenthesis ("according to the search - ...", "of which ...", "total ...") is not an item list. A list with quantities ("rotor-33: 5 pcs, gasket-9: 0 pcs") stays a list.
_ITEM_CARRIES_A_COUNT_RE = re.compile(
    r"(?i)^(?:по\s+данным|согласно|всего|итого|according|in\s+total)|\bиз\s+них\b|\bof\s+which\b")


#: A parenthesis belongs to the NEAREST number before it; splitting items on commas INSIDE code or mistaking a line number or a breakdown for a count gave false refutations.
_PAREN_RE = re.compile(r"\(([^()]{2,200})\)")
_COUNT_TOKEN_RE = re.compile(
    r"\b(\d{1,2})\b|\b(" + "|".join(_COUNT_WORDS) + r")\b", re.IGNORECASE)
_NOUN_THEN_GAP_RE = re.compile(r"\s+[\wЀ-ӿ-]+[^().\n]{0,60}$")
#: Address number (line, item, step, chapter), not a quantity.
_ADDRESS_BEFORE_RE = re.compile(
    r"(?i)(?:строк\w*|line|lines|стр\.|пункт\w*|№|#|§|глав\w*|раздел\w*|"
    r"chapter|section|step|шаг\w*)\s*$")
#: An item that starts with a quantity is a breakdown, not an item of the list.
_ITEM_STARTS_WITH_A_COUNT_RE = re.compile(
    r"(?i)^(?:\d+|" + "|".join(_COUNT_WORDS) + r")\b")
_OPENERS, _CLOSERS = "([{«", ")]}»"


def _top_level_items(inner: str) -> list[str]:
    """Parenthesis items: split on `,`/`;` only outside code, brackets and quotes."""
    items, buf, depth, in_code = [], [], 0, False
    for ch in inner:
        if ch == "`":
            in_code = not in_code
        elif not in_code and ch in _OPENERS:
            depth += 1
        elif not in_code and ch in _CLOSERS and depth:
            depth -= 1
        elif ch in ",;" and not in_code and depth == 0:
            items.append("".join(buf).strip())
            buf = []
            continue
        buf.append(ch)
    items.append("".join(buf).strip())
    return [item for item in items if item]


def _is_decimal_part(text: str, token: re.Match[str]) -> bool:
    """A digit group that is part of a decimal number ("0.6", "0,6"), not a count."""
    if not token.group(1):
        return False
    return bool(_DECIMAL_PART_BEFORE_RE.search(text[:token.start()])
                or _DECIMAL_PART_AFTER_RE.match(text[token.end():]))


def _claimed_count_before(text: str, paren_start: int) -> int:
    """The count a parenthesis refers to: the nearest number before it followed by a noun; 0 means none (date, address, not a count)."""
    lead_from = max(0, paren_start - 120)
    # A period cuts a sentence but not a fraction (a cut "0.6" turned its six into a promised count).
    lead = re.split(r"(?<!\d)\.|\.(?!\d)|[\n()]", text[lead_from:paren_start])[-1]
    offset = paren_start - len(lead)
    # Nearest number followed by a noun: in "three items with qty less than 6 (...)" six is a comparison threshold, the count is "three".
    token = next((t for t in reversed(list(_COUNT_TOKEN_RE.finditer(lead)))
                  if _NOUN_THEN_GAP_RE.match(lead[t.end():])
                  and not _is_decimal_part(lead, t)), None)
    if token is None:
        return 0
    at = _COUNT_TOKEN_RE.match(text, offset + token.start())
    if at is None or (at.group(1) and _number_is_a_date(text, at)):
        return 0
    if _ADDRESS_BEFORE_RE.search(text[:offset + token.start()]):
        return 0
    return int(token.group(1)) if token.group(1) else _COUNT_WORDS.get(token.group(2).lower(), 0)


def enumeration_count_reason(text: str) -> Any:
    """R2: the claimed count against the sentence's OWN enumeration."""
    from .verifier_models import ClaimReason

    text = text or ""
    for paren in _PAREN_RE.finditer(text):
        claimed = _claimed_count_before(text, paren.start())
        if claimed < 2:
            continue
        if _PARTIAL_LIST_RE.search(paren.group(1).strip()):
            continue
        items = _top_level_items(paren.group(1))
        if len(items) < 2 or any(
                _ITEM_CARRIES_A_COUNT_RE.search(item) or _ITEM_STARTS_WITH_A_COUNT_RE.search(item)
                for item in items):
            continue
        counted = sum(1 for item in items
                      if not _ENUM_EXCLUDED_RE.search(item)
                      and not _DISCOURSE_ONLY_RE.match(item.strip(" \t,;:—-")))
        if counted != claimed:
            return ClaimReason(
                code="count_mismatch",
                expected=str(claimed),
                actual=str(counted),
                explanation=(
                    "заявленный счёт противоречит собственному перечислению "
                    "в том же предложении"
                ),
                computed_from="собственное перечисление куска",
            )
    return None


def literal_covered_by_union(
    expected: str,
    evidences: list[Any],
    chain_source_ids: list[str] | None = None,
) -> bool:
    """R3: the literal is covered by the UNION of cited evidence (text + address)."""
    # `expected` is a join of up to three literals with ", " (see absent_literal_reason): searching the join as one substring found nothing.
    needles = [n.strip().lower() for n in (expected or "").split(",") if n.strip()]
    if not needles:
        return False
    haystacks = [
        (getattr(ev, "excerpt", "") or "").lower()
        + "\n" + (getattr(ev, "source_id", "") or "").lower()
        for ev in evidences or []
    ]
    # A chunk cited ONE file while naming others read in the same turn. A literal equal to the ADDRESS of a chain's evidence is not invented, its referent was opened. Addresses only, not texts: foreign content would launder values.
    haystacks.extend((sid or "").lower() for sid in chain_source_ids or [])
    return all(any(n in h for h in haystacks) for n in needles)
