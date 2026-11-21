"""A lesson reaches the plan it applies to, with its mechanical half
(audit M2, M3; block 4, 2026-09-03).

M3 measured: 359 injections, one lesson key, 347 into `planner.plan` on
every turn regardless of scope — a lesson about test-writing rode along on
every question about anything. M2: `lesson_block_for_prompt` dropped
`machine_action`, and the store's one field mixed the planner's action with
the name of the climb step that last touched the claim.
"""
from __future__ import annotations

from core.causal_claim_store import (
    distilled_lessons,
    lesson_block_for_prompt,
    save_claim,
)
from core.causal_lesson import (
    CausalClaim,
    Explanation,
    GeneralizationTest,
    Intervention,
    Observation,
)
from core.planner import LLMPlanner


def _lesson(episode: str, *, rule: str, scope: str, case: str = "case-x") -> CausalClaim:
    return CausalClaim(
        observation=Observation(episode_id=episode, trace_id="t", run_id="r",
                                evidence_refs=("ev",), observed_mismatch="m"),
        explanations=(
            Explanation(statement="A", author="agent", predicts="pA"),
            Explanation(statement="B", author="agent", predicts="pB", refuted_by="x"),
        ),
        chosen="A",
        violated_invariant="inv",
        intervention=Intervention(mutated="m", predicted="p", observed="o"),
        generalization=GeneralizationTest(case_ref=case, origin_ref=episode, held=True),
        generalized_rule=rule,
        scope=scope,
    )


_PLANNER = _lesson("ep-planner", rule="always check the planner prompt", scope="planner")
_WEB = _lesson("ep-web", rule="retry web_fetch once on timeout",
               scope="web_fetch tool timeouts", case="tools/web_fetch.py")


def test_a_file_hint_selects_by_the_named_file(tmp_path) -> None:
    save_claim(_WEB, workspace=tmp_path)

    assert "web_fetch once" in lesson_block_for_prompt(
        tmp_path, question="compare budgets", file_hint="tools/web_fetch.py")
    assert lesson_block_for_prompt(tmp_path, question="compare budgets") == ""


def test_the_planner_receipts_only_what_it_delivered(tmp_path) -> None:
    save_claim(_PLANNER, workspace=tmp_path)
    save_claim(_WEB, workspace=tmp_path)
    planner = LLMPlanner(llm=None, registry=None, workspace=str(tmp_path))

    block = planner.lesson_block_for_prompt("why does web_fetch time out?")

    assert "web_fetch once" in block and "planner prompt" not in block
    receipts = (tmp_path / "data" / "lesson_injections.jsonl").read_text(
        encoding="utf-8").strip().splitlines()
    assert len(receipts) == 1, "a receipt for a lesson that was not delivered"


def test_the_machine_action_travels_with_the_prose(tmp_path) -> None:
    save_claim(_PLANNER, workspace=tmp_path, machine_action="include_real_signatures")

    block = lesson_block_for_prompt(tmp_path)

    assert "Machine action: include_real_signatures" in block
    assert distilled_lessons(tmp_path)[0].machine_action == "include_real_signatures"
