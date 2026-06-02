"""Dynamic Model Catalog — discovers available models from provider APIs.

NO MODEL NAMES ARE HARDCODED HERE.

The catalog:
  1. Reads env-var overrides first   (AGENT_MODEL_TIER_{LIGHT,STANDARD,DEEP})
  2. Reads config/model_catalog.json  (written by :refresh-models command)
  3. Queries the provider's own API  (anthropic.models.list / openai.models.list)
  4. Returns ""                       (caller falls through to for_role() default)

Tier classification — by naming PATTERN, never by version number:
  LIGHT:    model name contains any of: haiku | mini | nano | small | flash | lite
  DEEP:     OpenAI o-series (regex: o followed by digit — o1, o3, o4, o5, ...)
            OR name contains: opus | thinking | ultra
  STANDARD: everything else (sonnet, gpt-4o, gemini-pro, llama-3, ...)

The OpenAI o-series (reasoning models) are detected by a regex that matches
"o" preceded by a non-word character and followed by a digit, so o4-mini,
o5, o6-mini, ... are automatically DEEP without any code change.
gpt-4o is NOT matched because the "o" there is not followed by a digit.

When a provider releases "claude-haiku-7-3" tomorrow, it is automatically
classified as LIGHT. The code never needs to change.

Cache file: config/model_catalog.json
  - Written by refresh_catalog() / :refresh-models command
  - TTL: AGENT_MODEL_CATALOG_TTL_DAYS (default 7)
  - Schema:
      {
        "updated_at": "<iso8601>",
        "providers": {
          "anthropic": {
            "models": [{"id": "...", "tier": "light|standard|deep"}, ...],
            "tier_best": {"light": "...", "standard": "...", "deep": "..."}
          },
          ...
        }
      }

The "best" model per tier is the one with the highest lexicographic id
(most recent by convention: providers append version numbers that sort
lexicographically in recency order, e.g. claude-haiku-3-5 < claude-haiku-4-0).
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.task_complexity import ComplexityTier

logger = logging.getLogger(__name__)

# ── classification patterns ───────────────────────────────────────────────────
# These are FAMILY NAMES, not version-specific identifiers.

_LIGHT_PATTERNS: tuple[str, ...] = (
    "haiku",    # Anthropic lightweight family
    "mini",     # OpenAI lightweight GPT (gpt-4o-mini, gpt-4.1-mini, …)
    "nano",     # Google / future providers
    "small",    # generic "small" variants
    "flash",    # Google Gemini flash
    "lite",     # generic "lite" variants
)

_DEEP_PATTERNS: tuple[str, ...] = (
    "opus",     # Anthropic flagship
    "thinking", # Anthropic extended thinking
    "ultra",    # generic "ultra"
    # OpenAI o-series is handled by _O_SERIES_RE below, not string patterns.
    # This avoids false positives from "o1" appearing in unrelated names.
)

# Regex for OpenAI o-series reasoning models: o1, o3, o4, o5, ... (and their -mini variants).
# Pattern: "o" preceded by a non-word char (or string start), followed by a digit.
# Matches:  "o1-preview", "o3", "o4-mini", "o5-turbo"
# No match: "gpt-4o", "proto3", "claude-3-opus" (o is not followed by a digit)
_O_SERIES_RE = re.compile(r"(?<![\w])o\d", re.IGNORECASE)

# Env-var names for explicit overrides
_TIER_ENV: dict[ComplexityTier, str] = {
    ComplexityTier.LIGHT:    "AGENT_MODEL_TIER_LIGHT",
    ComplexityTier.STANDARD: "AGENT_MODEL_TIER_STANDARD",
    ComplexityTier.DEEP:     "AGENT_MODEL_TIER_DEEP",
}

# Default cache path; can override via AGENT_MODEL_CATALOG_PATH
_DEFAULT_CATALOG_PATH = Path("config") / "model_catalog.json"
_DEFAULT_TTL_DAYS = 7

# ISO date YYYY-MM-DD inside model names
_ISO_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
# Compact date YYYYMMDD inside model names
_COMPACT_DATE_RE = re.compile(r"(\d{8})")


def _model_recency_key(model_id: str) -> tuple:
    """Return a comparable key so that the newest model sorts last (max wins).

    Key = (major_version, minor_version, year, month, day, model_id)

    Version numbers come FIRST — they reflect the model generation.
    Date is a tiebreaker within the same version (patch/revision date).

    Examples:
      claude-opus-4-8           → (4, 8, 0,    0,  0,  ...)  # version only
      claude-opus-4-5-20251101  → (4, 5, 2025, 11, 1,  ...)  # 4-8 > 4-5 ✓
      claude-sonnet-4-6         → (4, 6, 0,    0,  0,  ...)
      claude-sonnet-4-5-20250929→ (4, 5, 2025, 9,  29, ...)  # 4-6 > 4-5 ✓
      gpt-5.4-nano-2026-03-17   → (5, 4, 2026, 3,  17, ...)
      o4-mini-2025-04-16        → (4, 0, 2025, 4,  16, ...)
      o1-2024-12-17             → (1, 0, 2024, 12, 17, ...)  # 4 > 1 ✓
      gpt-4                     → (4, 0, 0,    0,  0,  ...)
    """
    name = model_id
    year, month, day = 0, 0, 0

    # 1. Extract and remove ISO date YYYY-MM-DD
    m = _ISO_DATE_RE.search(name)
    if m:
        y = int(m.group(1))
        if y >= 2020:  # sanity: it's really a year, not a version number
            year, month, day = y, int(m.group(2)), int(m.group(3))
            name = name[:m.start()] + name[m.end():]

    # 2. Extract and remove compact date YYYYMMDD (only if no ISO date found)
    if not year:
        m = _COMPACT_DATE_RE.search(name)
        if m:
            raw = m.group(1)
            y = int(raw[:4])
            if y >= 2020:
                year, month, day = y, int(raw[4:6]), int(raw[6:8])
                name = name[:m.start()] + name[m.end():]

    # 3. Extract remaining digit groups as version numbers (major, minor)
    parts = [int(d) for d in re.findall(r"\d+", name) if len(d) <= 4]
    major = parts[0] if parts else 0
    minor = parts[1] if len(parts) > 1 else 0

    return (major, minor, year, month, day, model_id)


# ── tier classification ───────────────────────────────────────────────────────

def classify_model(model_id: str) -> ComplexityTier:
    """Classify a model into a tier by its name pattern.

    No version numbers — only family keywords and structural patterns.

    Priority: DEEP is checked before LIGHT so that reasoning models whose
    name also contains "mini" (e.g. ``o3-mini``, ``o4-mini``) are correctly
    classified as DEEP instead of LIGHT.

    OpenAI o-series is detected by a regex matching "o" + digit (o1, o3, o4,
    o5, ...) automatically without listing each version explicitly.
    """
    n = model_id.casefold()
    # OpenAI o-series reasoning models → always DEEP
    if _O_SERIES_RE.search(n):
        return ComplexityTier.DEEP
    # Other flagship/reasoning keywords → DEEP
    if any(p in n for p in _DEEP_PATTERNS):
        return ComplexityTier.DEEP
    # Lightweight family names → LIGHT
    if any(p in n for p in _LIGHT_PATTERNS):
        return ComplexityTier.LIGHT
    return ComplexityTier.STANDARD


# ── catalog I/O ───────────────────────────────────────────────────────────────

def _catalog_path() -> Path:
    override = os.getenv("AGENT_MODEL_CATALOG_PATH", "").strip()
    return Path(override) if override else _DEFAULT_CATALOG_PATH


def _ttl_days() -> int:
    try:
        return int(os.getenv("AGENT_MODEL_CATALOG_TTL_DAYS", str(_DEFAULT_TTL_DAYS)))
    except ValueError:
        return _DEFAULT_TTL_DAYS


def _load_catalog() -> dict[str, Any] | None:
    """Load the cached catalog if it exists and is not expired."""
    path = _catalog_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        updated_at = data.get("updated_at", "")
        if updated_at:
            age = datetime.now(timezone.utc) - datetime.fromisoformat(updated_at)
            if age.days >= _ttl_days():
                logger.debug("model_catalog expired (age=%d days)", age.days)
                return None
        return data
    except Exception as exc:
        logger.warning("model_catalog load error: %s", exc)
        return None


def _save_catalog(data: dict[str, Any]) -> None:
    path = _catalog_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("model_catalog saved → %s", path)


# ── provider API fetchers ─────────────────────────────────────────────────────

def _fetch_anthropic(api_key: str | None = None) -> list[str]:
    """Return all model IDs available on the Anthropic API."""
    import anthropic  # optional dep — only needed at refresh time
    key = api_key or os.getenv("ANTHROPIC_API_KEY", "")
    if not key:
        raise ValueError("ANTHROPIC_API_KEY not set")
    client = anthropic.Anthropic(api_key=key)
    page = client.models.list(limit=100)
    return [m.id for m in page.data]


def _fetch_openai(api_key: str | None = None) -> list[str]:
    """Return chat/completion model IDs available on the OpenAI API.

    Keeps only text-generation models (GPT family and o-series reasoning).
    Excludes: embeddings, image/video generation, audio, realtime streaming,
    fine-tuning base models, and legacy models.
    """
    import openai  # optional dep — only needed at refresh time
    key = api_key or os.getenv("OPENAI_API_KEY", "")
    if not key:
        raise ValueError("OPENAI_API_KEY not set")
    client = openai.OpenAI(api_key=key)
    all_models = client.models.list()

    # Allowlist: only GPT chat models and o-series reasoning models
    _ALLOW_RE = re.compile(r"^(gpt-|chatgpt-|o\d)", re.IGNORECASE)
    # Denylist: non-text-generation capabilities
    _DENY = (
        "embedding", "dall-e", "whisper", "tts", "realtime", "sora",
        "audio", "transcri", "babbage", "davinci", "ada", "curie",
        "text-", "code-", "cushman", "moderation", "search", "image",
    )
    return [
        m.id for m in all_models.data
        if _ALLOW_RE.match(m.id)
        and not any(d in m.id.lower() for d in _DENY)
    ]


_FETCHERS: dict[str, Any] = {
    "anthropic": _fetch_anthropic,
    "openai":    _fetch_openai,
}


# ── public refresh API ────────────────────────────────────────────────────────

def refresh_catalog(
    providers: list[str] | None = None,
    *,
    api_keys: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Query provider APIs, classify models, save cache.

    Parameters
    ----------
    providers : list[str] | None
        Which providers to refresh. Defaults to all in _FETCHERS.
    api_keys : dict[str, str] | None
        Optional explicit API keys; falls back to env vars.

    Returns
    -------
    dict
        The catalog data that was saved.
    """
    providers = providers or list(_FETCHERS.keys())
    api_keys  = api_keys or {}

    catalog: dict[str, Any] = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "providers":  {},
    }

    for provider in providers:
        fetcher = _FETCHERS.get(provider)
        if fetcher is None:
            logger.warning("no fetcher for provider %r — skipped", provider)
            continue
        try:
            model_ids = fetcher(api_keys.get(provider))
        except Exception as exc:
            logger.warning("model fetch failed for %s: %s", provider, exc)
            continue

        classified = [
            {"id": mid, "tier": classify_model(mid).value}
            for mid in model_ids
        ]

        # Best model per tier = newest by date/version extracted from the name
        tier_best: dict[str, str] = {}
        for tier in ComplexityTier:
            candidates = [m["id"] for m in classified if m["tier"] == tier.value]
            if candidates:
                tier_best[tier.value] = max(candidates, key=_model_recency_key)

        catalog["providers"][provider] = {
            "models":    classified,
            "tier_best": tier_best,
        }
        logger.info(
            "model_catalog refreshed provider=%s models=%d tiers=%s",
            provider, len(classified), tier_best,
        )

    _save_catalog(catalog)
    return catalog


