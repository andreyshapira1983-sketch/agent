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

import pathlib
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
    if rest.split(maxsplit=1)[:1] == ["provenance"]:
        return _handle_provenance(rest.split(maxsplit=1)[1:] or [""], agent)

    if rest.split(maxsplit=1)[:1] == ["ab"]:
        return _handle_ab_experiment(rest.split()[1:], agent)

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


def _handle_provenance(args: list[str], agent: Any) -> bool:
    """`:causal provenance [ключ]` — цепь квитанций урока; замер, не рассказ."""
    from core.causal_claim_store import load_claims
    from core.lesson_provenance import trace_lesson_provenance

    workspace = getattr(agent, "workspace", None) or "."
    keys = args[0].split() if args and args[0] else []
    if not keys:
        keys = [extra["key"] for _c, extra in load_claims(workspace)]
    if not keys:
        print("утверждений в хранилище нет — мерить нечего", file=sys.stderr)
        return True
    for key in keys:
        report = trace_lesson_provenance(workspace, key)
        print(report.render(), file=sys.stderr)
        agent.log.log("lesson_provenance_measured", {
            "lesson_key": key,
            "state": report.state,
            "verdict": report.verdict,
            "missing": list(report.missing),
        })
    return True


def _handle_ab_experiment(args: list[str], agent: Any) -> bool:
    """`:causal ab <ключ> [k]` — различающий эксперимент: урок OFF против ON
    на настоящем кандидате из бэклога; вердикт подписывается измерением."""
    from core.backlog_selector import load_backlog
    from core.lesson_ab_experiment import run_lesson_ab_experiment

    if not args:
        print("Usage: :causal ab <lesson_key> [k]", file=sys.stderr)
        return True
    lesson_key = args[0]
    k = int(args[1]) if len(args) > 1 and args[1].isdigit() else 4
    workspace = getattr(agent, "workspace", None) or "."
    candidate = next(
        (c for c in load_backlog(workspace)
         if str(getattr(c, "signal_source", "")) == "code_todo"),
        None,
    )
    if candidate is None:
        print("в бэклоге нет code_todo-кандидата — эксперименту не на чем "
              "мерить; выдумывать задачу нельзя", file=sys.stderr)
        return True
    target = str(candidate.target_path)
    try:
        current = pathlib.Path(target).read_text(encoding="utf-8")
    except OSError:
        current = ""
    report = run_lesson_ab_experiment(
        workspace, agent.llm, lesson_key=lesson_key, k=k,
        impl_path=target, quote=str(candidate.problem_quote),
        evidence_ref=str(getattr(candidate, "evidence_ref", "") or target),
        current_content=current,
    )
    print(report.render(), file=sys.stderr)
    agent.log.log("lesson_ab_experiment", {
        "lesson_key": lesson_key, "impl_path": target, "k": k,
        "off_defects": report.off.defects, "off_measured": report.off.measured,
        "on_defects": report.on.defects, "on_measured": report.on.measured,
        "verdict": report.verdict, "measurement_id": report.measurement_id,
    })
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
