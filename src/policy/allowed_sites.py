"""
Белый список сайтов для агента: fetch_url, задачи с указанием сайта (портфолио и т.д.).
Если задача ссылается на URL не из списка — задача пропускается и в Telegram уходит уведомление.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

_log = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "allowed_sites.json"
_cached_prefixes: list[str] | None = None


def _load_prefixes() -> list[str]:
    global _cached_prefixes
    if _cached_prefixes is not None:
        return _cached_prefixes
    if not _CONFIG_PATH.exists():
        _cached_prefixes = []
        return _cached_prefixes
    try:
        data = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
        raw = data.get("allowed_url_prefixes") or data.get("allowed_sites") or []
        _cached_prefixes = [s.strip().rstrip("/") for s in raw if isinstance(s, str) and s.strip()]
        return _cached_prefixes
    except Exception as e:
        _log.warning("Failed to load allowed_sites.json: %s", e)
        _cached_prefixes = []
        return _cached_prefixes


def get_allowed_sites() -> list[str]:
    """Список разрешённых URL-префиксов (без завершающего слэша)."""
    return list(_load_prefixes())


def _normalize_url_for_match(url: str) -> str:
    """Убрать фрагмент и query, привести к https для сравнения с префиксами."""
    u = (url or "").strip()
    u = u.split("#")[0].split("?")[0]
    if u.startswith("http://"):
        u = "https://" + u[7:]
    elif not u.startswith("https://"):
        u = "https://" + u
    return u.rstrip("/") or u


def is_site_allowed(url: str) -> bool:
    """
    Проверить, разрешён ли сайт для запросов/работы.
    url — полный URL или только хост; сравнивается с префиксами из allowed_sites.json.
    """
    if not url or not str(url).strip():
        return False
    normalized = _normalize_url_for_match(str(url))
    prefixes = _load_prefixes()
    if not prefixes:
        return False
    for prefix in prefixes:
        p = _normalize_url_for_match(prefix)
        if normalized == p or normalized.startswith(p + "/") or normalized.startswith(p):
            return True
    return False


def notify_site_blocked(url: str, task_id: str = "", tool: str = "") -> None:
    """Отправить в Telegram уведомление о том, что задача пропущена из‑за сайта не из белого списка."""
    try:
        from src.communication.telegram_alerts import send_alert
        msg = f"⚠️ Задача пропущена: сайт не в списке разрешённых.\nURL: {url[:200]}"
        if task_id:
            msg += f"\nTask: {task_id}"
        if tool:
            msg += f"\nИнструмент: {tool}"
        send_alert(msg)
    except Exception as e:
        _log.warning("Could not send site-blocked Telegram alert: %s", e)


def clear_cache() -> None:
    """Сбросить кэш загруженных префиксов (для тестов или после изменения файла)."""
    global _cached_prefixes
    _cached_prefixes = None
