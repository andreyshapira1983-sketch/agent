"""MVP-14.4 — Verifier.

The whole point: **LLM is not an oracle, it is a draft writer.**
After the synthesizer produces an answer, the Verifier examines every
claim, checks whether it has a citation, and whether that citation
resolves to a real :class:`Evidence` record in the cycle's
:class:`ProvenanceChain`. Claims without a citation are marked
``[unverified]``; claims with a valid citation get a stronger
``[verified:<kind>:<source>]`` annotation that survives into the
final answer.

Citation grammar (LLM is instructed to follow this in
``core.planner.SYNTHESIZER_SYSTEM`` / equivalents):

  - ``[file:<path>]``         — workspace file content
  - ``[web:<url>]``            — fetched web page (kind=web_page)
  - ``[search:<query>]``       — search pointer (kind=web_search_hit, weak!)
  - ``[test:<cmd>]``           — pytest result
  - ``[log:<trace_id>]``       — JSONL log event
  - ``[shell:<cmd>]``          — shell_exec stdout
  - ``[tool:<name>]``          — generic tool output (current_time, etc.)
  - ``[diff:<path>]``          — diff_file preview
  - ``[memory:<mem_id>]``      — memory record
  - ``[user]``                 — user explicit directive

The first structural pass (sentence-level splitting + substring matching
on source_id) is always run. An optional *semantic NLI pass* can be
enabled by passing an ``llm`` instance to :func:`verify`. When enabled,
``cited_but_unmatched`` chunks are re-examined: the LLM is asked whether
any evidence excerpt in the chain actually supports the claim text. If
a supporting excerpt is found the verdict is upgraded to ``verified``
without requiring an exact citation-body / source_id match. This handles
URL format mismatches, partial paths, and lightly-paraphrased references.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from core.evidence import Evidence, ProvenanceChain, make_evidence


# ---------------------------------------------------------------------------
# Citation grammar
# ---------------------------------------------------------------------------

# Map citation prefix -> EvidenceKind tag expected on the matched record.
# Prefixes are intentionally short to stay LLM-friendly.
#
# `general-knowledge` is a SPECIAL prefix: the LLM uses it to honestly
# admit "this fact is from my training data, not from sources I
# gathered in this run". It will never match an evidence record
# (we don't pre-seed an llm_claim record into the chain), so the
# Verifier treats it as a third verdict — `self_declared` — neither
# `verified` nor `unverified`. The LLM gets credit for the honesty;
# the user gets a clear visual signal `[declared:general-knowledge]`.
CITATION_PREFIXES: dict[str, str] = {
    "file":              "file",
    "web":               "web_page",
    "search":            "web_search_hit",
    "test":              "test_result",
    "log":               "log_event",
    "shell":             "shell_output",
    "tool":              "tool_output",
    "diff":              "diff_preview",
    "memory":            "memory",
    "user":              "user_explicit",
    "general-knowledge": "llm_claim",
}

# Prefixes the Verifier handles as `self_declared` (LLM owns up to
# using prior knowledge). The chain almost never carries an
# `llm_claim` evidence, so the normal `match_citation` path won't
# resolve these. Special-casing keeps the rest of the matcher simple.
SELF_DECLARED_PREFIXES: frozenset[str] = frozenset({"general-knowledge"})

# Inline citation regex: matches `[file:foo.txt]`, `[web:https://...]`,
# `[user]` (no source body), etc. Citation bodies can carry slashes,
# dashes, dots, colons (URLs), letters, digits, underscores — but NOT
# closing brackets or newlines (so we stay greedy-safe).
_CITATION_RE = re.compile(
    r"\[("
    + "|".join(re.escape(p) for p in CITATION_PREFIXES)
    + r")(?::([^\]\n]+))?\]"
)

# Sentence splitter — naive but deterministic. We split on `.`, `!`,
# `?`, and newline, keeping the terminator. Markdown-style lists
# (`- item`) become individual chunks because newlines split them.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[\.\!\?])\s+|\n+")

# Output Contract section headers — when a chunk is JUST one of these
# (optionally with a markdown `#` / `##` prefix), it carries no claim
# and must not be marked `[unverified]`. Centralised here so the
# synthesizer prompt and the Verifier agree on what counts as
# structural noise.
_OUTPUT_CONTRACT_HEADERS: frozenset[str] = frozenset({
    "conclusion", "facts", "sources", "confidence",
    "unverified", "safety",
})

# A chunk is structural (non-claim) when it matches one of:
#   * a markdown heading: `# Title`, `## Subtitle`, ...
#   * an Output Contract header: `Conclusion:` / `Facts:` / ...
#   * a bare list marker / enumeration: `-`, `*`, `1.`, `2)`, ...
#   * an empty bracketed token (LLM artefact): `[]`
_MD_HEADING_RE = re.compile(r"^#{1,6}\s+\S.*$")
_BARE_LIST_MARKER_RE = re.compile(r"^([-*+]|\d+[\.\)])\s*$")
_OUTPUT_CONTRACT_HEADER_RE = re.compile(
    r"^(?:#{1,6}\s+)?(?:\*\*)?("
    + "|".join(_OUTPUT_CONTRACT_HEADERS)
    + r")\s*:?(?:\*\*)?\s*$",
    re.IGNORECASE,
)
_NON_CLAIM_SECTIONS: frozenset[str] = frozenset({
    "sources",
    "confidence",
    "unverified",
    "safety",
})


def _output_contract_header_name(text: str) -> str | None:
    stripped = (text or "").strip()
    match = _OUTPUT_CONTRACT_HEADER_RE.match(stripped)
    if not match:
        return None
    return match.group(1).casefold()


def is_structural_chunk(text: str) -> bool:
    """A chunk that is just structural scaffolding — section header,
    bullet marker, markdown heading. Verifier never marks these as
    `[unverified]` because they carry no factual claim."""
    if not text or not text.strip():
        return True
    stripped = text.strip()
    if _output_contract_header_name(stripped) is not None:
        return True
    if _MD_HEADING_RE.match(stripped):
        # A markdown heading WITHOUT a citation is structural. A heading
        # WITH a citation is unusual but legitimate ("# Result [file:x]"
        # — answer to a yes/no question). We keep those as claims.
        if "[" not in stripped:
            return True
    if _BARE_LIST_MARKER_RE.match(stripped):
        return True
    return False


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Citation:
    """One parsed inline citation."""
    prefix: str         # "file", "web", "user", ...
    body: str           # the part after the colon (may be "")
    raw: str            # original "[file:foo.txt]" substring
    expected_kind: str  # the EvidenceKind we'd want to match against


@dataclass(frozen=True)
class ClaimChunk:
    """One sentence-or-paragraph claim from the answer."""
    text: str
    citations: tuple[Citation, ...]
    matched_evidence_ids: tuple[str, ...]
    verdict: str        # "verified" | "unverified" | "cited_but_unmatched" | "self_declared"


@dataclass(frozen=True)
class VerificationReport:
    """The full diagnosis. Loop logs this; final answer is taken from
    `annotated_answer`.

    `total_chunks` counts only chunks that carry a claim — structural
    scaffolding (section headers, list markers, markdown headings) is
    bypassed and reported separately as `structural_chunks` so the
    counts add up cleanly:
        total_chunks = verified + unverified + cited_but_unmatched + self_declared

    `malformed_output` is set to True when the LLM response contains NO
    Output Contract section headers at all (no Conclusion / Facts / Sources
    etc.).  This signals that the LLM ignored the required output format;
    callers may want to log a warning or re-prompt.
    """
    total_chunks: int
    verified_chunks: int
    unverified_chunks: int
    cited_but_unmatched_chunks: int
    self_declared_chunks: int
    structural_chunks: int
    chunks: tuple[ClaimChunk, ...]
    annotated_answer: str
    fully_unverified: bool
    chain_was_empty: bool
    disclaimer: str | None = None
    malformed_output: bool = False

    def to_log_payload(self) -> dict[str, Any]:
        return {
            "total_chunks": self.total_chunks,
            "verified_chunks": self.verified_chunks,
            "unverified_chunks": self.unverified_chunks,
            "cited_but_unmatched_chunks": self.cited_but_unmatched_chunks,
            "self_declared_chunks": self.self_declared_chunks,
            "structural_chunks": self.structural_chunks,
            "fully_unverified": self.fully_unverified,
            "chain_was_empty": self.chain_was_empty,
            "malformed_output": self.malformed_output,
            "disclaimer_set": self.disclaimer is not None,
            "verdicts": [c.verdict for c in self.chunks],
        }


# ---------------------------------------------------------------------------
# Verifier
# ---------------------------------------------------------------------------

# Disclaimer copy. Pinned in tests so the surface stays stable.
DISCLAIMER_FULLY_UNVERIFIED = (
    "[note] The answer above is not grounded in any source the agent "
    "consulted this cycle. Treat it as the model's prior knowledge, not "
    "verified information."
)
DISCLAIMER_NO_CHAIN = (
    "[note] No external sources were gathered for this answer. The "
    "response is based on the model's prior knowledge and the "
    "conversation history."
)
# Used when the answer is explicitly grounded in Working Memory (prior
# turns) rather than newly gathered external sources.  This is a
# legitimate and expected code-path for follow-up questions.
DISCLAIMER_SESSION_MEMORY = (
    "[note] This answer draws on the conversation history from this "
    "session (prior turns). No new external sources were consulted."
)
DISCLAIMER_ALL_SELF_DECLARED = (
    "[note] Every claim above is marked [declared:general-knowledge] — "
    "the model honestly admits these come from its prior training, not "
    "from any source the agent verified this cycle."
)


def parse_citations(text: str) -> list[Citation]:
    """Extract every inline citation from a chunk of text."""
    cits: list[Citation] = []
    for m in _CITATION_RE.finditer(text):
        prefix = m.group(1)
        body = (m.group(2) or "").strip()
        cits.append(Citation(
            prefix=prefix,
            body=body,
            raw=m.group(0),
            expected_kind=CITATION_PREFIXES[prefix],
        ))
    return cits


def split_into_chunks(answer: str) -> list[str]:
    """Naive sentence/line splitter. Empty fragments are dropped."""
    if not answer or not answer.strip():
        return []
    parts = _SENTENCE_SPLIT_RE.split(answer)
    return [p.strip() for p in parts if p.strip()]


def extract_unresolved_web_urls(report: "VerificationReport") -> list[str]:
    """MVP-14.5 — collect every cited [web:URL] that the Verifier could
    NOT match against the ProvenanceChain.

    These are the URLs the LLM mentioned in its draft but never actually
    fetched via web_fetch. The AgentLoop feeds this list into a
    `FailureType.unresolved_citation` replan trigger so the planner can
    add the missing fetch step on the next attempt.

    Filters applied here (defence-in-depth — planner sanitiser will
    enforce them again):
      * citation prefix must be ``web`` (search hits / file refs / etc.
        cannot be resolved by web_fetch);
      * body must be a non-empty http(s) URL (no scheme-less hints,
        no placeholder tokens like ``<best URL from search>``).
    Duplicates are removed but original order is preserved so the
    planner sees the URLs in the order the LLM cited them.
    """
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
            if not (lowered.startswith("http://") or lowered.startswith("https://")):
                continue
            if url in seen:
                continue
            seen.add(url)
            ordered.append(url)
    return ordered


# Token splitter for the citation-body fallback matcher (MVP-14.5b).
# Splits on structural separators only — `:`, `/`, `\`, whitespace,
# `,` — and PRESERVES identifier characters like `_` and `-` so
# multi-word identifiers (`run_tests`, `bug_lab`, `tests-foo`)
# survive as single tokens. Keeping the alphabet explicit avoids
# accidentally splitting URLs at `.` or `?`.
_CITATION_BODY_TOKEN_RE = re.compile(r"[:/\\\s,]+")

# Citation prefixes that MUST NOT use the token-overlap fallback.
# URLs in particular tokenise into noisy fragments (`https`, `www`,
# top-level domains) that match across unrelated sites — matching
# `https://unknown.example` against `web_page:https://known.example`
# via the shared `https` token would be wrong. For these prefixes
# we rely exclusively on direct substring matching, which is
# already exact-enough for URLs / file paths.
_NO_TOKEN_FALLBACK_PREFIXES: frozenset[str] = frozenset({"web", "search", "file"})

# Generic / structural tokens that carry no identity. Each one appears
# in nearly every source_id of its kind (`shell_output`, `test_result`,
# the protocol scheme `https`, etc.), so counting it as a hit would let
# almost any citation match almost any record. Filter them out before
# scoring so a hit must be on something MEANINGFUL.
_TOKEN_STOPWORDS: frozenset[str] = frozenset({
    # Scheme / web noise
    "http", "https", "www", "ftp",
    # Citation prefix copies (LLMs often echo the prefix in the body)
    "file", "web", "search", "test", "log", "shell", "tool", "diff",
    "memory", "user", "general", "knowledge",
    # Evidence kind names (we put them at the start of source_id)
    "file", "web_page", "web_search_hit", "tool_output", "test_result",
    "log_event", "shell_output", "diff_preview",
    "user_explicit", "llm_claim", "unknown",
})

# Minimum token length to qualify as a fallback hit. Below this we
# assume the token is too generic (`a`, `b`, `vs`) or an artefact
# of an over-aggressive split.
_MIN_TOKEN_LEN = 3


def _tokenise_citation_body(body: str) -> list[str]:
    """Lowercase + split + drop empty fragments. Used by the fallback
    matcher when the LLM writes citations like `[test:run_tests:bug_lab]`
    whose body has no exact substring in the source_id but whose
    individual tokens (`run_tests`, `bug_lab`) do.

    Stopwords and short fragments are filtered HERE rather than at the
    scoring site so the rule is unit-testable in isolation.
    """
    if not body:
        return []
    raw = [t for t in _CITATION_BODY_TOKEN_RE.split(body.lower()) if t]
    return [
        t for t in raw
        if len(t) >= _MIN_TOKEN_LEN and t not in _TOKEN_STOPWORDS
    ]


def match_citation(
    citation: Citation, chain: ProvenanceChain
) -> Evidence | None:
    """Best-effort match: prefer same-kind evidence, then exact substring
    on source_id, then a token-overlap fallback. Returns ``None`` when
    no candidate carries enough signal — the citation will be marked
    ``cited_but_unmatched`` rather than silently attached to the wrong
    record.

    For ``[user]`` (no body), any user_explicit evidence matches.

    The token-overlap fallback (MVP-14.5b) makes the matcher robust to
    LLMs that compose composite bodies like ``[test:run_tests:bug_lab]``
    — even though the literal string ``run_tests:bug_lab`` may not
    appear in the canonical source_id, the individual tokens ``run_tests``
    and ``bug_lab`` typically do, so we pick the candidate whose
    source_id contains the most of them (≥ 1 token required so an
    accidental empty body never matches).
    """
    candidates = chain.by_kind(citation.expected_kind)  # type: ignore[arg-type]

    # Fallback: the LLM uses [web:query] to cite web_search results because
    # the evidence source label is "web:query" — but CITATION_PREFIXES maps
    # "web" → "web_page" while the actual evidence kind is "web_search_hit".
    # When no web_page evidence exists, retry against web_search_hit so the
    # citation is matched rather than silently dropped as cited_but_unmatched.
    if not candidates and citation.prefix == "web":
        candidates = chain.by_kind("web_search_hit")  # type: ignore[arg-type]

    if not candidates:
        return None
    # No body (e.g. `[user]`): take the most-recent same-kind evidence.
    if not citation.body:
        return candidates[0]
    body_lower = citation.body.lower()
    for ev in candidates:
        if body_lower in ev.source_id.lower():
            return ev

    # Prefixes whose body is a URL / file path skip the token
    # fallback — tokenising URLs produces noisy fragments (TLDs,
    # protocol names) that match across unrelated records.
    if citation.prefix in _NO_TOKEN_FALLBACK_PREFIXES:
        return None

    # Token-overlap fallback (test / shell / log / diff / memory /
    # user prefixes). We tokenise body on a permissive delimiter set
    # and filter stopwords + sub-3-char tokens, then count how many
    # distinct body tokens appear as substrings inside source_id.
    # The winner needs at least one MEANINGFUL token matched — zero
    # matches is indistinguishable from a random kind-only
    # attribution and a stopword-only body produces zero tokens.
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
    # Same kind, body didn't match even by tokens — refuse rather
    # than guess. The Verifier will report cited_but_unmatched and
    # (for [web:...]) the unresolved-citation re-plan loop kicks in.
    return None


# ---------------------------------------------------------------------------
# Semantic NLI helper (optional LLM-based entailment check)
# ---------------------------------------------------------------------------

_NLI_SYSTEM = (
    "You are a strict fact-checker. "
    "Answer ONLY with the single word 'yes' or 'no'. "
    "Do not add punctuation or explanation."
)
_MAX_EXCERPT_FOR_NLI = 600  # chars — keeps prompt within haiku context limit


def _semantic_nli_check(claim: str, excerpt: str, llm: Any) -> bool:
    """Return True when the LLM judges that *excerpt* entails (supports) *claim*.

    The prompt is intentionally minimal to elicit a binary decision without
    chain-of-thought. Uses the lowest-cost model role available via the llm
    ``complete()`` method so latency/cost stay bounded.

    Returns False on any exception so callers always get a clean bool.
    """
    try:
        prompt = (
            f"Source excerpt:\n{excerpt[:_MAX_EXCERPT_FOR_NLI]}\n\n"
            f"Claim: {claim[:300]}\n\n"
            "Does the source excerpt support the claim? Answer yes or no."
        )
        answer = llm.complete(
            system=_NLI_SYSTEM,
            user=prompt,
            max_tokens=4,
            temperature=0.0,
        )
        return answer.strip().lower().startswith("yes")
    except Exception:  # noqa: BLE001
        return False


def _find_semantic_support(
    claim: str, chain: ProvenanceChain, llm: Any
) -> Evidence | None:
    """Search the ProvenanceChain for any Evidence whose excerpt entails *claim*.

    Evidence records are checked in descending confidence order so the
    highest-quality source is returned when multiple records support the claim.
    Returns None when no record passes the NLI check.
    """
    candidates = sorted(chain.evidences, key=lambda e: e.confidence, reverse=True)
    for ev in candidates:
        if not ev.excerpt:
            continue
        if _semantic_nli_check(claim, ev.excerpt, llm):
            return ev
    return None


def _find_structured_support(
    claim: str, chain: ProvenanceChain
) -> Evidence | None:
    """Search the ProvenanceChain for a tool_output Evidence whose
    structured facts support *claim*.

    This bridges the gap between structured tool returns
    (``{"weekday": "Wednesday", "month": 6, "day": 3, "year": 2026}``)
    and natural-language claims paraphrasing them in another locale
    (``"среда, 3 июня 2026 года"``). Substring / source_id matching
    cannot see this kind of equivalence; the structured-facts module
    normalises both sides into a comparable set.

    Returns the first matching Evidence (highest-confidence first), or
    None when no tool_output record's facts overlap with the claim text.
    No LLM calls — pure deterministic matching.
    """
    from core.structured_facts import claim_supported_by, extract_facts  # noqa: PLC0415

    candidates = [
        ev for ev in chain.evidences if ev.kind == "tool_output"
    ]
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
    """Synthesise a ``[tool:<name>]`` citation marker for a tool_output
    evidence record matched via structured facts. Body is the tool name
    extracted from the source_id (``tool_output:current_time`` -> ``current_time``)."""
    sid = ev.source_id or ""
    if sid.startswith("tool_output:"):
        body = sid[len("tool_output:"):]
    else:
        body = sid or "structured"
    return f"[verified:tool:{body}]"


def verify(
    *,
    answer: str,
    chain: ProvenanceChain,
    llm: Any = None,
    user_question: str | None = None,
) -> VerificationReport:
    """Examine an answer and produce a structured verification report.

    When *llm* is provided, ``cited_but_unmatched`` chunks that carry at
    least one non-self-declared citation are re-examined via a lightweight
    NLI prompt: the LLM is asked whether any evidence excerpt in the chain
    supports the claim. A positive answer upgrades the verdict to
    ``verified`` without requiring an exact source_id / citation-body match.
    This handles URL format mismatches, partial paths, and lightly-paraphrased
    references that the substring matcher misses.

    When *user_question* is provided, the operator's own input is treated
    as a primary user_explicit source for verification purposes only —
    a synthetic Evidence record is added to a local chain copy so claims
    citing ``[user]`` (or paraphrasing the prompt text) resolve instead
    of being marked ``cited_but_unmatched``. The original *chain* and any
    downstream consumers (source_registry, evidence_collected event) are
    unaffected — this baseline lives only inside the verifier.

    The annotated_answer is the answer text with citations rewritten:
    ``[file:foo.txt]`` becomes ``[verified:file:foo.txt]`` when matched,
    ``[general-knowledge]`` becomes ``[declared:general-knowledge]``
    (a third "self-declared" verdict — LLM owns up to prior knowledge),
    and chunks with no citation get a trailing ``[unverified]`` tag.

    structural scaffolding (Output Contract headers like ``Conclusion:``,
    list markers, markdown headings) is preserved unchanged — these
    carry no claim and must never be tagged ``[unverified]``.
    """
    # Track whether the externally-supplied chain was empty BEFORE we
    # add the synthetic user_explicit baseline. The "no-chain" disclaimer
    # and downstream gates fire on this value, not on the post-baseline
    # length, so an answer derived purely from operator input is still
    # honestly labelled "no external source consulted".
    chain_empty = len(chain) == 0
    if user_question and user_question.strip():
        # P0: synthesise a user_explicit baseline locally so [user]
        # citations and paraphrases of the prompt resolve. We work on
        # a fresh ProvenanceChain so callers' chain is not mutated and
        # peripheral systems (source_registry, evidence_collected,
        # knowledge_pipeline) keep their existing contracts.
        user_ev = make_evidence(
            kind="user_explicit",
            source_id="user:current_turn",
            obtained_via="user_input",
            claim="Operator-provided text for the current turn",
            excerpt=user_question.strip(),
        )
        local_chain = ProvenanceChain()
        for ev in chain.evidences:
            local_chain.add(ev)
        local_chain.add(user_ev)
        chain = local_chain
    all_chunks_text = split_into_chunks(answer)

    if not all_chunks_text:
        return VerificationReport(
            total_chunks=0, verified_chunks=0,
            unverified_chunks=0, cited_but_unmatched_chunks=0,
            self_declared_chunks=0, structural_chunks=0,
            chunks=(),
            annotated_answer=answer,
            fully_unverified=True,
            chain_was_empty=chain_empty,
            disclaimer=(
                DISCLAIMER_NO_CHAIN if chain_empty
                else DISCLAIMER_FULLY_UNVERIFIED
            ),
        )

    examined_chunks: list[ClaimChunk] = []
    verified = 0
    unverified = 0
    cited_unmatched = 0
    # Counts cited_but_unmatched chunks where ALL citations use the
    # "memory:" prefix — i.e. the LLM grounded the answer in prior
    # turns rather than external sources.  Used to select the right
    # disclaimer (DISCLAIMER_SESSION_MEMORY) for follow-up answers.
    memory_only_unmatched = 0
    self_declared = 0
    structural = 0

    annotated_chunks: list[str] = []
    current_section: str | None = None
    for chunk_text in all_chunks_text:
        # Structural scaffolding: keep verbatim, don't tag, don't count
        # against the claim totals.
        header = _output_contract_header_name(chunk_text)
        if header is not None:
            current_section = header
            structural += 1
            annotated_chunks.append(chunk_text)
            continue

        if is_structural_chunk(chunk_text):
            structural += 1
            annotated_chunks.append(chunk_text)
            continue

        # Output Contract metadata sections are not factual claims.
        # `Sources` lists provenance, `Confidence` names the overall
        # confidence level, `Unverified` lists gaps, and `Safety` reports
        # redaction/safety status. Do not add recursive `[unverified]`
        # tags to these lines; the verifier already captures claim
        # verdicts in the Conclusion/Facts sections.
        if current_section in _NON_CLAIM_SECTIONS:
            structural += 1
            annotated_chunks.append(chunk_text)
            continue

        cits = parse_citations(chunk_text)
        matched_ids: list[str] = []
        verdict: str
        annotated = chunk_text

        if not cits:
            # No citation parsed. Before declaring `unverified`, try
            # structured-facts fallback against tool_output evidence —
            # the LLM may have stated a fact that derives directly from
            # a structured tool return (date, weekday, number) without
            # remembering the citation grammar.
            struct_ev = (
                _find_structured_support(chunk_text, chain)
                if not chain_empty else None
            )
            if struct_ev is not None:
                verdict = "verified"
                verified += 1
                matched_ids.append(struct_ev.id)
                annotated = chunk_text.rstrip() + " " + _tool_citation_for(struct_ev)
            else:
                verdict = "unverified"
                unverified += 1
                annotated = chunk_text.rstrip() + " [unverified]"
        else:
            any_matched = False
            any_self_declared = False
            for c in cits:
                # Special-case the LLM's honest "this is from prior
                # training" admission. We REWRITE to `[declared:...]`
                # so the user sees the admission visually, but we do
                # NOT count this as verified — the model is the source.
                if c.prefix in SELF_DECLARED_PREFIXES:
                    any_self_declared = True
                    body_part = f":{c.body}" if c.body else ""
                    annotated = annotated.replace(
                        c.raw,
                        f"[declared:{c.prefix}{body_part}]",
                    )
                    continue
                ev = match_citation(c, chain)
                if ev is not None:
                    matched_ids.append(ev.id)
                    any_matched = True
                    body_part = f":{c.body}" if c.body else ""
                    annotated = annotated.replace(
                        c.raw,
                        f"[verified:{c.prefix}{body_part}]",
                    )

            # Verdict precedence: verified > self_declared > cited_but_unmatched.
            # A chunk that cites both a real source and general-knowledge
            # is `verified` (the real source wins); a chunk with ONLY
            # general-knowledge is `self_declared`; a chunk with only
            # broken citations stays `cited_but_unmatched`.
            if any_matched:
                verdict = "verified"
                verified += 1
            elif any_self_declared:
                verdict = "self_declared"
                self_declared += 1
            else:
                # Primary heuristic pass failed. Before falling back
                # to the LLM-based NLI, try the deterministic
                # structured-facts matcher: when a tool_output evidence
                # in the chain carries facts that line up with the
                # claim text (date components, weekdays, numbers) we
                # accept it as a verified match without an LLM call.
                struct_ev = (
                    _find_structured_support(chunk_text, chain)
                    if chain.evidences and not chain_empty else None
                )
                if struct_ev is not None:
                    verdict = "verified"
                    verified += 1
                    matched_ids.append(struct_ev.id)
                    for c in cits:
                        if c.prefix not in SELF_DECLARED_PREFIXES:
                            body_part = f":{c.body}" if c.body else ""
                            annotated = annotated.replace(
                                c.raw,
                                f"[verified:{c.prefix}{body_part}]",
                            )
                # Optionally try semantic NLI:
                # ask the LLM whether any evidence excerpt in the chain
                # actually supports this claim text. This handles URL format
                # mismatches, partial paths, and lightly-paraphrased references.
                elif llm is not None and chain.evidences and not chain_empty:
                    sem_ev = _find_semantic_support(chunk_text, chain, llm)
                    if sem_ev is not None:
                        verdict = "verified"
                        verified += 1
                        matched_ids.append(sem_ev.id)
                        # Annotate all non-self-declared citations in the chunk
                        # to show they were resolved via semantic matching.
                        for c in cits:
                            if c.prefix not in SELF_DECLARED_PREFIXES:
                                body_part = f":{c.body}" if c.body else ""
                                annotated = annotated.replace(
                                    c.raw,
                                    f"[verified:{c.prefix}{body_part}]",
                                )
                    else:
                        verdict = "cited_but_unmatched"
                        cited_unmatched += 1
                else:
                    verdict = "cited_but_unmatched"
                    cited_unmatched += 1
                    if cits and all(
                        c.prefix == "memory"
                        or c.prefix in SELF_DECLARED_PREFIXES
                        for c in cits
                    ):
                        memory_only_unmatched += 1

        examined_chunks.append(ClaimChunk(
            text=chunk_text,
            citations=tuple(cits),
            matched_evidence_ids=tuple(matched_ids),
            verdict=verdict,
        ))
        annotated_chunks.append(annotated)

    annotated_answer = "\n".join(annotated_chunks)

    # Detect malformed output: the LLM completely ignored the Output Contract
    # format (no recognised section headers anywhere in the answer).  This is
    # distinct from "all claims unverified" — the LLM may still produce a
    # useful answer, but the caller should log a warning so the operator can
    # adjust the system prompt or model settings.
    headers_found = any(
        _output_contract_header_name((t or "").strip()) is not None
        for t in all_chunks_text
    )
    malformed_output = bool(all_chunks_text) and not headers_found

    # Disclaimer policy (four distinct cases):
    #   * zero verified + zero self_declared
    #     + ALL unmatched citations are memory: prefix (any chain state)
    #       -> agent answered from Working Memory (follow-up question)
    #       -> DISCLAIMER_SESSION_MEMORY (not misleading NO_CHAIN)
    #   * chain_was_empty + zero verified + zero self_declared (other)
    #       -> agent gathered nothing AND model didn't even own up
    #       -> DISCLAIMER_NO_CHAIN
    #   * chain not empty + zero verified + zero self_declared
    #       -> agent had sources but model cited none honestly
    #       -> DISCLAIMER_FULLY_UNVERIFIED
    #   * zero verified + self_declared > 0 (any chain state)
    #       -> model honestly admits prior knowledge; user should know
    #       -> DISCLAIMER_ALL_SELF_DECLARED
    fully_unverified = (verified == 0 and self_declared == 0)
    disclaimer: str | None = None
    if fully_unverified:
        if cited_unmatched > 0 and cited_unmatched == memory_only_unmatched:
            # All cited-but-unmatched chunks reference Working Memory
            # (memory: prefix) — the answer IS grounded in session context,
            # regardless of whether tool-gathered evidence is in the chain.
            disclaimer = DISCLAIMER_SESSION_MEMORY
        elif chain_empty:
            disclaimer = DISCLAIMER_NO_CHAIN
        else:
            disclaimer = DISCLAIMER_FULLY_UNVERIFIED
    elif verified == 0 and self_declared > 0:
        disclaimer = DISCLAIMER_ALL_SELF_DECLARED

    if disclaimer is not None:
        annotated_answer = annotated_answer.rstrip() + "\n\n" + disclaimer

    return VerificationReport(
        total_chunks=len(examined_chunks),
        verified_chunks=verified,
        unverified_chunks=unverified,
        cited_but_unmatched_chunks=cited_unmatched,
        self_declared_chunks=self_declared,
        structural_chunks=structural,
        chunks=tuple(examined_chunks),
        annotated_answer=annotated_answer,
        fully_unverified=fully_unverified,
        chain_was_empty=chain_empty,
        disclaimer=disclaimer,
        malformed_output=malformed_output,
    )
