from __future__ import annotations

import re

CITATION_PREFIXES: dict[str, str] = {
    "file": "file",
    "web": "web_page",
    "search": "web_search_hit",
    "test": "test_result",
    "log": "log_event",
    "shell": "shell_output",
    "tool": "tool_output",
    "diff": "diff_preview",
    "memory": "memory",
    "user": "user_explicit",
    "general-knowledge": "llm_claim",
}

SELF_DECLARED_PREFIXES: frozenset[str] = frozenset({"general-knowledge"})

_CITATION_RE = re.compile(
    r"\[("
    + "|".join(re.escape(p) for p in CITATION_PREFIXES)
    + r")(?::([^\]\n]+))?\]"
)

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[\.\!\?])\s+|\n+")

_OUTPUT_CONTRACT_HEADERS: frozenset[str] = frozenset({
    "conclusion", "facts", "sources", "confidence",
    "unverified", "safety",
})

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

_STAT_TRIGGER_RE = re.compile(
    r"(?:"
    r"\d+\s*%"
    r"|\d+\s*процент"
    r"|\$\s*\d"
    r"|\d+\s*(?:usd|eur|gbp|долл|руб|rub)\b"
    r"|\b\d+\s*[-–—]\s*\d+\s*(?:week|day|hour|month|year|нед|дн|час|мес|лет|год)"
    r"|\b\d+\s*x\b"
    r"|\bв\s*\d+\s*раз"
    r"|\b(?:most|majority|average|on\s+average)\b"
    r"|\b(?:средний|средняя|среднее|в\s+среднем|большинство)\b"
    r")",
    re.IGNORECASE,
)

_STAT_FIGURE_RE = re.compile(
    r"\d+\s*%"
    r"|\$\s*\d+(?:[.,]\d+)?"
    r"|\b\d+\s*[-–—]\s*\d+\b"
    r"|\b\d+\s*x\b"
    r"|\b\d+(?:[.,]\d+)?\s*(?:usd|eur|gbp|долл|руб|rub)\b"
    r"|\b\d+(?:[.,]\d+)?\b",
    re.IGNORECASE,
)

_STAT_FIGURE_MIN_LEN = 2
_STAT_STRICT_EXEMPT_PREFIXES: frozenset[str] = frozenset({"user", "memory", "general-knowledge"})

_CITATION_BODY_TOKEN_RE = re.compile(r"[:/\\\s,]+")
_NO_TOKEN_FALLBACK_PREFIXES: frozenset[str] = frozenset({"web", "search", "file"})
_TOKEN_STOPWORDS: frozenset[str] = frozenset({
    "http", "https", "www", "ftp",
    "file", "web", "search", "test", "log", "shell", "tool", "diff",
    "memory", "user", "general", "knowledge",
    "web_page", "web_search_hit", "tool_output", "test_result",
    "log_event", "shell_output", "diff_preview",
    "user_explicit", "llm_claim", "unknown",
})
_MIN_TOKEN_LEN = 3

_NLI_SYSTEM = (
    "You are a strict fact-checker. "
    "Answer ONLY with the single word 'yes' or 'no'. "
    "Do not add punctuation or explanation."
)
_MAX_EXCERPT_FOR_NLI = 600

_SUBAGENT_META_RE = re.compile(
    r"\[subagent-meta\s+external_evidence_count=(\d+)\s+external_kinds=([^\]]*)\]"
)

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
DISCLAIMER_SESSION_MEMORY = (
    "[note] This answer draws on the conversation history from this "
    "session (prior turns). No new external sources were consulted."
)
DISCLAIMER_ALL_SELF_DECLARED = (
    "[note] Every claim above is marked [declared:general-knowledge] — "
    "the model honestly admits these come from its prior training, not "
    "from any source the agent verified this cycle."
)
