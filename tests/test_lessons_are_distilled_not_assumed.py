"""Наблюдения не конденсируются в «истину»: урок — только полная лестница.

Background: docs/CODE_NOTES.md, "Observations are not lessons".
"""
from __future__ import annotations

import json

from core.causal_claim_store import (
    LessonCard,
    distilled_lessons,
    load_claims,
    save_claim,
)
from core.causal_climb import (
    attach_explanations,
    name_scope,
    propose_explanation,
    refute,
)
from core.causal_lesson import (
    CausalClaim,
    GeneralizationTest,
    Intervention,
    Observation,
    state_of,
)
from core.self_task_producer import _task_builder_generate

_ORIGIN = Observation(
    episode_id="ep-run-a", trace_id="trace_a", run_id="run_a",
    defect_signals=("phantom_signature",),
    evidence_refs=("approval:ain_31874b06", "trace:16b89e12", "trace:c7877cc0"),
    observed_mismatch="RepairProposal called with invented kwargs three times",
)


def _observed_only() -> CausalClaim:
    return CausalClaim(observation=_ORIGIN)


def _full_ladder() -> CausalClaim:
    """Каждая ступень принесена явно — ни одна не выведена из повторов."""
    chosen = propose_explanation(
        "the builder prompt never shows real signatures, so the model guesses",
        author="operator",
        predicts="shown the real signature, the model stops inventing kwargs",
    )
    rival = refute(
        propose_explanation(
            "the model cannot write valid Python tests at all",
            author="agent",
        ),
        "generated tests parsed cleanly and imported real modules; "
        "only the kwargs were invented",
    )
    claim = attach_explanations(
        _observed_only(), [chosen, rival],
        chosen=chosen.statement,
        violated_invariant="a test call must match the real signature",
    )
    claim = CausalClaim(
        **{**claim.__dict__,
           "intervention": Intervention(
               mutated="added real signatures to the builder prompt",
               predicted="phantom kwargs disappear on the same candidate",
               observed="measured: 0 of 3 generations invented kwargs",
           ),
           "generalized_rule": (
               "when generating tests against an existing Python API, the model "
               "invents parameters unless the real signature is in the prompt"
           ),
           "generalization": GeneralizationTest(
               case_ref="gen-case-other-module", origin_ref="ep-run-a", held=True,
           )},
    )
    return name_scope(claim, "generated repair acceptance tests")


def test_three_repeats_do_not_make_a_lesson(tmp_path):
    """Предохранитель оператора: «3 раза видели» не становится «урок» само.
    Повторы дают только повод расследовать — состояние OBSERVED.
    """
    save_claim(_observed_only(), workspace=tmp_path)

    assert state_of(_observed_only()) == "OBSERVED"
    assert distilled_lessons(tmp_path) == ()


def test_only_the_full_ladder_reaches_the_planner(tmp_path):
    claim = _full_ladder()
    assert state_of(claim) == "LESSON"

    save_claim(
        claim, workspace=tmp_path,
        directive="verify calls against the runtime signature before proposing",
        machine_action="include_real_signatures",
    )
    cards = distilled_lessons(tmp_path)

    assert len(cards) == 1
    card = cards[0]
    assert card.scope == "generated repair acceptance tests"
    assert card.machine_action == "include_real_signatures"
    assert "runtime signature" in card.directive


def test_a_refuted_claim_never_returns(tmp_path):
    claim = CausalClaim(
        **{**_full_ladder().__dict__, "refuted_reason": "prediction failed live"},
    )

    save_claim(claim, workspace=tmp_path)

    assert distilled_lessons(tmp_path) == ()


def test_generalization_on_the_origin_case_does_not_count(tmp_path):
    """Самоподтверждение закрыто и на выходе из хранилища."""
    claim = _full_ladder()
    claim = CausalClaim(
        **{**claim.__dict__,
           "generalization": GeneralizationTest(
               case_ref="ep-run-a", origin_ref="ep-run-a", held=True)},
    )

    save_claim(claim, workspace=tmp_path)

    assert state_of(claim) != "LESSON"
    assert distilled_lessons(tmp_path) == ()


def test_roundtrip_preserves_the_ladder(tmp_path):
    save_claim(_full_ladder(), workspace=tmp_path, directive="d")

    (loaded, extra), = load_claims(tmp_path)

    assert state_of(loaded) == "LESSON"
    assert loaded.generalized_rule == _full_ladder().generalized_rule
    assert extra["directive"] == "d"


def test_saving_twice_updates_one_row_not_two(tmp_path):
    """Подъём — это ОДНО утверждение в разных состояниях, а не история строк."""
    save_claim(_observed_only(), workspace=tmp_path)
    save_claim(_full_ladder(), workspace=tmp_path, directive="d")

    assert len(load_claims(tmp_path)) == 1
    assert len(distilled_lessons(tmp_path)) == 1


class _CapturingLLM:
    def __init__(self) -> None:
        self.system = ""
        self.user = ""

    def complete(self, *, system: str, user: str, **_kw) -> str:
        self.system, self.user = system, user
        return json.dumps({
            "task_title": "t", "task_summary": "s",
            "impl_path": "core/self_repair_models.py",
            "test_path": "tests/test_x.py",
            "test_lines": ["def test_a():", "    assert 1 == 2"],
            "confidence": 0.9,
        })


def _card() -> LessonCard:
    return LessonCard(
        rule="the model invents parameters unless the real signature is shown",
        scope="generated repair acceptance tests",
        directive="verify calls against the runtime signature before proposing",
        machine_action="include_real_signatures",
        evidence=("approval:ain_31874b06",),
        cases=("ep-run-a", "gen-case-other-module"),
    )


def test_without_a_lesson_the_prompt_is_the_old_one():
    """Поведение A: пустое хранилище — подсказка прежняя, без сигнатур."""
    llm = _CapturingLLM()

    _task_builder_generate(
        llm, impl_path="core/self_repair_models.py", quote="q",
        evidence_ref="e", current_content="",
    )

    assert "Real signatures" not in llm.user
    assert "LESSON" not in llm.system


def test_a_lesson_changes_the_next_action():
    """Поведение B: урок в хранилище — строитель получает директиву И настоящие
    сигнатуры из работающего кода. Изменение будущего действия из собственного
    прошлого опыта, а не чтение старых записей.
    """
    llm = _CapturingLLM()

    _task_builder_generate(
        llm, impl_path="core/self_repair_models.py", quote="q",
        evidence_ref="e", current_content="", lessons=(_card(),),
    )

    assert "LESSON" in llm.system
    assert "runtime signature" in llm.system
    assert "Real signatures" in llm.user
    assert "RepairProposal(" in llm.user


def test_signature_doubt_is_silence():
    """Модуль не импортируется — блока сигнатур нет, генерация не падает."""
    llm = _CapturingLLM()

    _task_builder_generate(
        llm, impl_path="core/no_such_module_xyz.py", quote="q",
        evidence_ref="e", current_content="", lessons=(_card(),),
    )

    assert "Real signatures" not in llm.user
    assert "LESSON" in llm.system
