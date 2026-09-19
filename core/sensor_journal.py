"""Один канал для сбоев наблюдательных сенсоров (MIR-077)."""
from __future__ import annotations

from typing import Any

#: Имя события в журнале. Одно на все сенсоры — оператор ищет одно слово.
SENSOR_FAILED_EVENT = "sensor_failed"

#: Сколько символов текста ошибки попадает в журнал.
_MAX_ERROR_CHARS = 300


def sensor_failure_payload(sensor: str, exc: BaseException) -> dict[str, Any]:
    """Полезная нагрузка события: КТО сломался, КАКОЙ ошибкой и с каким текстом.

    Чистая функция — её легко проверить и переиспользовать где угодно.
    """
    return {
        "sensor": sensor,
        "error_type": type(exc).__name__,
        "error": str(exc)[:_MAX_ERROR_CHARS],
    }


def journal_sensor_failure(log: Any, sensor: str, exc: BaseException) -> bool:
    """Записать сбой сенсора. Возвращает, удалось ли записать."""
    try:
        log.log(SENSOR_FAILED_EVENT, sensor_failure_payload(sensor, exc))
    except Exception:  # noqa: BLE001 — последний рубеж вокруг самого журналирования
        return False
    else:
        return True
