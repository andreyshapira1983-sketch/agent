"""A banked procedure must record what the run was for, not just which tools ran.

Measured on the live agent: five rounds produced procedures named
``"Workflow using shell_exec, shell_exec"`` with steps ``"Run tool: shell_exec"``
twice. That is the shape of a run. It carries no information about what the
workflow was ever good for — not for a human reader, and not for the retrieval
search, which scores on name/tags/steps and therefore scored zero against any
query about the subject.

The boundary the operator set for this change is the second half of this file:
**what** is stored changes; **when** a procedure is created does not. Widening
admission would bank better-written lessons behind the same false successes.
"""
from __future__ import annotations

from core.smart_memory import (
    ProcedureRecord,
    episode_from_agent_cycle,
    lesson_from_episode,
    procedure_credit_allowed,
    procedure_from_episode,
)


def _episode(**kw):
    base = dict(
        goal="Answer the question: where is the output lost",
        question="Where is the shell_exec output lost on the way to the synthesizer?",
        answer="a",
        tools_used=["shell_exec", "file_read"],
        source_labels=["shell_exec:grep -n", "file:core/loop.py"],
        verified_chunks=8,
        unverified_chunks=1,
        declared_completion="achieved",
    )
    base.update(kw)
    return episode_from_agent_cycle(**base)


# ---------------------------------------------------------------------------
# The lesson is assembled from what was observed, and nothing else
# ---------------------------------------------------------------------------

def test_the_lesson_names_the_request():
    lesson = lesson_from_episode(_episode())
    assert "Where is the shell_exec output lost" in lesson


def test_the_lesson_records_the_method_and_the_evidence():
    lesson = lesson_from_episode(_episode())
    assert "shell_exec->file_read" in lesson
    assert "file:core/loop.py" in lesson


def test_the_lesson_records_how_the_claims_held():
    assert "8/9 claims verified" in lesson_from_episode(_episode())


def test_the_lesson_records_an_observed_fault():
    """A workflow that worked despite a fault is not the same as a clean one."""
    lesson = lesson_from_episode(
        _episode(defect_signals=["reasoning_action_mismatch"])
    )
    assert "observed: reasoning_action_mismatch" in lesson


def test_no_request_means_no_lesson():
    """Empty is the honest answer when the run left nothing to say."""
    assert lesson_from_episode(_episode(question="")) == ""


def test_a_procedure_without_material_stores_no_lesson():
    procedure = procedure_from_episode(_episode(question=""))
    assert procedure is not None
    assert procedure.lessons == ()


def test_long_evidence_lists_are_summarised_not_dropped():
    lesson = lesson_from_episode(
        _episode(source_labels=[f"file:f{i}.py" for i in range(6)])
    )
    assert "+3 more" in lesson


# ---------------------------------------------------------------------------
# The record says what it was for
# ---------------------------------------------------------------------------

def test_the_name_leads_with_the_subject_not_the_tools():
    name = procedure_from_episode(_episode()).name
    assert name.startswith("Where is the shell_exec output lost")


def test_the_first_step_is_the_situation():
    steps = procedure_from_episode(_episode()).steps
    assert steps[0].startswith("Situation: Where is the shell_exec output lost")


def test_the_tool_sequence_is_still_recorded():
    """The method is honest history and must survive — it just is not the whole record."""
    steps = procedure_from_episode(_episode()).steps
    assert "Run tool: shell_exec" in steps
    assert "Run tool: file_read" in steps


def test_the_evidence_is_named_in_the_steps():
    steps = procedure_from_episode(_episode()).steps
    assert any("file:core/loop.py" in step for step in steps)


def test_subject_tokens_join_the_trigger_tags():
    """Retrieval scores on name/tags/steps; all three used to be tool names only.

    Asserted on a mid-sentence word: the shared tokenizer keeps trailing
    punctuation, so the final word arrives as `synthesizer?`. That is MIR-007's
    defect, registered and out of scope here — this change must not be credited
    with fixing it, and must not be blamed for it either.
    """
    tags = procedure_from_episode(_episode()).trigger_tags
    assert "output" in tags
    assert "lost" in tags


def test_a_run_without_a_recorded_question_still_yields_a_usable_record():
    procedure = procedure_from_episode(_episode(question=""))
    assert procedure.steps[0] == "Situation: (not recorded)"
    assert procedure.name.startswith("Workflow using")


# ---------------------------------------------------------------------------
# Accumulation — the known key-pooling defect must stay visible
# ---------------------------------------------------------------------------

def test_a_second_episode_appends_its_lesson():
    first = procedure_from_episode(_episode())
    second = first.with_episode(
        _episode(question="A completely unrelated question about budgets")
    )
    assert len(second.lessons) == 2, (
        "two unrelated runs merged on the same tool-shape key; a single summary "
        "line would describe one of them and silently claim the other's success"
    )
    assert any("unrelated question about budgets" in x for x in second.lessons)


