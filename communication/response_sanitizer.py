from __future__ import annotations

import re


def looks_internal_telemetry(text: str) -> bool:
    """Определяет, что в тексте протекли внутренние служебные маркеры."""
    t = (text or "").lower()
    if not t.strip():
        return True

    markers = (
        "success rate", "confidence", "score", "вектор", "vector",
        "метрик", "telemetry", "reasoning", "system", "debug",
        "суть сообщения", "идея найди", "рекомендация", "текст обрезан",
        "цикл", "оценка", "в двух словах", "релевантн",
    )
    hits = sum(1 for m in markers if m in t)
    return hits >= 2


def sanitize_user_response(text: str, user_text: str = "", style: str = "partner") -> str:
    """Очищает финальный ответ от логов/метрик и возвращает человеко-понятный текст.

    Args:
        style: 'professional' | 'balanced' | 'partner' — влияет на fallback-сообщения.
    """
    raw = str(text or "").replace("\r", "").strip()
    if not raw:
        _empty = {
            "professional": "Ответ не получен. Повторите запрос.",
            "balanced": "Не удалось получить ответ. Повторите запрос, пожалуйста.",
            "partner": "Извини, не расслышал ответ. Повтори запрос, и отвечу кратко и по делу.",
        }
        return _empty.get(style, _empty["partner"])

    kill_patterns = (
        r"^\s*суть сообщения",
        r"^\s*рекомендац",
        r"^\s*в двух словах",
        r"^\s*обнаруженная проблема",
        r"^\s*результат вектор",
        r"текст\s+обрез",
        r"success\s*rate",
        r"\bconfidence\b",
        r"\bscore\b",
        r"\btelemetry\b",
        r"\bdebug\b",
        r"\bsystem\b",
    )
    line_re = [re.compile(p, flags=re.IGNORECASE) for p in kill_patterns]

    kept: list[str] = []
    for line in raw.split("\n"):
        s = line.strip()
        if not s:
            continue
        if any(rx.search(s) for rx in line_re):
            continue
        kept.append(s)

    cleaned = "\n".join(kept).strip()
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)

    if not cleaned or looks_internal_telemetry(cleaned):
        ask = (user_text or "").strip()
        if ask:
            ask = ask[:180]
            _with_ask = {
                "professional": f"Запрос принят: {ask}. Подготовлю план действий.",
                "balanced": f"Понял: {ask}. Сейчас подготовлю план.",
                "partner": (
                    f"Понял запрос: {ask}. "
                    "Давай по делу: могу сразу дать короткий рабочий план в 5 шагов "
                    "и начать с первого шага."
                ),
            }
            return _with_ask.get(style, _with_ask["partner"])
        _no_ask = {
            "professional": "Сформулируйте задачу — подготовлю план действий.",
            "balanced": "Сформулируйте задачу одной фразой, и я подготовлю план.",
            "partner": "Понял. Давай по делу: сформулируй задачу одной фразой, и я дам конкретный план действий.",
        }
        return _no_ask.get(style, _no_ask["partner"])

    return cleaned
