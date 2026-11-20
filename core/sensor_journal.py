"""One channel for failures of observational sensors."""
from __future__ import annotations

from typing import Any

#: Event name in the journal; one for all sensors so the operator searches one word.
SENSOR_FAILED_EVENT = "sensor_failed"

#: How many characters of the error text go into the journal.
_MAX_ERROR_CHARS = 300


def sensor_failure_payload(sensor: str, exc: BaseException) -> dict[str, Any]:
    """Event payload: WHO broke, with WHAT error and text.

        Pure function."""
    return {
        "sensor": sensor,
        "error_type": type(exc).__name__,
        "error": str(exc)[:_MAX_ERROR_CHARS],
    }


def journal_sensor_failure(log: Any, sensor: str, exc: BaseException) -> bool:
    """Record a sensor failure. Returns whether it was written."""
    try:
        log.log(SENSOR_FAILED_EVENT, sensor_failure_payload(sensor, exc))
    except Exception:  # noqa: BLE001 — последний рубеж вокруг самого журналирования
        return False
    else:
        return True
