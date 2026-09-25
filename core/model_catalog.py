"""Dynamic Model Catalog — discovers provider models and picks the best one per complexity tier.

Lookup: env override (AGENT_MODEL_TIER_*) -> cached config/model_catalog.json -> provider API.
Tiers come from family-name patterns, never version numbers, so new releases need no code change.
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

_LIGHT_PATTERNS: tuple[str, ...] = (
    "haiku",
    "mini",
    "nano",
    "small",
    "flash",
    "lite",
)

_DEEP_PATTERNS: tuple[str, ...] = (
    "opus",
    "thinking",
    "ultra",
    # OpenAI o-series goes through _O_SERIES_RE: a bare "o1" substring gives false positives.
)

# OpenAI o-series ("o3", "o4-mini"): "o" at a word start followed by a digit; not "gpt-4o".
_O_SERIES_RE = re.compile(r"(?<![\w])o\d", re.IGNORECASE)

_TIER_ENV: dict[ComplexityTier, str] = {
    ComplexityTier.LIGHT:    "AGENT_MODEL_TIER_LIGHT",
    ComplexityTier.STANDARD: "AGENT_MODEL_TIER_STANDARD",
    ComplexityTier.DEEP:     "AGENT_MODEL_TIER_DEEP",
}

# Overridable via AGENT_MODEL_CATALOG_PATH.
_DEFAULT_CATALOG_PATH = Path("config") / "model_catalog.json"
_DEFAULT_TTL_DAYS = 7

_ISO_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_COMPACT_DATE_RE = re.compile(r"(\d{8})")


def _model_recency_key(model_id: str) -> tuple:
    """Return a comparable key so that the newest model sorts last (max wins)."""
    name = model_id
    year, month, day = 0, 0, 0

    m = _ISO_DATE_RE.search(name)
    if m:
        y = int(m.group(1))
        if y >= 2020:  # sanity: it's really a year, not a version number
            year, month, day = y, int(m.group(2)), int(m.group(3))
            name = name[:m.start()] + name[m.end():]

    if not year:
        m = _COMPACT_DATE_RE.search(name)
        if m:
            raw = m.group(1)
            y = int(raw[:4])
            if y >= 2020:
                year, month, day = y, int(raw[4:6]), int(raw[6:8])
                name = name[:m.start()] + name[m.end():]

    # Remaining digit groups are the version (major, minor).
    parts = [int(d) for d in re.findall(r"\d+", name) if len(d) <= 4]
    major = parts[0] if parts else 0
    minor = parts[1] if len(parts) > 1 else 0

    return (major, minor, year, month, day, model_id)


# ── tier classification ───────────────────────────────────────────────────────

def classify_model(model_id: str) -> ComplexityTier:
    """Classify a model into a tier by its family-name pattern."""
    n = model_id.casefold()
    if _O_SERIES_RE.search(n):
        return ComplexityTier.DEEP
    if any(p in n for p in _DEEP_PATTERNS):
        return ComplexityTier.DEEP
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
    """Return text-generation model IDs (GPT family and o-series) available on the OpenAI API."""
    import openai  # optional dep — only needed at refresh time
    key = api_key or os.getenv("OPENAI_API_KEY", "")
    if not key:
        raise ValueError("OPENAI_API_KEY not set")
    client = openai.OpenAI(api_key=key)
    all_models = client.models.list()

    _ALLOW_RE = re.compile(r"^(gpt-|chatgpt-|o\d)", re.IGNORECASE)
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
    """Модели DeepSeek через OpenAI-совместимый API (GET /models)."""
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

    Still a real network call (metadata only, no inference). Note that
    ``ensure_fresh_catalog`` reaches it from an ordinary tier lookup once the
    cache expires, once per process (AGENT_CATALOG_AUTOREFRESH).
    """
    providers = providers or list(_FETCHERS.keys())
    api_keys  = api_keys or {}

    catalog: dict[str, Any] = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "providers":  {},
    }
    #: Поставщики, которых не удалось спросить, и почему.
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
            # «Не смогли спросить» — не «ответил пусто» (MIR-170).
            unreachable[provider] = f"{type(exc).__name__}: {exc}"[:200]
            continue

        classified = [
            {"id": mid, "tier": classify_model(mid).value}
            for mid in model_ids
        ]

        # Best per tier = newest by date/version parsed from the name.
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
    """Run :func:`discover_catalog`, carry over unreachable providers, and SAVE the cache."""
    catalog = discover_catalog(providers, api_keys=api_keys)
    _carry_over_unreachable(catalog)
    _save_catalog(catalog)
    return catalog


def _read_catalog_file() -> dict[str, Any]:
    """Каталог с диска независимо от срока годности: перенос нужен как раз для просроченного."""
    path = _catalog_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001 — перенос не вправе ронять обновление
        logger.warning("model_catalog carry-over read failed: %s", exc)
        return {}


def _carry_over_unreachable(catalog: dict[str, Any]) -> None:
    """Сохранить прежние модели поставщика, которого не смогли спросить (MIR-170).

    Только при ошибке запроса: честный пустой ответ записывается как есть.
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
        # Дата первого переноса, иначе запись молодела бы с каждым обновлением.
        entry.setdefault("carried_from", previous.get("updated_at"))
        catalog["providers"][provider] = entry
        logger.warning(
            "model_catalog carried over provider=%s models=%d reason=%s",
            provider, len(entry.get("models") or ()), reason,
        )


# ── autorefresh: обновление прежде подстройки ─────────────────────────────────

#: Одна попытка на процесс, чтобы сбойное обновление не молотило по сети.
_AUTOREFRESH_DONE = False


def _autorefresh_enabled() -> bool:
    raw = (os.getenv("AGENT_CATALOG_AUTOREFRESH") or "").strip().lower()
    return raw not in ("0", "false", "off", "no")


def _credentialed_providers() -> list[str]:
    """Провайдеры, для которых заданы ключи."""
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
    """Best model for *tier* + *provider*: env override, then catalog (refreshed once if dead), else ""."""
    env_var = _TIER_ENV.get(tier, "")
    if env_var:
        override = os.getenv(env_var, "").strip()
        if override:
            return override

    catalog = _load_catalog()
    if catalog is None:
        ensure_fresh_catalog()
        catalog = _load_catalog()
    if catalog:
        provider_data = catalog.get("providers", {}).get(provider, {})
        model = provider_data.get("tier_best", {}).get(tier.value, "")
        if model:
            return model

    return ""


def catalog_freshness() -> dict[str, Any]:
    """Cache status (missing / expired / fresh / unreadable) with age and TTL for the operator."""
    hint = "run :refresh-models to rebuild the catalog"
    path = _catalog_path()
    if not path.exists():
        return {"status": "missing", "expired": False, "age_days": None,
                "ttl_days": _ttl_days(), "path": str(path), "hint": hint}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        age_days = _catalog_age_days(data)
    # Must not raise: callers use this status to decide on the refresh that repairs it.
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
    except Exception:  # noqa: BLE001 — каталог ходит в сеть; None = прежнее поведение
        return None


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
