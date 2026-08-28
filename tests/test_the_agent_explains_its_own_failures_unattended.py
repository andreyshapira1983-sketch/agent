"""Слайс 1 органа подъёма: наблюдение без объяснений становится работой агента.

Проект и конституция: docs/audit/CAUSAL_CLIMB_ORGAN_DESIGN.md (MIR-096).

Границы слайса, каждая приколочена здесь:
- сигнал: наблюдение, чей отпечаток не покрыт ни одной заявкой; самое
  повторяющееся — первым (повторяемость = сильнейший повод расследования);
- действие рождается только с исполнителем (анти-MIR-179);
- гипотезы фальсифицируемы или не принимаются: без ПРЕДСКАЗАНИЯ гипотеза
  отбрасывается, меньше двух выживших — отказ без записи;
- автор не судит себя (Huang ICLR'24): слайс 1 НЕ выбирает победителя —
  заявка сохраняется с chosen == "" до различения;
- dry-run не тратит и не пишет.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from core.best_next_action import select_best_next_action
from core.causal_claim_store import load_claims, save_claim
from core.causal_climb_action import (
    explain_causal_observation,
    unexplained_observations,
)
from core.causal_lesson import CausalClaim, Observation
from core.causal_store import CausalObservationStore, ObservationRecord


def _record(signals: tuple[str, ...], *, occurrences: int = 1,
            mismatch: str = "m") -> ObservationRecord:
    import hashlib
    key = "|".join(sorted(signals))
    return ObservationRecord(
        fingerprint="cobs_" + hashlib.sha256(key.encode()).hexdigest()[:12],
        defect_signals=signals,
        observed_mismatch=mismatch,
        evidence_refs=("log:trace-1",),
        episode_ids=("ep-1",),
        occurrences=occurrences,
    )


def _seed(tmp_path: Path, records: list[ObservationRecord]) -> None:
    store = CausalObservationStore(tmp_path / "data" / "causal_observations.jsonl")
    from core.state_integrity import rewrite_state_jsonl
    rewrite_state_jsonl(store.path, [r.to_dict() for r in records])


class _ScriptedLLM:
    def __init__(self, reply: str):
        self._reply = reply
        self.calls = 0

    def complete(self, **_kw) -> str:
        self.calls += 1
        return self._reply


def _agent(reply: str):
    events: list[tuple[str, dict]] = []
    agent = SimpleNamespace(
        llm=_ScriptedLLM(reply),
        log=SimpleNamespace(log=lambda e, p: events.append((e, p))),
        model_router=SimpleNamespace(usage_ledger=None),
    )
    agent.events = events
    return agent


_GOOD_REPLY = (
    "ОБЪЯСНЕНИЕ 1: планировщик не получает список инструментов до выбора.\n"
    "ПРЕДСКАЗАНИЕ 1: в журнале planner нет поля tools при этих провалах.\n"
    "ОБЪЯСНЕНИЕ 2: словарь сенсора не знает глаголов осмотра.\n"
    "ПРЕДСКАЗАНИЕ 2: обвинения исчезают на ходах с inspect/изучу в тексте.\n"
)


def test_an_uncovered_observation_is_listed_most_recurrent_first(tmp_path: Path) -> None:
    """Красный свидетель сигнала: непокрытое видно, покрытое — нет."""
    covered = _record(("citation_fabricated",), occurrences=9)
    naked_rare = _record(("self_contradiction",), occurrences=2)
    naked_hot = _record(("reasoning_action_mismatch",), occurrences=7)
    _seed(tmp_path, [covered, naked_rare, naked_hot])
    save_claim(
        CausalClaim(observation=Observation(
            episode_id="ep-1", trace_id="", run_id="r",
            defect_signals=("citation_fabricated",),
        )),
        workspace=tmp_path,
    )

    listed = unexplained_observations(tmp_path)

    fps = [r.fingerprint for r in listed]
    assert covered.fingerprint not in fps
    assert fps[0] == naked_hot.fingerprint, "повторяемость ведёт очередь"
    assert naked_rare.fingerprint in fps


def test_the_signal_births_the_action_and_zero_births_none() -> None:
    picked = select_best_next_action(unexplained_observations_count=3)
    silent = select_best_next_action(unexplained_observations_count=0)

    assert picked.action == "explain_causal_observation"
    assert silent.action != "explain_causal_observation"


def test_the_action_never_ships_without_its_executor() -> None:
    """Анти-MIR-179: имя, которое рождает выбиратель, знает исполнитель."""
    import inspect

    from core import campaign_io as mod

    src = inspect.getsource(mod._default_execute_action)
    assert '"explain_causal_observation"' in src


def test_two_falsifiable_hypotheses_become_a_claim_with_no_winner(tmp_path: Path) -> None:
    """Сердце слайса: заявка с двумя предсказаниями и БЕЗ выбранного."""
    _seed(tmp_path, [_record(("reasoning_action_mismatch",), occurrences=5)])
    agent = _agent(_GOOD_REPLY)

    outcome = explain_causal_observation(
        agent=agent, workspace=tmp_path, dry_run=False)

    assert outcome.did_work
    claims = load_claims(tmp_path)
    assert len(claims) == 1
    claim, _extra = claims[0]
    assert len(claim.explanations) == 2
    assert all(e.predicts.strip() for e in claim.explanations)
    assert claim.chosen == "", "слайс 1 не судит: выбор — за различением"


def test_a_prediction_free_hypothesis_is_rejected_at_the_gate(tmp_path: Path) -> None:
    """Ворота качества: без предсказания гипотеза мертва; одна выжившая — отказ."""
    _seed(tmp_path, [_record(("reasoning_action_mismatch",))])
    reply = (
        "ОБЪЯСНЕНИЕ 1: что-то сломалось.\n"
        "ПРЕДСКАЗАНИЕ 1:\n"
        "ОБЪЯСНЕНИЕ 2: словарь сенсора беден.\n"
        "ПРЕДСКАЗАНИЕ 2: обвинения исчезают после пополнения словаря.\n"
    )
    agent = _agent(reply)

    outcome = explain_causal_observation(
        agent=agent, workspace=tmp_path, dry_run=False)

    assert not outcome.did_work
    assert load_claims(tmp_path) == ()
    declined = [p for e, p in agent.events if e == "causal_climb_declined"]
    assert declined and "конкуриру" in declined[0]["reason"], (
        "молчаливый отказ нельзя расследовать — причина обязана лечь в журнал")


def test_an_already_covered_store_declines_without_spending(tmp_path: Path) -> None:
    _seed(tmp_path, [])
    agent = _agent(_GOOD_REPLY)

    outcome = explain_causal_observation(
        agent=agent, workspace=tmp_path, dry_run=False)

    assert not outcome.did_work
    assert agent.llm.calls == 0, "нет наблюдения — нет траты"


def test_dry_run_spends_nothing_and_writes_nothing(tmp_path: Path) -> None:
    _seed(tmp_path, [_record(("reasoning_action_mismatch",))])
    agent = _agent(_GOOD_REPLY)

    outcome = explain_causal_observation(
        agent=agent, workspace=tmp_path, dry_run=True)

    assert not outcome.did_work
    assert agent.llm.calls == 0
    assert load_claims(tmp_path) == ()
