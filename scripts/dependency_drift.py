"""Compare installed packages with requirements.lock; report only, exit code 1 on drift."""
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
