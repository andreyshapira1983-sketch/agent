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

from core.low_evidence_policy import _carries_plan_proposal


class TestARealPlanStaysAPlan:
    def test_a_plan_that_states_a_world_fact_still_owes_evidence(self):
        # The existing rule, unchanged: a proposal that also asserts about the
        # world is not exempt.
        answer = "Предлагаю купить лицензию за 500 долларов и затем внедрить."
        assert _carries_plan_proposal(answer) is False
