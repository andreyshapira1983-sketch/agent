"""Запись о сбое наблюдательного сенсора — один метод, и это его дом."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from core.sensor_journal import journal_sensor_failure


class AgentLoopSensor:
    """Подмешивается в ``AgentLoop``; состояние живёт на композированном цикле."""

    if TYPE_CHECKING:  # pragma: no cover — только объявления
        log: Any

        def _durable_learning_suppressed(self, sink: str) -> bool: ...

    def _sensor_failed(self, sensor: str, exc: BaseException) -> None:
        """Тонкая делегация в `core/sensor_journal` (MIR-077).

        Логика живёт в отдельном компактном модуле — правило оператора: не
        раздувать большие файлы, а подключать маленькие.
        """
        journal_sensor_failure(self.log, sensor, exc)
