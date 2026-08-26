"""Различающая сила подбора опыта: что впускается и что доходит до планировщика.

MIR-105 оставлен открытым с явной причиной: выбор между «полом» для невиданных
слов и «порогом» на счёт требует замера КАЧЕСТВА подбора, а не вкуса. Этот
скрипт даёт замер.

Разметка не на вкус: процедура относится к тому вопросу, из которого её
отчеканили: сначала независимый след `source_questions` (MIR-165), который
переживает гигиену, затем — пока он не накопился — живой эпизод по
`source_episode_ids`. Отрицательный контроль — пары, где вопрос и
вопрос-происхождение процедуры не делят НИ ОДНОГО значащего слова: настолько
«не про то», насколько это вообще удостоверяется данными.

Запуск: PYTHONIOENCODING=utf-8 PYTHONPATH=. python scripts/measure_experience_retrieval_discrimination.py
"""
from __future__ import annotations

import math
from pathlib import Path

from core.smart_memory import (
    STOPWORDS,
    EpisodicMemoryStore,
    ProceduralMemoryStore,
    _tokens,
)
from core.topic_tokens import TokenSalience, build_salience, topic_tokens

EPISODES = Path("data/episodic_memory.jsonl")
PROCEDURES = Path("data/procedural_memory.jsonl")
RETIRED = {"obsolete", "needs_review"}


def _content(text: str) -> set[str]:
    return _tokens(text) - STOPWORDS


def _admitted(proc, q_tokens: set[str], q_content: set[str],
              salience: TokenSalience, threshold: float) -> tuple[bool, float]:
    """Повторяет допуск `ProceduralMemoryStore.search_with_report` один в один."""
    if proc.status in RETIRED:
        return False, 0.0
    haystack = " ".join([proc.name, " ".join(proc.trigger_tags), " ".join(proc.steps)])
    hay = _tokens(haystack)
    score = salience.overlap(q_tokens, hay)
    if not (score and (q_content & hay)):
        return False, score
    return score >= threshold, score


def _self_score(q_tokens: set[str], salience: TokenSalience) -> float:
    return salience.overlap(q_tokens, q_tokens) or 1.0


def main() -> None:
    episodes = EpisodicMemoryStore(EPISODES).load()
    procedures = ProceduralMemoryStore(PROCEDURES).load()
    by_id = {e.id: e for e in episodes}
    salience = build_salience(e.question for e in episodes if e.question)

    print(f"корпус: {sum(1 for e in episodes if e.question)} вопросов, "
          f"вес невиданного слова {salience.unseen:.2f}")
    counts: dict[str, int] = {}
    for e in episodes:
        for t in topic_tokens(e.question or ""):
            counts[t] = counts.get(t, 0) + 1
    hapax = sum(1 for c in counts.values() if c == 1)
    once = math.log((sum(1 for e in episodes if e.question) + 1) / 1)
    print(f"  слов в корпусе {len(counts)}, из них встречены РОВНО ОДИН раз "
          f"{hapax} ({hapax * 100 // max(len(counts), 1)} %)")
    print(f"  вес слова, встреченного однажды: {once:.2f} — "
          f"{'НЕОТЛИЧИМ от невиданного' if abs(once - salience.unseen) < 1e-9 else 'отличается'}\n")

    # Положительные пары: вопрос и процедура, отчеканенная ИЗ него. Сначала
    # независимый след (`source_questions`, MIR-165) — он переживает гигиену;
    # затем прежний способ через живой эпизод, пока след не накопился.
    positives = [
        (question, {p.id})
        for p in procedures
        for question in (p.source_questions or [
            by_id[eid].question for eid in p.source_episode_ids
            if eid in by_id and by_id[eid].question
        ])
        if question
    ]
    # Отрицательные: тот же вопрос против процедур, чей вопрос-происхождение
    # не делит с ним ни одного значащего слова.
    negatives = []
    for question, _ in positives:
        qc = _content(question)
        unrelated = set()
        for p in procedures:
            origins = list(p.source_questions) or [
                by_id[e].question for e in p.source_episode_ids if e in by_id
            ]
            origins = [o for o in origins if o]
            if origins and not any(qc & _content(o) for o in origins):
                unrelated.add(p.id)
        if unrelated:
            negatives.append((question, unrelated))

    print(f"размечено: {len(positives)} положительных пар, "
          f"{sum(len(u) for _, u in negatives)} отрицательных\n")

    print(f"{'порог':>8}  {'TPR':>6}  {'FPR':>6}  {'J':>6}  что доходит")
    for frac in (0.0, 0.05, 0.10, 0.20, 0.30, 0.40):
        tp = sum(
            1 for q, wanted in positives
            for p in procedures
            if _admitted(p, _tokens(q), _content(q), salience,
                         frac * _self_score(_tokens(q), salience))[0]
            and p.id in wanted
        )
        fp = fn_total = 0
        for q, unrelated in negatives:
            thr = frac * _self_score(_tokens(q), salience)
            for p in procedures:
                if p.id not in unrelated:
                    continue
                fn_total += 1
                if _admitted(p, _tokens(q), _content(q), salience, thr)[0]:
                    fp += 1
        tpr = tp / max(len(positives), 1)
        fpr = fp / max(fn_total, 1)
        print(f"{frac:>8.2f}  {tpr:>6.2f}  {fpr:>6.2f}  {tpr - fpr:>+6.2f}  "
              f"{tp}/{len(positives)} своих, {fp}/{fn_total} посторонних")


if __name__ == "__main__":
    main()
