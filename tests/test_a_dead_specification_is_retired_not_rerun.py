"""Спецификация, которая ничего не воспроизводит, — не эксперимент.

Живой замер 2026-09-20 (двое суток трасс): `run_claim_experiment` дал 46
исходов «следствие не воспроизвелось ни в одном рукаве» — ВСЕ на одной
заявке `cclaim_908eb82c459d`, и ни одного вердикта за всё время. Причина в
коде, а не в удаче: рукава родились прозой («трейс с последним шагом
partially_achieved»), которую цель белого списка на вход не берёт, а
`след=` оказалось целым рассуждением («если mismatch исчезает только в
B — …»), которого в выводе цели не бывает по построению. Проверки на
исполнимость при рождении не было, а неопределённый исход не стоил спеке
жизни — значит, следующий цикл брал ту же мёртвую спеку первой.

Два правила здесь: спека рождается только исполнимой, а неопределённый
исход её снимает и возвращает заявку под новое рождение.
"""
from __future__ import annotations

import dataclasses

from core.causal_claim_store import load_claims, save_claim
from core.causal_climb_action import (
    awaiting_experiment,
    experimentable_claims,
    parse_experiment,
    run_claim_experiment,
    spec_is_executable,
)
from core.causal_lesson import CausalClaim, Explanation, Observation

#: Рукава-проза и след-рассуждение: ровно то, что родила модель в проде.
_DEAD = ("[exp: reasoning_action_check | A=трейс с partially_achieved, где "
         "reasoning консистентен | B=трейс, где reasoning пуст | след=если "
         "mismatch исчезает только в B — причина в статусе]")
#: Исполнимая спека: рукав — вход цели, след — подстрока её вывода.
_LIVE = ("[exp: reasoning_action_check | A=я посчитаю это ;; current_time | "
         "B=мне нужна текущая дата ;; current_time | "
         "след=unjustified=['current_time']]")


class _Agent:
    llm = None
    log = None


def _claim(spec: str) -> CausalClaim:
    return CausalClaim(
        observation=Observation(
            episode_id="ep1", trace_id="t1", run_id="r1",
            evidence_refs=("ev1",), observed_mismatch="детектор срабатывает",
            defect_signals=("reasoning_action_mismatch",),
        ),
        explanations=(
            Explanation(statement="статус виноват", author="agent",
                        predicts="так и будет " + spec),
        ),
    )


def test_a_specification_that_runs_on_nothing_is_not_executable() -> None:
    dead = parse_experiment(_DEAD)
    assert dead is not None, "разбор формы проходит — беда не в синтаксисе"
    assert spec_is_executable(dead), "должна назваться причина неисполнимости"
    live = parse_experiment(_LIVE)
    assert live is not None and spec_is_executable(live) == ""


def test_an_inconclusive_run_retires_the_specification(tmp_path) -> None:
    save_claim(_claim(_DEAD), workspace=tmp_path)
    assert len(experimentable_claims(tmp_path)) == 1

    outcome = run_claim_experiment(agent=_Agent(), workspace=tmp_path)
    assert outcome.result != "completed", "вердикта не было — успехом не зовём"

    (claim, _extra), = load_claims(tmp_path)
    live = [e for e in claim.explanations if e.alive]
    assert live, "гипотеза жива: спека сломана, а не гипотеза"
    assert parse_experiment(live[0].predicts) is None, "мёртвая спека снята"
    assert awaiting_experiment(claim), "заявка снова ждёт спецификации"
    assert not experimentable_claims(tmp_path), "второй прогон её не повторит"


def test_a_working_specification_still_reaches_its_verdict(tmp_path) -> None:
    save_claim(_claim(_LIVE), workspace=tmp_path)
    outcome = run_claim_experiment(agent=_Agent(), workspace=tmp_path)
    assert outcome.result == "completed"

    (claim, _extra), = load_claims(tmp_path)
    assert parse_experiment(claim.explanations[0].predicts) is not None
    assert not awaiting_experiment(claim)


def test_a_born_specification_is_executed_before_it_is_saved(tmp_path) -> None:
    from core.causal_climb_action import _birth_experiment_spec

    class _LLM:
        def complete(self, **_kw) -> str:
            return _DEAD

    agent = _Agent()
    agent.llm = _LLM()
    claim = dataclasses.replace(_claim(""), explanations=(
        Explanation(statement="A", author="agent", predicts="p1"),
        Explanation(statement="B", author="agent", predicts="p2"),
    ))
    assert _birth_experiment_spec(agent, claim) is None
