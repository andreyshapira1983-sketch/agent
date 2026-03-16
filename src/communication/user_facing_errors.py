"""
Понятные пользователю сообщения об ошибках вместо технических traceback.
Используется в Telegram-ответах.
"""
from __future__ import annotations

import re


def user_facing_error(exc: BaseException, max_length: int = 400) -> str:
    """
    Превратить исключение в короткое сообщение на русском для пользователя.
    Не показываем внутренние детали (пути, строки кода).
    """
    msg = str(exc).strip() if exc else "Неизвестная ошибка"
    if not msg:
        msg = type(exc).__name__ or "Ошибка"

    # Известные типы — короткие формулировки
    if "timeout" in msg.lower() or "timed out" in msg.lower():
        return "Запрос занял слишком много времени. Попробуйте короче или позже."
    if "rate limit" in msg.lower() or "429" in msg:
        return "Слишком много запросов к сервису. Подождите минуту и попробуйте снова."
    if "api key" in msg.lower() or "authentication" in msg.lower() or "401" in msg:
        return "Ошибка доступа к сервису (проверьте ключи в .env)."
    if "connection" in msg.lower() or "connect" in msg.lower() or "network" in msg.lower():
        return "Нет связи с сервисом. Проверьте интернет и попробуйте снова."
    if "file" in msg.lower() and ("not found" in msg.lower() or "no such file" in msg.lower()):
        return "Файл не найден или недоступен."
    if "permission" in msg.lower() or "forbidden" in msg.lower() or "403" in msg:
        return "Нет прав на это действие."
    if "memory" in msg.lower() or "out of memory" in msg.lower():
        return "Не хватает памяти. Попробуйте более короткий запрос."

    # Убираем технические детали: пути (C:\, /home, ...), номера строк
    cleaned = re.sub(r"[A-Za-z]:\\[^\s]+", "", msg)
    cleaned = re.sub(r"/[\w/.-]+", "", cleaned)
    cleaned = re.sub(r"line \d+", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\d+\.\d+\.\d+\.\d+", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if len(cleaned) > 80:
        cleaned = cleaned[:77] + "..."

    if cleaned:
        prefix = "Не удалось выполнить запрос"
        out = f"{prefix}: {cleaned}"
    else:
        out = "Не удалось выполнить запрос. Попробуйте ещё раз или сформулируйте иначе."

    return out[:max_length]
