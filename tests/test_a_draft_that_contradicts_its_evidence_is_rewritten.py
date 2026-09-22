"""Черновик, противоречащий своим уликам, переписывается до ответа.

Разговор 2026-09-22, 11:52: «пробой это подтверждается: verified» при трёх
упавших пробах; [claim-refuted] стоял, а фраза ушла первой строкой. И
требования второй сборки (не про вопрос, дочитанные свои файлы) до модели
не доходили: синтезатору шли только WORLD_FACING-провалы.
"""
from __future__ import annotations

from types import SimpleNamespace

import core.verifier as verifier_mod
from core.draft_refutation import revise_refuted_draft
from core.replan import ReplanTrigger, failures_for_synthesis


def _trigger(code: str) -> ReplanTrigger:
    return ReplanTrigger(code=code, step_id="s", tool_name=None, arguments={}, reason=code, attempt=0)


def test_demands_for_the_second_draft_reach_the_synthesizer() -> None:
    history = [_trigger("answer_off_topic"), _trigger("unverified_own_file"),
               _trigger("draft_contradicts_evidence"), _trigger("claim_refuted"),
               _trigger("tool_error")]
    seen = [t.code for t in failures_for_synthesis(history, exhausted=False)]
    assert seen == ["answer_off_topic", "unverified_own_file", "draft_contradicts_evidence", "tool_error"]
    assert len(failures_for_synthesis(history, exhausted=True)) == 5


def _chunk(text: str) -> SimpleNamespace:
    reason = SimpleNamespace(code="absence_refuted_by_evidence", explanation="улика говорит иное",
                             computed_from="tool_output:python_probe", actual="exit_code: 1")
    return SimpleNamespace(text=text, reason=reason)


class _Loop:
    verifier_enabled = True
    _last_synth_degraded = False
    _synthesis_expects_contract_headers = True

    def __init__(self):
        self.events: list[tuple[str, dict]] = []
        self.log = SimpleNamespace(log=lambda e, p: self.events.append((e, p)))

    def _sensor_failed(self, name, exc):
        raise AssertionError(f"{name}: {exc}")


def _state(draft: str) -> SimpleNamespace:
    return SimpleNamespace(draft_answer=draft, chain=object(), user_question="q", failure_history=[])


def _fake_verify(refuted_by_answer: dict[str, int]):
    def verify(*, answer, **_kw):
        return SimpleNamespace(chunks=[_chunk(answer)] * refuted_by_answer.get(answer, 0))
    return verify


def test_a_refuted_draft_is_rewritten_once_from_the_same_evidence(monkeypatch) -> None:
    monkeypatch.setattr(verifier_mod, "verify", _fake_verify({"ложь": 1, "правда": 0}))
    loop, st, calls = _Loop(), _state("ложь"), []

    def synthesize(attempt):
        calls.append(st.failure_history[-1].reason)
        return "правда"

    revise_refuted_draft(loop, st, synthesize)

    assert st.draft_answer == "правда"
    assert len(calls) == 1 and "«ложь»" in calls[0] and "exit_code: 1" in calls[0]
    assert st.failure_history[-1].code == "draft_contradicts_evidence"
    assert loop.events[-1][1]["rewritten"] is True


def test_a_rewrite_that_is_no_better_is_not_kept(monkeypatch) -> None:
    monkeypatch.setattr(verifier_mod, "verify", _fake_verify({"ложь": 1, "опять ложь": 1}))
    loop, st = _Loop(), _state("ложь")

    revise_refuted_draft(loop, st, lambda attempt: "опять ложь")

    assert st.draft_answer == "ложь"


def test_a_clean_draft_costs_nothing(monkeypatch) -> None:
    monkeypatch.setattr(verifier_mod, "verify", _fake_verify({}))
    loop, st = _Loop(), _state("правда")

    def synthesize(attempt):
        raise AssertionError("чистый черновик не пересобирается")

    revise_refuted_draft(loop, st, synthesize)

    assert st.draft_answer == "правда" and not st.failure_history
