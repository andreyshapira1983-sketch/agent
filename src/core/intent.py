"""
Интерпретация намерений: естественная фраза пользователя → структурированная команда.
Natural language interface: человек говорит по-человечески, система переводит в действие.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

try:
    from openai import OpenAI
except Exception:  # pragma: no cover - optional dependency for offline tests/runtime
    OpenAI = None  # type: ignore[assignment]

_INTENT_SYSTEM = """Ты классификатор намерений. На вход — сообщение пользователя (русский или английский).
Ответь ТОЛЬКО одним JSON-объектом, без markdown и без текста до/после.
Формат: {"command": "<команда>", "params": {}}

Команды:
- read_book: пользователь просит почитать, поучиться, почитать книгу, обучайся, иди читать, что-то про книги/обучение.
- get_status: как дела, что с агентом, статус, что происходит.
- get_metrics: метрики, производительность, ошибки, здоровье системы.
- get_quality: качество, quality-метрики, что с качеством, история патчей/ремонтов.
- export_quality_text: выгрузи/экспортируй quality отчёт в текст.
- export_quality_json: выгрузи/экспортируй quality отчёт в json.
- export_quality_full: выгрузи полный quality отчёт (расширенная история).
- reset_quality: сбрось/обнули quality-метрики.
- run_cycle: запусти цикл, сделай цикл, поработай автономно (без явного /autonomous).
- chat: обычный разговор, вопрос, благодарность, непонятное или не подходит ни одна команда выше.

params оставь пустым {}.
"""

_client: OpenAI | None = None


def _client_get() -> OpenAI:
    global _client
    if _client is None:
        if OpenAI is None:
            raise RuntimeError("openai package not available")
        key = os.getenv("OPENAI_API_KEY", "")
        if not key:
            raise RuntimeError("OPENAI_API_KEY not set")
        _client = OpenAI(api_key=key)
    return _client


def _keyword_intent(user_message: str) -> dict[str, Any] | None:
    """
    Локальный fallback без LLM: базовые русские фразы → намерение.
    Нужен, чтобы пользователь мог работать без slash-команд.
    """
    txt = (user_message or "").strip().lower()
    if not txt:
        return None

    has_quality = ("quality" in txt) or ("качеств" in txt)
    has_export = ("выгруз" in txt) or ("экспорт" in txt) or ("отч" in txt)

    if has_quality and has_export and ("full" in txt or "полный" in txt or "расшир" in txt):
        return {"command": "export_quality_full", "params": {}}
    if has_quality and has_export and "json" in txt:
        return {"command": "export_quality_json", "params": {}}
    if has_quality and has_export:
        return {"command": "export_quality_text", "params": {}}

    if ("сброс" in txt or "обнул" in txt) and (has_quality or "метрик" in txt):
        return {"command": "reset_quality", "params": {}}

    if has_quality and ("покаж" in txt or "что" in txt or "как" in txt or "дай" in txt):
        return {"command": "get_quality", "params": {}}

    if "метрик" in txt or "производитель" in txt:
        return {"command": "get_metrics", "params": {}}

    if "статус" in txt or "что с агент" in txt or "как дела" in txt:
        return {"command": "get_status", "params": {}}

    if "запусти цикл" in txt or "сделай цикл" in txt or "поработай автоном" in txt:
        return {"command": "run_cycle", "params": {}}

    return None


def interpret_intent(user_message: str) -> dict[str, Any]:
    """
    Извлечь намерение из естественной фразы пользователя.
    Возвращает {"command": ..., "params": {}}.
    При ошибке или пустом сообщении возвращает {"command": "chat", "params": {}}.
    """
    if not (user_message or "").strip():
        return {"command": "chat", "params": {}}
    fallback = _keyword_intent(user_message)
    if fallback:
        return fallback
    try:
        client = _client_get()
        r = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": _INTENT_SYSTEM},
                {"role": "user", "content": (user_message or "").strip()[:2000]},
            ],
            max_tokens=100,
        )
        if not r.choices or not r.choices[0].message.content:
            return {"command": "chat", "params": {}}
        raw = r.choices[0].message.content.strip()
        # Убрать обёртку markdown code block если есть
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```\s*$", "", raw)
        data = json.loads(raw)
        cmd = (data.get("command") or "chat").strip().lower()
        if cmd not in (
            "read_book",
            "get_status",
            "get_metrics",
            "get_quality",
            "export_quality_text",
            "export_quality_json",
            "export_quality_full",
            "reset_quality",
            "run_cycle",
            "chat",
        ):
            cmd = "chat"
        return {"command": cmd, "params": data.get("params") or {}}
    except Exception:
        return {"command": "chat", "params": {}}


def wrap_message_with_intent(user_message: str, intent: dict[str, Any]) -> str:
    """
    Обернуть сообщение пользователя в текст с явным намерением для агента.
    Агент видит [Intent: X] и выполняет действие, не уточняя.
    """
    cmd = intent.get("command") or "chat"
    if cmd == "chat":
        return user_message
    if cmd == "read_book":
        prefix = "[Intent: read_book] Выполни цикл чтения/обучения: get_gutenberg_book_list или search_openlibrary → fetch_url по каждой ссылке → log_reading. Не спрашивай — делай и отчитайся. Сообщение пользователя: "
    elif cmd == "get_status":
        prefix = "[Intent: get_status] Дай краткий статус агента (очередь, последние действия, здоровье). Сообщение пользователя: "
    elif cmd == "get_metrics":
        prefix = "[Intent: get_metrics] Покажи метрики/производительность/ошибки. Сообщение пользователя: "
    elif cmd == "run_cycle":
        prefix = "[Intent: run_cycle] Пользователь просит запустить автономный цикл. Сообщение пользователя: "
    elif cmd == "get_quality":
        prefix = "[Intent: get_quality] Покажи quality-метрики и историю. Сообщение пользователя: "
    elif cmd == "get_metrics":
        prefix = "[Intent: get_metrics] Покажи метрики/производительность/ошибки. Сообщение пользователя: "
    elif cmd == "get_status":
        prefix = "[Intent: get_status] Дай краткий статус агента. Сообщение пользователя: "
    else:
        return user_message
    return prefix + (user_message or "").strip()
