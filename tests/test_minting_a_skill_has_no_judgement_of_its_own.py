"""Third specimen, twice narrowed: the mint stamps the defect — nobody reads it.

NOT a breach (operator ruling 2026-08-17): the admission law behaved as
written — DISQUALIFYING_DEFECT_SIGNALS holds only falsehood-proving
signals, and reasoning_action_mismatch is excluded with recorded reasoning
(MIR-057). What is proven is an OVERLOAD: one predicate
(`procedure_credit_allowed`) serves answer-steering, crediting, and
MINTING, and only the first two were ever argued.

Falsification trail, kept on purpose:
  v1 (operator): «записал дефект — и всё равно материал для навыка» — TRUE
     for eligibility and credit, intentional by law.
  v2 (engineer): «чеканка молчалива — запись нигде не несёт дефект
     источника» — FALSIFIED, and by the exact error class this repo pins on
     its own verifier: absence was certified from a TRUNCATED excerpt
     (lessons read at 200 chars). The full live record proc_88e7ebb7
     carries «observed: reasoning_action_mismatch»; lesson_from_episode
     stamps it at mint time. Pinned green below so it cannot regress.
  v3 (narrowed, open, not pinned by a test): the stamp is PROSE inside `lessons` with no
     reader — machine judgement (status, confidence) treats a
     defect-stamped skill identically to a clean one at birth.
"""
from __future__ import annotations

import json
from dataclasses import asdict

from core.smart_memory import EpisodeRecord, procedure_from_episode


def _episode(defects: tuple[str, ...] = ()) -> EpisodeRecord:
    return EpisodeRecord(
        goal="answer",
        question="как ты будешь строить неизвестную модель",
        outcome="success",
        summary="answered with receipts",
        tools_used=("web_search", "file_read"),
        source_labels=("web:example",),
        verified_chunks=3,
        completion_state="achieved",
        defect_signals=defects or None,
        usage_eligible=True,
        run_id="run_test",
    )


def test_the_mint_stamps_the_process_defect() -> None:
    """Already true and worth guarding: a skill minted from a
    process-defect run names that defect on its own record (live twin:
    proc_88e7ebb7, «observed: reasoning_action_mismatch» in lessons)."""
    proc = procedure_from_episode(_episode(("reasoning_action_mismatch",)))
    assert proc is not None
    rendered = json.dumps(asdict(proc), ensure_ascii=False, default=str)
    assert "reasoning_action_mismatch" in rendered


def test_a_clean_run_mints_freely() -> None:
    assert procedure_from_episode(_episode()) is not None


def test_the_falsehood_list_still_stops_minting() -> None:
    """The argued half of the unification, untouched: falsehood-proving
    signals refuse minting outright (2026-08-10)."""
    assert procedure_from_episode(_episode(("content_refuted",))) is None
