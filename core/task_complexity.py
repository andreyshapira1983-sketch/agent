"""Task Complexity Assessment — automatic model tier selection.

The agent evaluates task complexity BEFORE making an LLM call and selects
the appropriate model tier:

  LIGHT    → fast, cheap  (haiku-family, mini-family)
  STANDARD → balanced     (sonnet-family, standard GPT)
  DEEP     → powerful     (opus-family, o1/o3-family)

No LLM call is made here — this is a pure rule-based heuristic.

Model names are NEVER hardcoded here.
Actual model selection is in core/model_catalog.py which queries the
provider's own API (anthropic.models.list / openai.models.list) and
classifies results into tiers by naming pattern.

Env override (optional — otherwise auto-discovered):
  AGENT_MODEL_TIER_LIGHT    = <model-id>
  AGENT_MODEL_TIER_STANDARD = <model-id>
  AGENT_MODEL_TIER_DEEP     = <model-id>
"""
from __future__ import annotations

from enum import Enum


# ── tier enum ─────────────────────────────────────────────────────────────────

class ComplexityTier(str, Enum):
    LIGHT    = "light"
    STANDARD = "standard"
    DEEP     = "deep"




# ── role-level overrides ──────────────────────────────────────────────────────

# Roles that are always LIGHT regardless of task text.
_ALWAYS_LIGHT_ROLES: frozenset[str] = frozenset({
    "memory_summary",  # summaries never need a frontier model
})


# ── signals ───────────────────────────────────────────────────────────────────

# Any of these substrings in the task text → DEEP tier.
# Multi-lingual (RU + EN). Substrings, not whole words — so "архитектур"
# matches "архитектура", "архитектуры", "архитектурный", etc.
_DEEP_SIGNALS: frozenset[str] = frozenset({
    # Architecture & system design
    "архитектур",   "architecture",   "system design",  "design system",
    "спроектируй",  "спроектировать",
    # Full audit / security
    "аудит",        "audit",          "security audit", "аудит безопасности",
    "pentest",      "penetration",    "уязвимост",
    # Full implementation / from scratch
    "написать и протестировать",  "write and test",  "write, test",
    "разработать с нуля",         "from scratch",    "полная реализация",
    "full implementation",        "полный",
    # Research & strategic analysis
    "исследуй вес", "research all",   "compare all",    "сравни вс",
    "evaluate all", "оцени вс",       "оцени риск",     "risk assessment",
    "стратеги",     "strategy",       "roadmap",
    # Complex multi-step
    "многоэтапн",   "multi-step",     "multi-phase",    "комплексн",
    "complex analysis", "комплексный анализ",
    # Comprehensive documentation
    "полную документацию", "full documentation", "complete documentation",
})

# Any of these substrings + short text → LIGHT tier.
_LIGHT_SIGNALS: frozenset[str] = frozenset({
    # Greetings
    "привет",   "hello",    "hi",     "hey",    "добрый",
    # Simple Q&A
    "что такое", "what is", "define", "скажи",  "say",
    "объясни в одном", "explain in one",
    # Status & health
    "статус",   "status",   "ping",   "health", "готов",
    "да или нет", "yes or no", "true or false",
    # Quick ops
    "переведи",  "translate", "перевод",
    "суммаризуй", "summarize", "summarise", "summary",
    "кратко",   "briefly",   "quick",  "быстро",
    # Simple lists / lookups
    "перечисли", "list all",  "покажи", "show me",
    "найди одн", "find one",  "get one",
    # Version / info
    "версия",   "version",   "changelog",
})

# Short text under this many characters always → LIGHT (unless DEEP signal).
_SHORT_TEXT_THRESHOLD = 45


# ── public API ────────────────────────────────────────────────────────────────

def assess_complexity(
    text: str,
    *,
    role: str = "planner",
) -> ComplexityTier:
    """Rule-based complexity assessment. No LLM call. O(n) in signal count.

    Priority order
    ──────────────
    1. Role-level overrides  (memory_summary → always LIGHT)
    2. DEEP signals          (substring match in normalized text)
    3. LIGHT signals         (substring match) AND short text
    4. Very short text       (< threshold characters)
    5. Default               → STANDARD
    """
    if role in _ALWAYS_LIGHT_ROLES:
        return ComplexityTier.LIGHT

    if not isinstance(text, str):
        return ComplexityTier.STANDARD
    stripped = text.strip()
    if not stripped:
        return ComplexityTier.STANDARD

    normalized = stripped.casefold()

    # DEEP check first — highest priority
    for signal in _DEEP_SIGNALS:
        if signal in normalized:
            return ComplexityTier.DEEP

    # LIGHT check — only when text is short enough
    is_short = len(stripped) < _SHORT_TEXT_THRESHOLD * 4  # ~180 chars
    if is_short:
        for signal in _LIGHT_SIGNALS:
            if signal in normalized:
                return ComplexityTier.LIGHT

    return ComplexityTier.STANDARD


def tier_label(tier: ComplexityTier) -> str:
    """Human-readable label for logging/display."""
    return {
        ComplexityTier.LIGHT:    "light (fast/cheap)",
        ComplexityTier.STANDARD: "standard (balanced)",
        ComplexityTier.DEEP:     "deep (powerful)",
    }[tier]


# ── live grounding signals ────────────────────────────────────────────────────
#
# When a task mentions time-sensitive keywords the agent must NOT rely on
# its training-data snapshot. Instead the planner should add a web_search
# step to retrieve fresh information before synthesising the answer.
#
# False positives are cheap (one extra search). False negatives produce
# stale / wrong answers. So the signal list is deliberately broad.

_LIVE_GROUNDING_SIGNALS: frozenset[str] = frozenset({
    # ── English temporal / recency ──
    "latest",   "newest",   "current",   "today",    "right now",
    "as of",    "recent",   "recently",
    "just released", "just launched", "just announced", "just dropped",
    "this week", "this month", "this year",
    "2025", "2026", "2027",          # explicit calendar year → always fresh
    # ── English release / news ──
    "release", "released", "launched", "shipped", "new version",
    "changelog", "what's new", "news about",
    "updates on", "updates to", "roadmap",
    # ── English model / AI specific ──
    "latest model", "newest model", "which model", "best model",
    "gpt-5", "o3-mini", "o4", "claude 4", "claude 5",
    "gemini 2", "llama 4", "mistral", "deepseek",
    # ── Russian temporal ──
    "последн",  "актуальн",  "сейчас",   "сегодня",
    "только что", "на данный момент", "в данный момент",
    # ── Russian release / news ──
    "вышел",  "вышла",  "вышло",  "вышли",
    "выпустил", "выпустили", "анонсировал",
    "новост", "что нового",  "обновлен",
    # ── Russian model / AI ──
    "новый claude", "новый gpt", "последняя модель",
    "актуальная модель", "какая модель", "лучшая модель",
})


def needs_live_grounding(text: str) -> bool:
    """Return True when the task likely requires fresh web data.

    Called by the planner to inject a ``[LIVE_GROUNDING=required]`` hint
    into the user prompt, which makes the LLM reliably add ``web_search``
    as the first plan step instead of answering from stale training data.

    This is a keyword heuristic — no LLM call, O(n) in signal count.
    """
    if not isinstance(text, str) or not text.strip():
        return False
    normalized = text.strip().casefold()
    return any(sig in normalized for sig in _LIVE_GROUNDING_SIGNALS)

