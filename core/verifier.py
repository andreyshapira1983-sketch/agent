"""Verifier: marks each synthesized claim [unverified] or [verified:<kind>:<source>] against the cycle's ProvenanceChain.

Citation grammar: [file:<path>] [web:<url>] [search:<query>] (weak) [test:<cmd>] [log:<trace_id>]
[shell:<cmd>] [tool:<name>] [diff:<path>] [memory:<mem_id>] [user].

A structural pass (sentence split + substring match on source_id) always runs; an optional
NLI pass (``llm`` passed to :func:`verify`) re-checks ``cited_but_unmatched`` chunks against all evidence excerpts.
"""
from __future__ import annotations

from typing import Any

from core.evidence import Evidence, ProvenanceChain, make_evidence

from .verifier_core import verify
from .verifier_models import Citation, ClaimChunk, VerificationReport
from .verifier_patterns import (
    _BARE_LIST_MARKER_RE,
    _CITATION_BODY_TOKEN_RE,
    _CITATION_RE,
    _MAX_EXCERPT_FOR_NLI,
    _MD_HEADING_RE,
    _MIN_TOKEN_LEN,
    _NLI_SYSTEM,
    _NO_TOKEN_FALLBACK_PREFIXES,
    _NON_CLAIM_SECTIONS,
    _OUTPUT_CONTRACT_HEADER_RE,
    _OUTPUT_CONTRACT_HEADERS,
    _SENTENCE_SPLIT_RE,
    _STAT_FIGURE_MIN_LEN,
    _STAT_FIGURE_RE,
    _STAT_STRICT_EXEMPT_PREFIXES,
    _STAT_TRIGGER_RE,
    _SUBAGENT_META_RE,
    _TOKEN_STOPWORDS,
    CITATION_PREFIXES,
    DISCLAIMER_ALL_SELF_DECLARED,
    DISCLAIMER_FULLY_UNVERIFIED,
    DISCLAIMER_NO_CHAIN,
    DISCLAIMER_SESSION_MEMORY,
    SELF_DECLARED_PREFIXES,
)
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
    _tool_citation_for,
    extract_statistical_figures,
    extract_unresolved_web_urls,
    is_statistical_claim,
    is_structural_chunk,
    match_citation,
    parse_citations,
    split_into_chunks,
)

__all__ = [
    "CITATION_PREFIXES",
    "DISCLAIMER_ALL_SELF_DECLARED",
    "DISCLAIMER_FULLY_UNVERIFIED",
    "DISCLAIMER_NO_CHAIN",
    "DISCLAIMER_SESSION_MEMORY",
    "SELF_DECLARED_PREFIXES",
    "_BARE_LIST_MARKER_RE",
    "_CITATION_BODY_TOKEN_RE",
    "_CITATION_RE",
    "_MAX_EXCERPT_FOR_NLI",
    "_MD_HEADING_RE",
    "_MIN_TOKEN_LEN",
    "_NLI_SYSTEM",
    "_NON_CLAIM_SECTIONS",
    "_NO_TOKEN_FALLBACK_PREFIXES",
    "_OUTPUT_CONTRACT_HEADERS",
    "_OUTPUT_CONTRACT_HEADER_RE",
    "_SENTENCE_SPLIT_RE",
    "_STAT_FIGURE_MIN_LEN",
    "_STAT_FIGURE_RE",
    "_STAT_STRICT_EXEMPT_PREFIXES",
    "_STAT_TRIGGER_RE",
    "_SUBAGENT_META_RE",
    "_TOKEN_STOPWORDS",
    "Any",
    "Citation",
    "ClaimChunk",
    "Evidence",
    "ProvenanceChain",
    "VerificationReport",
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
    "extract_statistical_figures",
    "extract_unresolved_web_urls",
    "is_statistical_claim",
    "is_structural_chunk",
    "make_evidence",
    "match_citation",
    "parse_citations",
    "split_into_chunks",
    "verify",
]
