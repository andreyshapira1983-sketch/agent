"""`:causal` — водитель причинной лестницы.

Подъём (`core/causal_climb.py`) — решатель, и без водителя он «decider that
cannot run»: тот самый класс, который INV-2 ловит, а эта сессия разбирает.
Здесь водитель, и он операторский НАРОЧНО.

Гипотезы выдвигает автор — так сказано в `core/causal_lesson.py`, и автором
может быть оператор, агент или внешний инженер. Первым сделан оператор,
потому что он единственный, кто сегодня умеет выдвинуть конкурирующие
объяснения: живой замер 2026-08-15 показал, что агент не переводит наблюдение
в гипотезу о себе. Дать машине выдумывать гипотезы за него значило бы получить
ATTRIBUTED, означающее «модель уверена».

Показывает список наблюдений и то, чего лестнице не хватает для следующей
ступени. Ничего не поднимает сам: подъём — это ходы с измерениями, а команда
только называет, где стоишь.

Зачем: docs/CODE_NOTES.md, «The climb, and where it stops being wiring».
"""
from __future__ import annotations

import sys
from typing import Any

from core.causal_climb import attach_explanations, propose_explanation
from core.causal_lesson import CausalClaim, blocking_reason, state_of


def _handle_causal(rest: str, agent: Any) -> bool:
    """`:causal [гипотеза | гипотеза]` — где стоим и что даст выдвинутое.

    Без аргументов: список наблюдений и ближайшая нехватка у каждого.
    С гипотезами через `|`: они привязываются к САМОМУ ЧАСТОМУ наблюдению —
    повторяемость и есть основание считать дефект классом — и лестница
    отвечает, что теперь мешает. Ничего не сохраняется: это ход, а не запись,
    и подъём засчитывается только измерением (`run_intervention`).
    """
    store = getattr(agent, "causal_store", None)
    if store is None:
        print("причинное хранилище не подключено на этом пути", file=sys.stderr)
        return True

    records = store.load()
    if not records:
        print("наблюдений нет: детекторы молчали", file=sys.stderr)
        return True

    proposed = [h.strip() for h in rest.split("|") if h.strip()]
    if proposed:
        top = max(records, key=lambda r: r.occurrences)
        claim = attach_explanations(
            CausalClaim(observation=_as_observation(top)),
            [propose_explanation(h, author="operator") for h in proposed],
            chosen=proposed[0],
        )
        print(
            f"{top.fingerprint} x{top.occurrences}: выдвинуто {len(proposed)}\n"
            f"  состояние : {state_of(claim)}\n"
            f"  не хватает: {blocking_reason(claim)}",
            file=sys.stderr,
        )
        agent.log.log("causal_ladder_step", {
            "fingerprint": top.fingerprint,
            "explanations": len(proposed),
            "state": state_of(claim),
            "blocking_reason": blocking_reason(claim),
        })
        return True

    print(f"наблюдений: {len(records)}", file=sys.stderr)
    for rec in sorted(records, key=lambda r: -r.occurrences):
        # Состояние считается судьёй, а не хранилищем: запись — это ступень
        # OBSERVED, и всё, что выше, надо ещё заработать.
        claim = CausalClaim(observation=_as_observation(rec))
        print(
            f"  {rec.fingerprint}  x{rec.occurrences}  {state_of(claim)}\n"
            f"      сигналы   : {', '.join(rec.defect_signals)}\n"
            f"      не хватает: {blocking_reason(claim)}\n"
            f"      случаи    : {', '.join(rec.episode_ids[-3:]) or '—'}",
            file=sys.stderr,
        )
    agent.log.log("causal_ladder_status", {"observations": len(records)})
    return True


def _as_observation(record: Any):
    from core.causal_lesson import Observation

    return Observation(
        episode_id=record.episode_ids[-1] if record.episode_ids else "",
        trace_id="",
        run_id="",
        defect_signals=record.defect_signals,
        evidence_refs=record.evidence_refs,
        observed_mismatch=record.observed_mismatch,
    )
