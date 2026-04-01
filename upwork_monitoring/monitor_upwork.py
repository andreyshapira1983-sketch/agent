"""Совместимый запуск мониторинга Upwork из корня проекта.

Если реальный скрипт лежит в outputs/monitor_upwork.py,
этот файл запускает его и не ломает старые команды.
"""

from __future__ import annotations

from pathlib import Path
import runpy
import sys


def main() -> int:
    root = Path(__file__).resolve().parent
    target = root / "outputs" / "monitor_upwork.py"

    if not target.exists():
        print(
            "Файл outputs/monitor_upwork.py не найден. "
            "Сначала сгенерируй его через план агента.",
            file=sys.stderr,
        )
        return 2

    runpy.run_path(str(target), run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
