from core.causal_claim_store import lesson_block_for_prompt, save_claim
from core.causal_lesson import (
    CausalClaim,
    Explanation,
    GeneralizationTest,
    Intervention,
    Observation,
)
from core.planner import LLMPlanner


def _make_claim():
    return CausalClaim(
        observation=Observation(episode_id="ep1", trace_id="tr1", run_id="run1",
            evidence_refs=("ev1",), observed_mismatch="mismatch"),
        explanations=(
            Explanation(statement="explanation A", author="agent", predicts="predA", refuted_by=""),
            Explanation(statement="explanation B", author="agent", predicts="predB", refuted_by="counter-evidence"),
        ),
        chosen="explanation A",
        violated_invariant="every call matches the runtime signature",
        intervention=Intervention(mutated="mut", predicted="pred", observed="obs"),
        generalization=GeneralizationTest(case_ref="case1", origin_ref="origin2", held=True),
        generalized_rule="rule: always check the planner prompt",
        scope="planner",
    )


def test_empty_store_returns_empty_block(tmp_path):
    assert lesson_block_for_prompt(tmp_path) == ""


def test_store_with_lesson_returns_rule_and_refutation(tmp_path):
    save_claim(_make_claim(), workspace=tmp_path)
    block = lesson_block_for_prompt(tmp_path)
    assert "rule: always check the planner prompt" in block
    assert "counter-evidence" in block


def test_planner_with_workspace_injects_lesson_and_receipt(tmp_path):
    save_claim(_make_claim(), workspace=tmp_path)
    planner = LLMPlanner(llm=None, registry=None, workspace=str(tmp_path))
    block = planner.lesson_block_for_prompt()
    assert "rule: always check the planner prompt" in block
    receipt_path = tmp_path / "data" / "lesson_injections.jsonl"
    lines = receipt_path.read_text().strip().splitlines()
    assert len(lines) == 1
    assert "planner" in lines[0]


def test_planner_without_workspace_returns_empty_and_no_receipt(tmp_path):
    planner = LLMPlanner(llm=None, registry=None)
    assert planner.lesson_block_for_prompt() == ""
    receipt_path = tmp_path / "data" / "lesson_injections.jsonl"
    assert not receipt_path.exists()
