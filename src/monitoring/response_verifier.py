"""
Strict anti-simulation verifier for assistant replies.

If a reply contains system-metrics numeric claims (CPU/RAM/PID/process load),
it must be backed by a confirmed system snapshot source.
"""
from __future__ import annotations

import re

from src.monitoring.system_metrics import get_system_metrics_snapshot


_SYSTEM_KEYWORDS_RE = re.compile(r"(?iu)(cpu|ram|памят|процесс|pid|нагрузк)")
_SYSTEM_CONTEXT_RE = re.compile(
    r"(?iu)(систем|процесс|pid|нагрузк|памят|сервер|машин|host|os|операционн|core|ядр|utilization|usage|memory)"
)
_NUMBER_RE = re.compile(r"\d+[\.,]?\d*")
_PERCENT_RE = re.compile(r"\d+[\.,]?\d*\s*%")


def _has_percent_near_system_context(text: str, window_chars: int = 48) -> bool:
    if not text:
        return False
    lowered = text.lower()
    for m in _PERCENT_RE.finditer(lowered):
        start, end = m.span()
        left = max(0, start - window_chars)
        right = min(len(lowered), end + window_chars)
        fragment = lowered[left:right]
        if _SYSTEM_CONTEXT_RE.search(fragment):
            return True
    return False


def _looks_like_system_numeric_claim(text: str) -> bool:
    if not text:
        return False
    # Strict rule: percentages near system context are treated as system claims,
    # even if CPU/RAM tokens are not explicitly present.
    if _has_percent_near_system_context(text):
        return True
    if not _SYSTEM_KEYWORDS_RE.search(text):
        return False
    return bool(_NUMBER_RE.search(text))


def enforce_verified_system_metrics(reply: str) -> str:
    """
    Block unverified system numeric claims.

    Policy:
    - If reply does not look like system metrics claim -> pass through.
    - If it does, only allow confirmed values from real snapshot source.
    """
    if not _looks_like_system_numeric_claim(reply):
        return reply

    snapshot = get_system_metrics_snapshot(force_refresh=False, ttl_sec=30, top_n=3)
    source = snapshot.get("source", "unknown")
    ts = snapshot.get("timestamp_utc", "unknown")

    if not snapshot.get("ok"):
        return (
            reply + "\n\n[Анти-симуляционный контур: в ответе были числа про систему, но подтверждённых метрик нет.]"
        )

    cpu = snapshot.get("cpu_percent")
    ram = snapshot.get("ram_percent")
    top = snapshot.get("top_processes") or []
    top_lines = []
    for p in top[:3]:
        top_lines.append(
            f"- pid={p.get('pid')} name={p.get('name')} cpu={p.get('cpu_percent')}% rss_mb={p.get('rss_mb')} "
            f"[source={source} timestamp={ts}]"
        )
    top_text = "\n".join(top_lines) if top_lines else "- нет данных"

    # Не заменять ответ целиком — дополняем подтверждёнными метриками, чтобы пользователь видел и ответ агента, и факты
    appendix = (
        f"\n\n[Подтверждённые метрики: CPU={cpu}% RAM={ram}% (source={source}). Топ процессов:\n{top_text}]"
    )
    return reply + appendix
