"""Контроль к зонду MemGuard: почему подтверждённая запись проигрывает.

Печатает разбор одной очной ставки — очки, метки, сигнал — и повторяет тот же
подбор БЕЗ прибавки за метку. Если без прибавки побеждает сама запись, значит
проигрыш даёт именно метка, а не слабое совпадение слов.

Запуск: PYTHONIOENCODING=utf-8 PYTHONPATH=. python scripts/memguard_axis_control.py
"""
from __future__ import annotations

from pathlib import Path

from core.smart_memory import STOPWORDS, EpisodicMemoryStore, _tokens

STORE = Path("data/episodic_memory.jsonl")


def _score(ep, q_tokens) -> int:
    haystack = " ".join([ep.goal, ep.question, ep.summary, " ".join(ep.tags)])
    return len(q_tokens & _tokens(haystack))


def main() -> None:
    memory = EpisodicMemoryStore(STORE)
    episodes = memory.load()
    protected = EpisodicMemoryStore.PROTECTED_TAGS
    admitted = [e for e in episodes if e.usage_eligible]
    plain_verified = [
        e for e in admitted
        if (e.verified_chunks or 0) > 0 and not (protected & set(e.tags))
    ]
    subject = plain_verified[0]
    query = f"{subject.goal} {subject.question}".strip()
    q_tokens = _tokens(query)
    q_content = q_tokens - STOPWORDS

    print(f"спрашиваем словами записи: {query[:90]}")
    print(f"её сигнал: verified={subject.verified_chunks} метки={subject.tags}\n")

    ranked = []
    for ep in episodes:
        raw = _score(ep, q_tokens)
        hay = _tokens(" ".join([ep.goal, ep.question, ep.summary, " ".join(ep.tags)]))
        if not (raw and (q_content & hay)):
            continue
        boost = 50 if protected & set(ep.tags) else 0
        ranked.append((raw + boost, raw, boost, ep))

    ranked.sort(key=lambda r: (r[0], r[3].created_at), reverse=True)
    print("прежняя формула (прибавка +50 за метку):")
    for total, raw, boost, ep in ranked[:3]:
        mark = "  <-- сама запись" if ep.created_at == subject.created_at else ""
        print(f"  итог={total:3d} (слов={raw}, метка=+{boost}) "
              f"verified={ep.verified_chunks} {ep.goal[:44]}{mark}")

    ranked.sort(key=lambda r: (r[1], r[3].created_at), reverse=True)
    print("\nконтроль — тот же подбор БЕЗ прибавки за метку:")
    for _total, raw, _boost, ep in ranked[:3]:
        mark = "  <-- сама запись" if ep.created_at == subject.created_at else ""
        print(f"  слов={raw:3d} verified={ep.verified_chunks} {ep.goal[:44]}{mark}")


if __name__ == "__main__":
    main()
