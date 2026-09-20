"""Names come from the experiment's question; truth comes from its outcome.

Live run 2026-09-20, after the relaunch: three answers in a row were stamped
content_refuted because the literal gate could not find `f_gw`, `k_bt` and a
number in the evidence — those names live in the CODE the agent ran, and the
code is deliberately kept out of the excerpt («The experiment's question
refuted its answer», docs/CODE_NOTES.md). Two true things had to hold at once:
a name the run itself wrote is not a fabrication, and the question must never
support its own answer. The code now rides behind a marker: the literal gate
sees it, the gates that judge truth cut it off.
"""
from __future__ import annotations

from core.evidence import QUESTION_CODE_MARKER, evidence_from_tool_result
from core.verifier_absence import absent_literal_reason
from core.verifier_utils import truth_excerpt

_CODE = "f_gw = 1.4e-3\nk_bt = 4.141\nprint('f_gw*k_bt =', f_gw * k_bt)"
_OUTPUT = {
    "code": _CODE, "exit_code": 0, "stdout": "f_gw*k_bt = 0.0057974\n", "stderr": "",
    "inputs": [], "missing_inputs": [], "timed_out": False, "duration_ms": 12,
    "stdout_truncated": False,
}


def _probe_evidence():
    return evidence_from_tool_result(
        tool_name="python_probe", arguments={"code": _CODE}, output=_OUTPUT)


def test_a_name_the_run_itself_wrote_is_not_a_fabrication() -> None:
    ev = _probe_evidence()
    assert QUESTION_CODE_MARKER in ev.excerpt and "k_bt" in ev.excerpt
    claim = "Произведение f_gw на k_bt равно 0.0057974"
    assert absent_literal_reason(claim, ev, "tool") is None


def test_the_question_still_cannot_support_its_own_answer() -> None:
    ev = _probe_evidence()
    outcome = truth_excerpt(ev.excerpt)
    assert "0.0057974" in outcome, "исход остаётся уликой"
    assert "k_bt = 4.141" not in outcome, "код — вопрос, не улика истины"
    assert QUESTION_CODE_MARKER not in outcome


def test_a_name_from_nowhere_is_still_refused() -> None:
    ev = _probe_evidence()
    claim = "Значение взято из core/nonexistent_module.py"
    reason = absent_literal_reason(claim, ev, "tool")
    assert reason is not None and "nonexistent_module" in reason.expected
