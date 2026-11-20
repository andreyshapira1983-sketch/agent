"""Record of an observation sensor failure: a single method."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from core.sensor_journal import journal_sensor_failure


class AgentLoopSensor:
    """Mixed into ``AgentLoop``; state lives on the composed loop."""

    if TYPE_CHECKING:  # pragma: no cover — declarations only
        log: Any

        def _durable_learning_suppressed(self, sink: str) -> bool: ...

    def _sensor_failed(self, sensor: str, exc: BaseException) -> None:
        """Thin delegation to `core/sensor_journal`."""
        journal_sensor_failure(self.log, sensor, exc)
