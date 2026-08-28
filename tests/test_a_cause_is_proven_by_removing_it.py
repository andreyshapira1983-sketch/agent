"""Слайс 3 органа подъёма: причина доказывается её УСТРАНЕНИЕМ, не совпадением.

Проект: docs/audit/CAUSAL_CLIMB_ORGAN_DESIGN.md (MIR-096). DoVer дословно:
атрибуция из журналов — непроверенная гипотеза, пока не подтверждена
исполнением. Здесь исполнение — двурукавный эксперимент на БЕЛОМ СПИСКЕ
чистых функций агента:

  [exp: <цель> | A=<вход с причиной> | B=<вход без причины> | след=<подстрока>]

Следствие есть в рукаве A и исчезает в рукаве B → причина доказана
вмешательством (`Intervention.proves_cause`), выбор достаётся доказанному.
Следствие в ОБОИХ рукавах → механизм гипотезы различия не даёт — она
опровергнута экспериментом. Следствия нет нигде — эксперимент не воспроизвёл
явление: неведение, не вердикт.

Честность к соперникам: доказательство А не трогает Б — чужая гипотеза
падает только от СВОЕЙ пробы или своего эксперимента.

Цели — только белый список чистых функций (ни файлов, ни сети, ни состояния);
неизвестная цель — спецификация невалидна и не исполняется.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from core.causal_claim_store import load_claims, save_claim
from core.causal_climb import propose_explanation
from core.causal_climb_action import (
    experimentable_claims,
    parse_experiment,
    run_claim_experiment,
)
from core.causal_lesson import CausalClaim, Observation


def _claim(*predicts: str) -> CausalClaim:
    return CausalClaim(
        observation=Observation(
            episode_id="ep-1", trace_id="", run_id="r",
            defect_signals=("reasoning_action_mismatch",),
            evidence_refs=("log:t",), observed_mismatch="m",
        ),
        explanations=tuple(
            propose_explanation(f"гипотеза {i}", author="agent", predicts=p)
            for i, p in enumerate(predicts)
        ),
    )


def _agent():
    events: list[tuple[str, dict]] = []
    agent = SimpleNamespace(
        log=SimpleNamespace(log=lambda e, p: events.append((e, p))),
    )
    agent.events = events
    return agent


# Живая цель белого списка: сенсор рассуждение↔действие. Вход
# «текст ;; инструменты» — А несёт причину (нет упоминания инструмента),
# Б — не несёт (глагол осмотра оправдывает file_read после MIR-015).
_EXP_PROVES = (
    "без упоминания сенсор обвиняет "
    "[exp: reasoning_action_check | A=посчитаю в уме ;; web_search | "
    "B=изучу файл конфигурации ;; file_read | след=unjustified=['w]"
)
# Уточнение: следствие — подстрока результата; для А это unjustified с
# web_search, для Б unjustified пуст.


def test_the_experiment_grammar_parses_and_rejects() -> None:
    """Красный свидетель разбора: белый список — закон."""
    ok = parse_experiment(
        "x [exp: reasoning_action_check | A=а ;; t1 | B=б ;; t2 | след=unjust]")
    assert ok is not None and ok.target == "reasoning_action_check"
    assert ok.arm_a == "а ;; t1" and ok.effect == "unjust"

    assert parse_experiment("без спецификации") is None
    assert parse_experiment(
        "x [exp: os.system | A=rm | B=ls | след=x]") is None, "цель вне списка"


def test_a_vanishing_effect_proves_the_cause(tmp_path: Path) -> None:
    """Следствие есть в A и исчезает в B — причина доказана вмешательством."""
    save_claim(_claim(
        _EXP_PROVES,
        "соперник без своей проверки",
    ), workspace=tmp_path)

    outcome = run_claim_experiment(agent=_agent(), workspace=tmp_path)

    assert outcome.did_work
    claim, _extra = load_claims(tmp_path)[0]
    assert claim.chosen == "гипотеза 0", "выбор достался доказанному экспериментом"
    assert claim.intervention is not None
    assert claim.intervention.proves_cause
    rival = claim.explanations[1]
    assert rival.alive, "доказательство А не опровергает Б — честность к соперникам"


def test_an_effect_in_both_arms_refutes_the_hypothesis(tmp_path: Path) -> None:
    """Следствие не исчезло с причиной — механизм гипотезы различия не даёт."""
    both_arms = (
        "обвинение всегда "
        "[exp: reasoning_action_check | A=посчитаю в уме ;; web_search | "
        "B=сравню два числа ;; web_search | след=unjustified=['w]"
    )
    save_claim(_claim(both_arms, "соперник"), workspace=tmp_path)

    outcome = run_claim_experiment(agent=_agent(), workspace=tmp_path)

    claim, _extra = load_claims(tmp_path)[0]
    assert not claim.explanations[0].alive, "следствие в обоих рукавах — опровергнута"
    assert "ОБОИХ" in claim.explanations[0].refuted_by
    assert claim.chosen == ""
    assert outcome.did_work


def test_no_effect_anywhere_is_ignorance_not_a_verdict(tmp_path: Path) -> None:
    nowhere = (
        "х [exp: reasoning_action_check | A=прочитаю файл ;; file_read | "
        "B=изучу файл ;; file_read | след=НЕТ_ТАКОЙ_ПОДСТРОКИ]"
    )
    save_claim(_claim(nowhere, "соперник"), workspace=tmp_path)
    agent = _agent()

    outcome = run_claim_experiment(agent=agent, workspace=tmp_path)

    claim, _extra = load_claims(tmp_path)[0]
    assert all(e.alive for e in claim.explanations)
    assert not outcome.did_work
    assert any(e == "causal_experiment_inconclusive" for e, _p in agent.events)


def test_the_signal_and_the_executor_ship_together() -> None:
    import inspect

    from core import campaign_io as mod
    from core.best_next_action import select_best_next_action

    picked = select_best_next_action(experimentable_claims_count=1)
    silent = select_best_next_action(experimentable_claims_count=0)
    assert picked.action == "run_claim_experiment"
    assert silent.action != "run_claim_experiment"
    assert '"run_claim_experiment"' in inspect.getsource(mod._default_execute_action)


def test_experimentable_lists_only_open_specced_claims(tmp_path: Path) -> None:
    save_claim(_claim(_EXP_PROVES, "без спеки"), workspace=tmp_path)

    assert len(experimentable_claims(tmp_path)) == 1

    import dataclasses
    claim, _extra = load_claims(tmp_path)[0]
    save_claim(dataclasses.replace(claim, chosen="гипотеза 0"),
               workspace=tmp_path)
    assert experimentable_claims(tmp_path) == ()


def test_the_experiment_calls_no_model() -> None:
    import inspect

    from core import causal_climb_action as mod

    src = inspect.getsource(mod.run_claim_experiment)
    assert ".complete(" not in src and "llm" not in src
