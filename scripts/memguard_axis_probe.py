"""Какую власть сохраняет проверяющий сигнал ПОСЛЕ допуска в память.

Ось MemGuard (2026-08-26). Вопрос не «допускать ли неподтверждённое», а
«различает ли выдача подтверждённое и неподтверждённое». Замер печатает три
числа: сколько записей допущено меткой в обход сигнала, сколько подтверждённых
записей НЕ имеют защитной метки, и кто из них кого обгоняет в живой выдаче.

Запуск: PYTHONIOENCODING=utf-8 python scripts/memguard_axis_probe.py
"""
from __future__ import annotations

from pathlib import Path

from core.smart_memory import EpisodicMemoryStore

STORE = Path("data/episodic_memory.jsonl")


def main() -> None:
    memory = EpisodicMemoryStore(STORE)
    episodes = memory.load()
    protected = EpisodicMemoryStore.PROTECTED_TAGS

    admitted = [e for e in episodes if e.usage_eligible]
    verified = [e for e in admitted if (e.verified_chunks or 0) > 0]
    unverified = [e for e in admitted if not (e.verified_chunks or 0)]
    boosted_unverified = [e for e in unverified if protected & set(e.tags)]
    plain_verified = [e for e in verified if not (protected & set(e.tags))]

    print(f"эпизодов в хранилище: {len(episodes)}")
    print(f"допущено к выдаче:    {len(admitted)}")
    print(f"  из них подтверждено (verified_chunks>0): {len(verified)}")
    print(f"  из них НЕ подтверждено:                  {len(unverified)}")
    print(f"    и при этом несут защитную метку (+50): {len(boosted_unverified)}")
    print(f"подтверждённых БЕЗ защитной метки (буст 0): {len(plain_verified)}")

    # Очная ставка: берём подтверждённую запись без метки и спрашиваем память
    # её же собственными словами. Если сверху окажется неподтверждённая —
    # сигнал проиграл метке не гипотетически, а на живом хранилище.
    losses = 0
    checked = 0
    for ep in plain_verified:
        query = f"{ep.goal} {ep.question}".strip()
        if not query:
            continue
        top = memory.search(query, limit=1)
        if not top:
            continue
        checked += 1
        winner = top[0]
        if winner.created_at != ep.created_at and not (winner.verified_chunks or 0):
            losses += 1

    print(f"\nочная ставка: спрошено {checked} подтверждённых записей их же словами")
    print(f"  сверху оказалась НЕподтверждённая: {losses}")


if __name__ == "__main__":
    main()
