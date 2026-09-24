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
            "tier_best": {"light": "...", "standard": "...", "deep": "..."},
            # Only when this provider could not be ASKED this time and its
            # previous models were kept rather than dropped (MIR-170):
            "carried_over": true, "carried_reason": "...", "carried_from": "..."
          },
          ...
        },
        # Only when at least one provider could not be asked:
        "unreachable": {"anthropic": "AuthenticationError: ..."}
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
    """Return a comparable key so that the newest model sorts last (max wins)."""
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


def _catalog_age_days(data: dict[str, Any]) -> int | None:
    """Age of a loaded catalog in whole days, or None when it is undated."""
    updated_at = data.get("updated_at", "")
    if not updated_at:
        return None
    return (datetime.now(timezone.utc) - datetime.fromisoformat(updated_at)).days


def _load_catalog() -> dict[str, Any] | None:
    """Load the cached catalog if it exists and is not expired."""
    path = _catalog_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        age_days = _catalog_age_days(data)
        if age_days is not None and age_days >= _ttl_days():
            logger.debug("model_catalog expired (age=%d days)", age_days)
            return None
    except Exception as exc:  # noqa: BLE001 — the failure is reported to the caller
        logger.warning("model_catalog load error: %s", exc)
        return None
    else:
        return data


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


def _fetch_deepseek(api_key: str | None = None) -> list[str]:
    """Модели DeepSeek: их API совместим с OpenAI (GET /models).

    24.09 каталог держал только снятый список OpenAI 18.09 — DeepSeek, у
    которого единственный ключ, в каталоге не было вовсе: опрашивать его было
    нечем.
    """
    import openai  # optional dep — only needed at refresh time
    key = api_key or os.getenv("DEEPSEEK_API_KEY", "")
    if not key:
        raise ValueError("DEEPSEEK_API_KEY not set")
    client = openai.OpenAI(api_key=key, base_url="https://api.deepseek.com")
    return [m.id for m in client.models.list().data]


_FETCHERS: dict[str, Any] = {
    "anthropic": _fetch_anthropic,
    "openai":    _fetch_openai,
    "deepseek":  _fetch_deepseek,
}


# ── public refresh API ────────────────────────────────────────────────────────