def test_the_same_episode_twice_adds_one_lesson():
    episode = _episode()
    once = procedure_from_episode(episode).with_episode(episode)
    twice = once.with_episode(episode)
    assert once.lessons == twice.lessons


def test_an_uncredited_episode_contributes_no_lesson():
    """No credit, no lesson — the two move together."""
    credited = procedure_from_episode(_episode())
    blocked = credited.with_episode(
        _episode(
            defect_signals=["obligation_silently_missing"],
            declared_completion="achieved",
        )
    )
    assert blocked.lessons == credited.lessons


# ---------------------------------------------------------------------------
# The boundary: what is stored changed, when it is stored did not
# ---------------------------------------------------------------------------

def test_admission_still_requires_credit():
    blocked = _episode(
        defect_signals=["obligation_silently_missing"],
        declared_completion="achieved",
    )
    assert procedure_credit_allowed(blocked) is False
    assert procedure_from_episode(blocked) is None


def test_a_failed_run_still_mints_nothing():
    assert procedure_from_episode(_episode(aborted_reason="crash")) is None


def test_a_run_with_no_tools_still_mints_nothing():
    assert procedure_from_episode(_episode(tools_used=[])) is None


def test_a_minted_procedure_is_still_born_a_candidate():
    """The maturity gate is untouched: one success never reaches `active`."""
    assert procedure_from_episode(_episode()).status == "candidate"


# ---------------------------------------------------------------------------
# Bounds — both accumulating fields are written to disk and into prompts
# ---------------------------------------------------------------------------

def test_lessons_stop_growing_and_keep_the_recent_ones():
    """`tools:file_read` pools every credited run (MIR-050); unbounded is not an option."""
    procedure = procedure_from_episode(_episode(question="run 0"))
    for i in range(1, 30):
        procedure = procedure.with_episode(_episode(question=f"run {i}"))
    assert len(procedure.lessons) <= 12
    assert any("run 29" in x for x in procedure.lessons), "the newest was dropped"
    assert not any("run 0" in x for x in procedure.lessons), "the oldest was kept"


def test_trigger_tags_are_capped():
    """A record carrying every token matches every query — that is not retrieval."""
    procedure = procedure_from_episode(
        _episode(question=" ".join(f"word{i}" for i in range(200)))
    )
    assert len(procedure.trigger_tags) <= 40


def test_the_evidence_step_is_bounded_like_the_lesson():
    procedure = procedure_from_episode(
        _episode(source_labels=[f"file:f{i}.py" for i in range(40)])
    )
    step = next(s for s in procedure.steps if s.startswith("Evidence gathered:"))
    assert "+37 more" in step
    assert len(step) < 200


def test_a_new_field_is_not_dropped_when_an_episode_is_folded_in():
    """`with_episode` uses `replace`; a field-by-field rebuild silently drops columns."""
    first = procedure_from_episode(_episode())
    merged = first.with_episode(_episode(question="another"))
    assert merged.name == first.name
    assert merged.workflow_key == first.workflow_key
    assert merged.steps == first.steps
    assert merged.trigger_tags == first.trigger_tags
    assert merged.created_at == first.created_at


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def test_lessons_survive_a_round_trip():
    procedure = procedure_from_episode(_episode())
    back = ProcedureRecord.from_dict(procedure.to_dict())
    assert back.lessons == procedure.lessons


def test_an_oversized_record_is_capped_when_it_is_loaded():
    """A record written by an earlier version must not keep spending prompt budget.

    Caps applied only on write leave an oversized row on disk paying its cost on
    every run — the record is injected into planner prompts — until some future
    credited episode happens to rewrite it.
    """
    row = procedure_from_episode(_episode()).to_dict()
    row["lessons"] = [f"lesson {i}" for i in range(50)]
    row["trigger_tags"] = [f"tag{i}" for i in range(200)]
    loaded = ProcedureRecord.from_dict(row)
    assert len(loaded.lessons) <= 12
    assert len(loaded.trigger_tags) <= 40
    assert "lesson 49" in loaded.lessons, "loading dropped the newest lessons"


def test_loading_deduplicates_tags():
    row = procedure_from_episode(_episode()).to_dict()
    row["trigger_tags"] = ["same", "same", "other"]
    assert ProcedureRecord.from_dict(row).trigger_tags == ("same", "other")


def test_an_uncredited_fold_in_repairs_an_oversized_record():
    """A debit-only update must shrink an old record, not carry it forward."""
    oversized = ProcedureRecord.from_dict({
        **procedure_from_episode(_episode()).to_dict(),
        "lessons": [f"lesson {i}" for i in range(50)],
    })
    folded = oversized.with_episode(
        _episode(
            defect_signals=["obligation_silently_missing"],
            declared_completion="achieved",
        )
    )
    assert len(folded.lessons) <= 12


def test_a_legacy_record_without_lessons_loads_as_empty():
    row = procedure_from_episode(_episode()).to_dict()
    row.pop("lessons")
    assert ProcedureRecord.from_dict(row).lessons == ()