# ── main public function ──────────────────────────────────────────────────────

def tier_model_for(tier: ComplexityTier, provider: str) -> str:
    """Return the best available model name for *tier* + *provider*.

    Lookup order:
      1. env var  AGENT_MODEL_TIER_{LIGHT|STANDARD|DEEP}  (operator override)
      2. config/model_catalog.json  (written by :refresh-models)
      3. ""  → caller falls through to for_role() default

    No model names are hardcoded. Returns "" if nothing is configured.
    """
    # 1. env override — always wins
    env_var = _TIER_ENV.get(tier, "")
    if env_var:
        override = os.getenv(env_var, "").strip()
        if override:
            return override

    # 2. catalog cache
    catalog = _load_catalog()
    if catalog:
        provider_data = catalog.get("providers", {}).get(provider, {})
        model = provider_data.get("tier_best", {}).get(tier.value, "")
        if model:
            return model

    # 3. not found — caller uses its own default
    return ""


def catalog_summary() -> dict[str, Any]:
    """Return a human/log-friendly snapshot of the current catalog."""
    catalog = _load_catalog()
    if catalog is None:
        return {"status": "no_catalog", "hint": "run :refresh-models to populate"}
    result: dict[str, Any] = {
        "updated_at": catalog.get("updated_at"),
        "providers": {},
    }
    for provider, pdata in catalog.get("providers", {}).items():
        result["providers"][provider] = {
            "model_count": len(pdata.get("models", [])),
            "tier_best":   pdata.get("tier_best", {}),
        }
    return result
