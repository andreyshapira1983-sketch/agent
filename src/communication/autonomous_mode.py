"""
Режим автомата: фоновый цикл оркестратора, управление через /autonomous и /stop в Telegram.
Все уведомления о циклах уходят в Telegram (send_autonomous_event уже вызывается из run_cycle).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

_log = logging.getLogger(__name__)

_autonomous_stop_requested = False
_autonomous_task: asyncio.Task[None] | None = None
_autonomous_cycles_done = 0


def is_autonomous_running() -> bool:
    return _autonomous_task is not None and not _autonomous_task.done()


def request_stop() -> None:
    global _autonomous_stop_requested
    _autonomous_stop_requested = True


def is_stop_requested() -> bool:
    """Проверить, запрошена ли остановка (для выхода из цикла или из act в середине цикла)."""
    return _autonomous_stop_requested


def get_cycles_done() -> int:
    return _autonomous_cycles_done


async def run_autonomous_loop() -> None:
    """
    Фоновый цикл: пока не попросили стоп и квоты не исчерпаны — запускаем run_cycle().
    Результаты каждого цикла уже уходят в Telegram из orchestrator.run_cycle (send_autonomous_event).
    """
    global _autonomous_stop_requested, _autonomous_task, _autonomous_cycles_done
    _autonomous_stop_requested = False
    _autonomous_cycles_done = 0
    from src.tools.orchestrator import Orchestrator
    orch = Orchestrator()
    while True:
        if _autonomous_stop_requested:
            _log.info("Autonomous loop stopped by user request.")
            break
        try:
            policy = orch._get_policy()
            if not policy.can_start_cycle():
                _log.info("Autonomous loop stopped: quota exceeded.")
                try:
                    from src.communication.telegram_alerts import send_alert
                    send_alert("Автономный режим остановлен: исчерпаны квоты циклов/действий.")
                except Exception:
                    pass
                break
            summary: dict[str, Any] = await asyncio.to_thread(orch.run_cycle)
            _autonomous_cycles_done += 1
            status = summary.get("status", "ok")
            if status == "quota_exceeded":
                break
            # Проактивность: время от времени агент сам пишет пользователю (тема, прочитанное, вопрос)
            try:
                from src.communication.proactive_planner import try_send_proactive
                await asyncio.to_thread(try_send_proactive)
            except Exception:
                pass
            await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            _log.info("Autonomous loop task cancelled.")
            break
        except Exception as e:
            _log.exception("Autonomous cycle error: %s", e)
            try:
                from src.communication.telegram_alerts import send_alert
                send_alert(f"Ошибка в автономном цикле: {e!s}"[:300])
            except Exception:
                pass
            await asyncio.sleep(2)
    _autonomous_task = None


def start_autonomous_loop() -> bool:
    """Запустить фоновый цикл. Возвращает False, если уже запущен."""
    global _autonomous_task
    if is_autonomous_running():
        return False
    _autonomous_task = asyncio.create_task(run_autonomous_loop())
    return True


def stop_autonomous_loop() -> None:
    """Попросить остановить цикл (после текущего run_cycle)."""
    request_stop()
