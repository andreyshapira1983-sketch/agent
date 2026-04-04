# Communication Layer — Channel Bridge — Слой 15
# Мост между каналами (Telegram ↔ Web): кросс-канальные уведомления.
#
# Любой канал может отправить событие, и все остальные каналы его увидят.
# События: task_received, task_progress, task_done, user_message, agent_reply.

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Callable


class ChannelEvent:
    """Одно кросс-канальное событие."""
    __slots__ = ('type', 'source', 'text', 'ts', 'extra')

    def __init__(self, event_type: str, source: str, text: str,
                 extra: dict | None = None):
        self.type = event_type      # task_received | task_progress | task_done | message | reply
        self.source = source        # 'telegram' | 'web' | 'loop' | 'system'
        self.text = text
        self.ts = time.time()
        self.extra = extra or {}

    def to_dict(self) -> dict:
        return {
            'type': self.type,
            'source': self.source,
            'text': self.text,
            'ts': self.ts,
            **self.extra,
        }


class ChannelBridge:
    """
    Центральный мост между каналами связи.

    Каждый канал регистрирует свой listener. При emit() событие
    рассылается всем listener-ам кроме источника.
    """

    def __init__(self, monitoring=None):
        self._listeners: dict[str, Callable[[ChannelEvent], None]] = {}
        self._lock = threading.Lock()
        self._history: deque[ChannelEvent] = deque(maxlen=200)
        self.monitoring = monitoring

    def register(self, channel_name: str,
                 listener: Callable[[ChannelEvent], None]):
        """Регистрирует канал и его callback для получения событий."""
        with self._lock:
            self._listeners[channel_name] = listener

    def unregister(self, channel_name: str):
        with self._lock:
            self._listeners.pop(channel_name, None)

    def emit(self, event: ChannelEvent):
        """Рассылает событие всем каналам кроме источника."""
        self._history.append(event)
        with self._lock:
            targets = {k: v for k, v in self._listeners.items()
                       if k != event.source}
        for name, listener in targets.items():
            try:
                listener(event)
            except Exception as e:
                if self.monitoring:
                    self.monitoring.warning(
                        f"[bridge] Ошибка доставки в {name}: {e}",
                        source="channel_bridge",
                    )

    def recent(self, since_ts: float = 0, limit: int = 50) -> list[dict]:
        """Возвращает последние события после since_ts."""
        result = []
        for ev in self._history:
            if ev.ts > since_ts:
                result.append(ev.to_dict())
        return result[-limit:]

    # ── Удобные хелперы ───────────────────────────────────────────────────

    def task_received(self, source: str, task: str):
        self.emit(ChannelEvent('task_received', source, task))

    def task_progress(self, source: str, text: str):
        self.emit(ChannelEvent('task_progress', source, text))

    def task_done(self, source: str, text: str):
        self.emit(ChannelEvent('task_done', source, text))

    def user_message(self, source: str, text: str):
        self.emit(ChannelEvent('message', source, text))

    def agent_reply(self, source: str, text: str):
        self.emit(ChannelEvent('reply', source, text))
