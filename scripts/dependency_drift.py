"""Сверка установленного окружения с замком зависимостей.

ИСТОРИЧЕСКИЙ КЛАСС (H-16, docs/audit/HISTORICAL_FAILURE_LEDGER.md).
xz-utils, CVE-2024-3094, март 2024: закладка лежала в выпускаемых архивах и
отсутствовала в репозитории, а собирающая система её активировала. Ключевая
предпосылка не «зависимость злая», а «то, что ЗАПУСКАЕТСЯ, отличается от того,
что рассматривали».

ЗАМЕР 2026-08-24. Замок полон: 36 пакетов, 662 хеша sha256, ни одного пакета
без хеша. Но установленное окружение с ним РАСХОДИТСЯ: `anthropic` заперт на
0.102.0, установлен 0.121.0; `click` заперт на 8.4.1, установлен 8.4.2. Хеши
защищают установку, которая через них проходит, и молчат о той, что прошла
мимо — а сверять их с установленным не пробовал никто.

ЭТОТ СКРИПТ ТОЛЬКО СООБЩАЕТ. Он ничего не доустанавливает и не откатывает:
трогать зависимости — решение оператора, и «починить» расхождение установкой
означало бы менять рабочее окружение агента без его слова.

Запуск: `python scripts/dependency_drift.py`; код возврата 1 при расхождении.
"""
from __future__ import annotations

import pathlib
import re
import sys
from importlib import metadata

_LOCK = pathlib.Path(__file__).resolve().parents[1] / "requirements.lock"
_PIN = re.compile(r"(?m)^([A-Za-z0-9_.\-]+)==([^ ;\\\n]+)")


def main() -> int:
    if not _LOCK.exists():
        print(f"замок не найден: {_LOCK}")
        return 0

    text = _LOCK.read_text(encoding="utf-8")
    locked = {name: version.strip() for name, version in _PIN.findall(text)}
    hashes = text.count("--hash=sha256:")

    drift: list[tuple[str, str, str]] = []
    absent: list[str] = []
    for name, version in sorted(locked.items()):
        try:
            installed = metadata.version(name)
        except metadata.PackageNotFoundError:
            absent.append(name)
            continue
        if installed != version:
            drift.append((name, version, installed))

    print(f"замок: {len(locked)} пакетов, {hashes} хешей sha256")
    print(f"не установлено: {len(absent)}")
    print(f"расходится с замком: {len(drift)}")
    for name, version, installed in drift:
        print(f"  {name}: заперто {version}, установлено {installed}")
    if drift:
        print()
        print("окружение отличается от того, что описывает замок — то есть от")
        print("того, что получит свежая установка и что видит CI.")
    return 1 if drift else 0


if __name__ == "__main__":
    sys.exit(main())
