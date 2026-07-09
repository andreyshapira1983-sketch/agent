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

from typing import Any

from core.evidence import Evidence, ProvenanceChain, make_evidence
from .verifier_patterns import (
    _BARE_LIST_MARKER_RE,
    _CITATION_BODY_TOKEN_RE,
    _CITATION_RE,
    _MD_HEADING_RE,
    _MAX_EXCERPT_FOR_NLI,
    _MIN_TOKEN_LEN,
    _NON_CLAIM_SECTIONS,
    _NLI_SYSTEM,
    _OUTPUT_CONTRACT_HEADER_RE,
    _OUTPUT_CONTRACT_HEADERS,
    _SENTENCE_SPLIT_RE,
    _STAT_FIGURE_MIN_LEN,
    _STAT_FIGURE_RE,
    _STAT_STRICT_EXEMPT_PREFIXES,
    _STAT_TRIGGER_RE,
    _SUBAGENT_META_RE,
    _TOKEN_STOPWORDS,
    _NO_TOKEN_FALLBACK_PREFIXES,
    CITATION_PREFIXES,
    SELF_DECLARED_PREFIXES,
    DISCLAIMER_ALL_SELF_DECLARED,
    DISCLAIMER_FULLY_UNVERIFIED,
    DISCLAIMER_NO_CHAIN,
    DISCLAIMER_SESSION_MEMORY,
)
from .verifier_models import Citation, ClaimChunk, VerificationReport
from .verifier_utils import (
    _excerpt_supports_figures,
    _find_semantic_support,
    _find_structured_support,
    _is_citation_only_chunk,
    _is_derivative_subagent_evidence,
    _merge_citation_only_chunks,
    _normalise_figure,
    _output_contract_header_name,
    _semantic_nli_check,
    _tokenise_citation_body,
    extract_statistical_figures,
    extract_unresolved_web_urls,
    is_statistical_claim,
    is_structural_chunk,
    match_citation,
    parse_citations,
    split_into_chunks,
    _tool_citation_for,
)
from .verifier_core import verify

__all__ = [
    "Any",
    "Evidence",
    "ProvenanceChain",
    "make_evidence",
    "CITATION_PREFIXES",
    "SELF_DECLARED_PREFIXES",
    "DISCLAIMER_ALL_SELF_DECLARED",
    "DISCLAIMER_FULLY_UNVERIFIED",
    "DISCLAIMER_NO_CHAIN",
    "DISCLAIMER_SESSION_MEMORY",
    "Citation",
    "ClaimChunk",
    "VerificationReport",
    "parse_citations",
    "split_into_chunks",
    "extract_unresolved_web_urls",
    "extract_statistical_figures",
    "is_statistical_claim",
    "is_structural_chunk",
    "match_citation",
    "verify",
    "_BARE_LIST_MARKER_RE",
    "_CITATION_BODY_TOKEN_RE",
    "_CITATION_RE",
    "_MD_HEADING_RE",
    "_MAX_EXCERPT_FOR_NLI",
    "_MIN_TOKEN_LEN",
    "_NON_CLAIM_SECTIONS",
    "_NLI_SYSTEM",
    "_OUTPUT_CONTRACT_HEADER_RE",
    "_OUTPUT_CONTRACT_HEADERS",
    "_SENTENCE_SPLIT_RE",
    "_STAT_FIGURE_MIN_LEN",
    "_STAT_FIGURE_RE",
    "_STAT_STRICT_EXEMPT_PREFIXES",
    "_STAT_TRIGGER_RE",
    "_SUBAGENT_META_RE",
    "_TOKEN_STOPWORDS",
    "_NO_TOKEN_FALLBACK_PREFIXES",
    "_excerpt_supports_figures",
    "_find_semantic_support",
    "_find_structured_support",
    "_is_citation_only_chunk",
    "_is_derivative_subagent_evidence",
    "_merge_citation_only_chunks",
    "_normalise_figure",
    "_output_contract_header_name",
    "_semantic_nli_check",
    "_tokenise_citation_body",
    "_tool_citation_for",
]
