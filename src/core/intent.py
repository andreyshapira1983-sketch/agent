"""
Интерпретация намерений: естественная фраза пользователя → структурированная команда.
Natural language interface: человек говорит по-человечески, система переводит в действие.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

from openai import OpenAI

_INTENT_SYSTEM = """Ты классификатор намерений. На вход — сообщение пользователя (русский или английский).
Ответь ТОЛЬКО одним JSON-объектом, без markdown и без текста до/после.
Формат: {"command": "<команда>", "params": {}}

Команды:
- read_book: пользователь просит почитать, поучиться, почитать книгу, обучайся, иди читать, что-то про книги/обучение.
- get_status: как дела, что с агентом, статус, что происходит.
- get_metrics: метрики, производительность, ошибки, здоровье системы.
- run_cycle: запусти цикл, сделай цикл, поработай автономно (без явного /autonomous).
- chat: обычный разговор, вопрос, благодарность, непонятное или не подходит ни одна команда выше.

params оставь пустым {}.
"""

_client: OpenAI | None = None


def _client_get() -> OpenAI:
    global _client
    if _client is None:
        key = os.getenv("OPENAI_API_KEY", "")
        if not key:
            raise RuntimeError("OPENAI_API_KEY not set")
        _client = OpenAI(api_key=key)
    return _client


def interpret_intent(user_message: str) -> dict[str, Any]:
    """
    Извлечь намерение из естественной фразы пользователя.
    Возвращает {"command": "read_book"|"get_status"|"get_metrics"|"run_cycle"|"chat", "params": {}}.
    При ошибке или пустом сообщении возвращает {"command": "chat", "params": {}}.
    """
    if not (user_message or "").strip():
        return {"command": "chat", "params": {}}
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
        if cmd not in ("read_book", "get_status", "get_metrics", "run_cycle", "chat"):
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
    else:
        return user_message
    return prefix + (user_message or "").strip()
