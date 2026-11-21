"""Probe: does memory retrieval still rank verified episodes above tag-boosted unverified ones?"""
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

    # Query with an untagged verified episode's own words; an unverified winner on top means
    # the tag beat the verification signal on the live store.
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
