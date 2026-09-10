"""A report about an executed step is not a plan proposal.

Exam 2026-09-05, session exam_k. The genre exemption (a plan owes no evidence)
keyed on the words «шаг», «затем» and the list markers "1." "2." "3." anywhere
in the text. Three answers that REPORTED tool results were reclassified as
plans, `evidence_support` logged `no_evidence_expected`, and the gate stood
down:

* turn 39 — «Все шаги плана выполнились успешно (exit_code=0)»;
* turn 41 — «Шаг 1 (last_n=60, без фильтра) вернул events_returned=4,
  fields_truncated=0» — the tool had returned 60 and 7; the synthesizer took
  step 2's numbers, and the false claim reached the operator as verified;
* turn 43 — «601.23365 с ≈ 600.3 с» — the "1." marker matched a decimal.

A report has a past-tense verb or a `field=value` from a tool result; a plan
has neither. Numbered lists count only at the start of a line.
"""

from __future__ import annotations

import pytest

from core.low_evidence_policy import _carries_plan_proposal, is_evidence_expected

_TURN_39 = (
    "В каталоге `core` ровно **9** файлов `.py` содержат слово `failure_history`. "
    "Все шаги плана выполнились успешно (exit_code=0)."
)
_TURN_41 = (
    "Шаг 1 (last_n=60, без фильтра) вернул events_returned=4, fields_truncated=0; "
    "событие с полем, у которого наибольшее chars, — planner. Шаг 2 "
    "(event_filter=[\"planner\"], last_n=5) вернул 4 события."
)
_TURN_43 = (
    "Самый длинный промежуток — 600.3 секунды между `injection_suspicious` и "
    "`run_objects_settled`. Длительность: 16:41:27.169987 − 16:31:25.936337 = "
    "601.23365 с."
)
_REAL_PLAN = (
    "Шаг 1: ставлю порог 0.55 при длине 40. Шаг 2: окно 32 символа. "
    "Затем прогоняю выборку и сравниваю."
)
_NUMBERED_PLAN = "Предлагаю так:\n1. прочитать лог;\n2. сузить фильтр;\n3. повторить замер."


class TestAReportIsNotAProposal:
    @pytest.mark.parametrize("answer", [_TURN_39, _TURN_41, _TURN_43], ids=["t39", "t41", "t43"])
    def test_the_three_exam_answers_are_reports(self, answer: str):
        assert _carries_plan_proposal(answer) is False

    @pytest.mark.parametrize("answer", [_TURN_39, _TURN_41, _TURN_43], ids=["t39", "t41", "t43"])
    def test_so_they_owe_evidence_whatever_the_role(self, answer: str):
        for role in ("programmer", "operator_chat", "researcher"):
            assert is_evidence_expected(
                role=role, chain_was_empty=False, realtime_required=False, answer=answer,
            ) is True, role

    def test_a_decimal_is_not_a_list_item(self):
        assert _carries_plan_proposal("Затем измерил: 1.02 с между вызовами.") is False
        assert _carries_plan_proposal("Итого 3.5 попытки на ход.") is False


class TestARealPlanStaysAPlan:
    @pytest.mark.parametrize("answer", [_REAL_PLAN, _NUMBERED_PLAN], ids=["steps", "numbered"])
    def test_a_proposal_owes_no_evidence(self, answer: str):
        assert _carries_plan_proposal(answer) is True
        assert is_evidence_expected(
            role="operator_chat", chain_was_empty=False, realtime_required=False, answer=answer,
        ) is False

    def test_a_plan_that_states_a_world_fact_still_owes_evidence(self):
        # The existing rule, unchanged: a proposal that also asserts about the
        # world is not exempt.
        answer = "Предлагаю купить лицензию за 500 долларов и затем внедрить."
        assert _carries_plan_proposal(answer) is False