def discover_catalog(
    providers: list[str] | None = None,
    *,
    api_keys: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Query provider model lists and classify them — WITHOUT writing anything.

    This is the read-only half of :func:`refresh_catalog`. It performs the
    same provider queries and tier classification and returns the resulting
    catalog dict, but it never touches ``config/model_catalog.json``.

    IMPORTANT: querying a provider's model list is a metadata-only, non-
    inference provider call — it runs no LLM inference and generates no
    completion — but it is still a real network/provider call, and it should be
    recorded as provider metadata access rather than an LLM inference call.

    This paragraph used to promise that only an explicit operator request could
    trigger it. That was untrue: ``ensure_fresh_catalog`` fires this from an
    ordinary tier lookup once the cache expires — once per process, gated by
    AGENT_CATALOG_AUTOREFRESH. The behaviour is deliberate (a dead catalog
    silently downgrades every failover), so the promise was corrected rather
    than the code. Worth knowing, because it is how a read-looking call reached
    the network and rewrote config while MIR-170 was being measured.
    """
    providers = providers or list(_FETCHERS.keys())
    api_keys  = api_keys or {}

    catalog: dict[str, Any] = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "providers":  {},
    }
    #: Поставщики, которых спросить НЕ УДАЛОСЬ, и почему. Отсутствие ключа
    #: означает «все спрошенные ответили», а не «никого не спрашивали».
    unreachable: dict[str, str] = {}

    for provider in providers:
        fetcher = _FETCHERS.get(provider)
        if fetcher is None:
            logger.warning("no fetcher for provider %r — skipped", provider)
            continue
        try:
            model_ids = fetcher(api_keys.get(provider))
        except Exception as exc:  # noqa: BLE001 — the failure is reported to the caller
            logger.warning("model fetch failed for %s: %s", provider, exc)
            # «Не смогли спросить» — не «ответил пусто». Раньше поставщик просто
            # пропускался, и сохранение объявляло его безмодельным (MIR-170).
            unreachable[provider] = f"{type(exc).__name__}: {exc}"[:200]
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
            "model_catalog discovered provider=%s models=%d tiers=%s",
            provider, len(classified), tier_best,
        )

    if unreachable:
        catalog["unreachable"] = unreachable
    return catalog


def refresh_catalog(
    providers: list[str] | None = None,
    *,
    api_keys: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Query provider APIs, classify models, and SAVE the cache.

    Thin write wrapper over :func:`discover_catalog`: it performs the same
    read-only discovery and then persists the result to
    ``config/model_catalog.json``. Behaviour is unchanged from before the
    discover/refresh split.
    """
    catalog = discover_catalog(providers, api_keys=api_keys)
    _carry_over_unreachable(catalog)
    _save_catalog(catalog)
    return catalog


def _read_catalog_file() -> dict[str, Any]:
    """Каталог с диска НЕЗАВИСИМО от срока годности.

    `_load_catalog` намеренно отдаёт пустоту просроченному — но перенос нужен
    ровно тогда, когда каталог просрочен и потому обновляется. Просроченные
    сведения о недоступном поставщике лучше, чем объявление его пустым.
    """
    path = _catalog_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001 — перенос не вправе ронять обновление
        logger.warning("model_catalog carry-over read failed: %s", exc)
        return {}


def _carry_over_unreachable(catalog: dict[str, Any]) -> None:
    """Сохранить прежние модели поставщика, которого не смогли спросить.

    Переносится ТОЛЬКО при ошибке запроса. Честный пустой ответ — это ответ, и
    он записывается как есть, иначе поставщик, снявший все модели, остался бы в
    каталоге навсегда. Замер: MIR-170.
    """
    unreachable = catalog.get("unreachable") or {}
    if not unreachable:
        return
    previous = _read_catalog_file()
    prior_providers = previous.get("providers") or {}
    for provider, reason in unreachable.items():
        prior = prior_providers.get(provider) or {}
        if not prior.get("models"):
            continue
        entry = dict(prior)
        entry["carried_over"] = True
        entry["carried_reason"] = reason
        # Дата ПЕРВОГО переноса, а не последнего: иначе запись молодела бы с
        # каждым обновлением и выглядела свежее, чем она есть.
        entry.setdefault("carried_from", previous.get("updated_at"))
        catalog["providers"][provider] = entry
        logger.warning(
            "model_catalog carried over provider=%s models=%d reason=%s",
            provider, len(entry.get("models") or ()), reason,
        )


# ── autorefresh: обновление прежде подстройки ─────────────────────────────────

#: Одна попытка на процесс: неудачное обновление не молотит по сети на каждый
#: вызов маршрутизатора. Тесты сбрасывают флаг через monkeypatch.
_AUTOREFRESH_DONE = False


def _autorefresh_enabled() -> bool:
    raw = (os.getenv("AGENT_CATALOG_AUTOREFRESH") or "").strip().lower()
    return raw not in ("0", "false", "off", "no")


def _credentialed_providers() -> list[str]:
    """Провайдеры, к которым ЕСТЬ ключи: без ключей не бывает и сети."""
    out = []
    if os.getenv("ANTHROPIC_API_KEY", "").strip():
        out.append("anthropic")
    if os.getenv("OPENAI_API_KEY", "").strip():
        out.append("openai")
    if os.getenv("DEEPSEEK_API_KEY", "").strip():
        out.append("deepseek")
    return out


def ensure_fresh_catalog() -> str:
    """Мёртвый каталог сначала пытаются обновить — и только потом обходят."""
    global _AUTOREFRESH_DONE  # noqa: PLW0603 — одна попытка на процесс и есть контракт
    if _load_catalog() is not None:
        return "fresh"
    if not _autorefresh_enabled():
        return "disabled"
    if _AUTOREFRESH_DONE:
        return "already_attempted"
    _AUTOREFRESH_DONE = True
    providers = _credentialed_providers()
    if not providers:
        return "no_credentials"
    try:
        refresh_catalog(providers=providers)
    except Exception as exc:  # noqa: BLE001 — сбой сети не роняет маршрутизацию
        logger.warning("catalog autorefresh failed: %s", exc)
        return f"refresh_failed:{type(exc).__name__}"
    return "refreshed"


# ── main public function ──────────────────────────────────────────────────────

def tier_model_for(tier: ComplexityTier, provider: str) -> str:
    """Return the best available model name for *tier* + *provider*.

    Lookup order:
      1. env var  AGENT_MODEL_TIER_{LIGHT|STANDARD|DEEP}  (operator override)
      2. config/model_catalog.json — a DEAD cache is first refreshed once
         (``ensure_fresh_catalog``), then re-read; refresh before adapt
      3. ""  → caller falls through to for_role() default

    No model names are hardcoded. Returns "" if nothing is configured.
    """
    # 1. env override — always wins
    env_var = _TIER_ENV.get(tier, "")
    if env_var:
        override = os.getenv(env_var, "").strip()
        if override:
            return override

    # 2. catalog cache; смерть кэша — повод обновить, не повод подстроиться
    catalog = _load_catalog()
    if catalog is None:
        ensure_fresh_catalog()
        catalog = _load_catalog()
    if catalog:
        provider_data = catalog.get("providers", {}).get(provider, {})
        model = provider_data.get("tier_best", {}).get(tier.value, "")
        if model:
            return model

    # 3. not found — caller uses its own default
    return ""


def catalog_freshness() -> dict[str, Any]:
    """Is the cache usable, and if not, why — in numbers the operator can act
    on.

    Statuses are kept distinct because they need different actions:
    ``missing`` (never built), ``expired`` (stale, refresh it), ``fresh``,
    ``unreadable`` (corrupt file).
    """
    hint = "run :refresh-models to rebuild the catalog"
    path = _catalog_path()
    if not path.exists():
        return {"status": "missing", "expired": False, "age_days": None,
                "ttl_days": _ttl_days(), "path": str(path), "hint": hint}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        age_days = _catalog_age_days(data)
    # A corrupt or unreadable catalog file must degrade to "unreadable"
    # rather than raise: this is a STATUS query, and callers use it to
    # decide whether to refresh. Raising here would break the refresh path
    # that exists to repair exactly this condition.
    except Exception as exc:  # noqa: BLE001
        return {"status": "unreadable", "expired": False, "age_days": None,
                "ttl_days": _ttl_days(), "path": str(path),
                "error": f"{type(exc).__name__}: {exc}", "hint": hint}

    ttl = _ttl_days()
    expired = age_days is not None and age_days >= ttl
    return {
        "status": "expired" if expired else "fresh",
        "expired": expired,
        "age_days": age_days,
        "ttl_days": ttl,
        "path": str(path),
        "hint": hint,
    }


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


def peer_model_at_same_tier(model: str | None, provider: str) -> str | None:
    """Равный по уровню у нового провайдера, или None — тогда его дефолт."""
    if not model:
        return None
    try:
        return tier_model_for(classify_model(str(model)), provider) or None
    except Exception:  # noqa: BLE001 — каталог ходит в сеть; отказоустойчивость
        return None    # важнее любой его беды, и None здесь = прежнее поведение


def offered_models(provider: str) -> frozenset[str]:
    """Что провайдер предлагает СЕЙЧАС, по последнему наблюдению мира."""
    if catalog_freshness().get("expired"):
        ensure_fresh_catalog()
    catalog = _load_catalog() or {}
    models = (catalog.get("providers", {}).get(provider, {}) or {}).get("models", [])
    return frozenset(
        str(m.get("id") or "") for m in models
        if isinstance(m, dict) and m.get("id")
    )
