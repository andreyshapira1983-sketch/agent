"""Classify legacy episodes whose completion verdict is recoverable.

Most stored episodes that lack a completion verdict are *unclassifiable*, not
merely unclassified: the cycle settles the axis with
:func:`core.smart_memory.assemble_completion_state`, whose inputs are the
synthesizer's declaration and ``aborted_reason`` — neither of which was ever
persisted. ``scripts/completion_legacy_report.py`` says exactly this, and it is
right about the records it was written to describe.

Two writers are the exception, and this module is only about them. Neither has
ever consulted the assembly table; each settles the axis from evidence it does
persist, through the shared table in :mod:`core.writer_completion`:

* the self-build writer (:mod:`core.self_build_memory`) maps ``outcome``;
* the self-repair writer (:mod:`core.self_repair`) writes a lesson only once the
  repair verified clean, so its verdict is constant.

Both settle the axis at banking today. Rows they banked *before* that fix carry
the same durable evidence the writer would use now, so replaying the writer's
own rule recovers the verdict rather than guessing it.

**Provenance is proved, not assumed.** The store also holds cycle-banked rows
whose ``outcome`` says nothing about whether their goal was reached; mapping
those would re-open the inference MIR-057 exists to forbid. A category marker is
not provenance — the tag ``self-build`` says "this record is about self-build",
which a cycle-banked, hand-written or future-writer record could equally carry.
So each signature below demands several facts that only one writer emits
*together*:

* self-build — ``question`` is one of the four ``kind`` values the writer is ever
  called with, and both ``self-build`` and that same ``kind`` appear in the tags,
  because the writer always emits ``["self-build", "lesson", kind, ...]``
  (`core/self_build_memory.py:103,109`);
* self-repair — ``goal == "repair"``, both regression tags present, *and*
  ``outcome == "success"``. The constant verdict rests on the invariant that
  `_write_repair_lesson` runs only when the repair re-verified clean
  (`core/self_repair.py:485`); requiring the stored outcome means a corrupted or
  foreign row claiming those tags cannot collect ``achieved`` for free.

Everything else fails closed: malformed tags, an unknown ``kind``, an
unrecognised ``outcome``, a partial signature, or a ``completion_state`` key that
is present at all. An unrecognised legacy record stays unclassified, which is the
correct outcome for it.

The axis is still settled once and never recomputed (MIR-057) — this module
settles it for records banked before their writer learned to.

It lives under ``scripts/`` rather than ``core/`` on purpose: nothing in the
running agent decides this, only the migration and the diagnostic report do. A
decider in ``core/`` that no production module imports is a mechanism that cannot
run, which INV-2 rejects (self-audit-lessons #6).
"""

from __future__ import annotations

from typing import Any

from core.writer_completion import completion_from_outcome

# Every `kind` the self-build writer is called with in production:
# `agent_tick.py:697`, `cli/commands_self_apply.py:76`,
# `cli/commands_self_build.py:121`, `cli/commands_self_task.py:69,147`,
# `core/autonomous_runtime.py:1327`. Read from the call sites, not from whatever
# the local store happens to contain: a value absent here must fail closed, and a
# future trigger has to be added here deliberately.
_SELF_BUILD_QUESTIONS = frozenset(
    {
        "self-build-produce",
        "self-apply-run",
        "self-task-produce",
        "self-task-build",
    }
)

_SELF_BUILD_TAG = "self-build"

# A self-repair lesson exists only when `report.status == "repaired"`, i.e. the
# fix was applied and re-verified; `core/self_repair.py` hard-codes the same
# constant for new rows.
_REPAIR_VERDICT = "achieved"
_REPAIR_OUTCOME = "success"
_REPAIR_TAGS = frozenset({"bug-fix", "regression-guard"})


def _tags(payload: dict[str, Any]) -> frozenset[str]:
    """Tags as the writer stored them, or nothing at all.

    Fails closed on any other shape. A bare ``str`` is the dangerous case:
    ``set("self-build")`` is a set of *characters*, so a membership test against
    it is meaningless rather than merely wrong. A migration that cannot read a
    row's schema must not decide that row's verdict.
    """
    raw = payload.get("tags")
    if not isinstance(raw, (list, tuple)):
        return frozenset()
    if not all(isinstance(tag, str) for tag in raw):
        return frozenset()
    return frozenset(raw)


def _is_self_build_row(payload: dict[str, Any]) -> bool:
    """True only when several facts co-occur that one writer emits together."""
    question = payload.get("question")
    if question not in _SELF_BUILD_QUESTIONS:
        return False
    tags = _tags(payload)
    return _SELF_BUILD_TAG in tags and question in tags


def _is_self_repair_row(payload: dict[str, Any]) -> bool:
    """True only for a repair lesson that also stored the success it claims."""
    return (
        payload.get("goal") == "repair"
        and payload.get("outcome") == _REPAIR_OUTCOME
        and _REPAIR_TAGS <= _tags(payload)
    )


def writer_backfill_verdict(payload: dict[str, Any]) -> str | None:
    """Return the verdict a known writer would settle for ``payload``.

    ``payload`` is a stored episodic row as it sits on disk. Returns ``None``
    when the row carries a ``completion_state`` key at all, when no writer
    signature is proved, or when a proved signature's own evidence is missing or
    unrecognised — in every one of those cases the record is left exactly as it
    is.

    The key test is presence, not truthiness. ``EpisodeRecord.to_dict`` omits the
    key when the verdict is unset (`core/smart_memory.py:246`), so an absent key
    is the one honest encoding of "this row predates the axis". Anything else —
    an explicit ``null``, an empty string, a value this module does not recognise
    — was put there by some writer on purpose, and overwriting it would be a
    second decision about an axis that is settled once (MIR-057).
    """
    if "completion_state" in payload:
        return None
    if _is_self_repair_row(payload):
        return _REPAIR_VERDICT
    if _is_self_build_row(payload):
        return completion_from_outcome(payload.get("outcome"))
    return None
