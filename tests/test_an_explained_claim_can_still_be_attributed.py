"""EXPLAINED has an exit (audit M1, block 4, 2026-09-03).

Live store on 2026-09-03: 25 REFUTED, 15 EXPLAINED, 10 OBSERVED, 1 LESSON,
0 ATTRIBUTED ever. Every EXPLAINED claim had an explanation chosen by the
journal probes and nothing more: `experimentable_claims` skipped any claim
with `chosen`, the birth action took only claims marked «awaiting», and
`violated_invariant` was a prose field no machine wrote. Slice 2 disqualified
slice 3; the ladder's third rung was unreachable.

Now a chosen-but-unproven claim is a birth candidate; birth asks for a
one-hypothesis removal experiment AND the invariant the cause violates; the
experiment proves the chosen cause by removing it — or un-chooses it.
"""
from __future__ import annotations

import dataclasses
from pathlib import Path
from types import SimpleNamespace

from core.causal_claim_store import load_claims, save_claim
from core.causal_climb_action import (
    AWAITING_EXPERIMENT_MARK,
    birth_candidates,
    birth_experiment_specs,
    experimentable_claims,
    run_claim_experiment,
)
from core.causal_lesson import CausalClaim, Explanation, Observation, state_of

_SPEC = (
    "[exp: reasoning_action_check | A=посчитаю в уме ;; web_search | "
    "B=изучу файл конфигурации ;; file_read | след=unjustified=['w]"
)
_BOTH_ARMS = (
    "[exp: reasoning_action_check | A=посчитаю в уме ;; web_search | "
    "B=сравню два числа ;; web_search | след=unjustified=['w]"
)
_INVARIANT = "каждое действие названо в рассуждении до вызова"


def _explained(chosen_predicts: str = "сенсор обвиняет без упоминания") -> CausalClaim:
    return CausalClaim(
        observation=Observation(
            episode_id="ep-explained", trace_id="", run_id="r",
            defect_signals=("reasoning_action_mismatch",),
            evidence_refs=("log:t",), observed_mismatch="m",
        ),
        explanations=(
            Explanation(statement="причина A", author="agent", predicts=chosen_predicts),
            Explanation(statement="причина B", author="agent", predicts="иное",
                        refuted_by="probe logs/x: наблюдалось обратное"),
        ),
        chosen="причина A",
    )


def _agent(reply: str = ""):
    events: list[tuple[str, dict]] = []
    agent = SimpleNamespace(
        llm=SimpleNamespace(complete=lambda **kw: reply),
        log=SimpleNamespace(log=lambda e, p: events.append((e, p))),
    )
    agent.events = events
    return agent


def test_the_live_shape_is_explained_and_stuck_by_the_invariant() -> None:
    """The specimen: chosen by probes, no invariant, no intervention."""
    claim = _explained()
    assert state_of(claim) == "EXPLAINED"
    assert claim.violated_invariant == "" and claim.intervention is None


def test_a_chosen_claim_without_proof_is_a_birth_candidate(tmp_path: Path) -> None:
    import core.campaign_io as cio

    save_claim(_explained(), workspace=tmp_path)

    assert len(birth_candidates(tmp_path)) == 1, (
        "the claim is not marked «awaiting» and was invisible to birth"
    )
    assert cio._birth_candidate_count(tmp_path) == 1, "signal and executor drifted"
    assert experimentable_claims(tmp_path) == (), "no spec yet — nothing to run"


def test_birth_writes_the_spec_to_the_chosen_and_names_the_invariant(tmp_path: Path) -> None:
    save_claim(_explained(), workspace=tmp_path)
    agent = _agent(_SPEC + "\nИНВАРИАНТ: " + _INVARIANT + "\n")

    outcome = birth_experiment_specs(agent=agent, workspace=tmp_path)

    assert outcome.did_work, outcome
    claim, _extra = load_claims(tmp_path)[0]
    assert claim.violated_invariant == _INVARIANT, "the prose field a machine never wrote"
    assert "[exp:" in claim.explanations[0].predicts, "the spec went to the CHOSEN explanation"
    assert "[exp:" not in claim.explanations[1].predicts
    assert AWAITING_EXPERIMENT_MARK not in claim.notes
    assert len(experimentable_claims(tmp_path)) == 1, "now the experiment can run"
    born = [p for e, p in agent.events if e == "spec_born"]
    assert born and born[0]["invariant_named"] is True


def test_birth_without_an_invariant_is_not_a_birth(tmp_path: Path) -> None:
    """A spec without the invariant would leave the rung half-built; refused,
    and the claim keeps its shape (the inexpressible note applies, as for a
    pair of hypotheses the model cannot split)."""
    save_claim(_explained(), workspace=tmp_path)
    agent = _agent(_SPEC + "\n")

    outcome = birth_experiment_specs(agent=agent, workspace=tmp_path)

    assert not outcome.did_work
    claim, _extra = load_claims(tmp_path)[0]
    assert claim.violated_invariant == ""
    assert "[exp:" not in claim.explanations[0].predicts


def test_the_experiment_proves_the_chosen_cause_and_attributes(tmp_path: Path) -> None:
    claim = dataclasses.replace(
        _explained("сенсор обвиняет " + _SPEC), violated_invariant=_INVARIANT,
    )
    save_claim(claim, workspace=tmp_path)

    outcome = run_claim_experiment(agent=_agent(), workspace=tmp_path)

    assert outcome.did_work
    after, _extra = load_claims(tmp_path)[0]
    assert after.chosen == "причина A"
    assert after.intervention is not None and after.intervention.proves_cause
    assert state_of(after) == "ATTRIBUTED", state_of(after)


def test_an_effect_in_both_arms_unchooses_the_cause(tmp_path: Path) -> None:
    """A refuted explanation cannot stay chosen (ladder invariant 6)."""
    save_claim(_explained("обвинение всегда " + _BOTH_ARMS), workspace=tmp_path)

    run_claim_experiment(agent=_agent(), workspace=tmp_path)

    after, _extra = load_claims(tmp_path)[0]
    assert after.chosen == ""
    assert not after.explanations[0].alive
    assert after.intervention is None


def test_a_proven_claim_is_closed_at_this_rung(tmp_path: Path) -> None:
    """Control: after the intervention nothing re-runs it."""
    claim = dataclasses.replace(
        _explained("сенсор обвиняет " + _SPEC), violated_invariant=_INVARIANT,
    )
    save_claim(claim, workspace=tmp_path)
    run_claim_experiment(agent=_agent(), workspace=tmp_path)

    assert experimentable_claims(tmp_path) == ()
    assert birth_candidates(tmp_path) == ()
