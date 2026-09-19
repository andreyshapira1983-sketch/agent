"""A gate that refused to start is a status line, not a lesson.

WHY THIS EXISTS. Measured on the live store 2026-08-22
(`docs/audit/archive/SELF_BUILD_FAILURE_ANALYSIS.md`): one unanswered approval on
2026-08-16 banked 41 episodes saying *an approval is already pending*, and the
store held 64 wait-records in all — half of the entire protected set, while
genuine lessons were evicted to make room. Every one was `usage_eligible=True`,
so retrieval was entitled to serve "the working tree is not clean" as
experience.

THE DISTINCTION. `core/self_build_memory.py` tags every self-build episode
`lesson` unconditionally. But the four pre-flight gates — kill switch, budget,
pending approval, dirty tree — run BEFORE any pipeline work (MIR-100 classes
them "may I act", never "is this true"). A run that was refused permission to
start produced no experience: there is nothing in "an approval is pending" that
a later attempt can learn from, and MIR-115 measured what the tag confers —
usage eligibility, protection from eviction, +50 retrieval boost. Status lines
must not inherit any of that.

Measured before writing: WITHOUT the tag, the ordinary admission gate already
refuses these episodes (outcome `partial`, nothing verified) — so the tag is
not merely unnecessary here, it is the entire defect.

THE SECOND HALF: MIR-090's own "missing test", named in the registry when the
duplicate collapser was built and its writer half deliberately left open:
*"a producer that hits the same gate twice does not bank a second identical
episode."* The collapser cannot reach these rows (protected, and it only runs
from a typed command — MIR-131), so the writer must not mint the duplicate in
the first place.

WHAT THIS DOES NOT CLAIM. Not that gate waits vanish from memory — the FIRST
wait is banked, searchable under its status tag, and simply ages out like any
ordinary episode. Not that genuine attempts lose anything: a veto or a proposal
still carries `lesson`, still protected, still eligible under its own rules.
And repeated GENUINE outcomes are deliberately left to the hygiene collapser,
which keeps the newest of an identical group — at write time we cannot know
that an identical veto tomorrow is noise rather than "still failing".
"""
from __future__ import annotations

from pathlib import Path

from core.self_build_memory import build_self_build_episode, record_self_build_episode
from core.smart_memory import EpisodicMemoryStore, admit_for_storage


class _Agent:
    def __init__(self, store: EpisodicMemoryStore) -> None:
        self.episodic_store = store


def _store(workspace: Path) -> EpisodicMemoryStore:
    return EpisodicMemoryStore(path=workspace / "data" / "episodic_memory.jsonl")


_WAIT = {"status": "approval_wait",
         "reason": "a pending self_apply_lane.run approval item already exists"}


def test_a_gate_wait_is_not_tagged_lesson() -> None:
    """The root. The tag is minted by the machinery that produced the content
    (MIR-115's self-granting shape), and for a refusal-to-start it grants
    eligibility, protection and retrieval priority to a status line."""
    ep = build_self_build_episode("self-build-produce", _WAIT)
    assert "lesson" not in ep.tags, (
        "a pre-flight refusal is tagged `lesson`: it becomes unevictable, "
        "usage-eligible and retrieval-boosted — 64 such rows held half the "
        "protected set on 2026-08-22"
    )


def test_a_gate_wait_is_not_usage_eligible() -> None:
    """What the tag was conferring. Without it the ordinary admission gate
    refuses these episodes on its own — measured before this test was written."""
    ep = build_self_build_episode("self-build-produce", _WAIT)
    assert admit_for_storage(ep).usage_eligible is False, (
        "'an approval is pending' may steer a later answer"
    )


def test_every_preflight_gate_is_covered() -> None:
    """All four gates from `produce_self_apply_proposal`, plus the same wait
    statuses minted by the sibling producers. Fitted to the class, not to the
    one status that happened to spam on 2026-08-16."""
    for status in ("budget_kill_switch", "budget_wait",
                   "approval_wait", "dirty_tree_wait"):
        ep = build_self_build_episode("self-build-produce",
                                      {"status": status, "reason": "x"})
        assert "lesson" not in ep.tags, f"{status} is tagged lesson"


def test_a_genuine_attempt_keeps_its_lesson_tag() -> None:
    """The boundary that must not move. Learning from real attempts — vetoes
    included — is correct and must stay; MIR-096 measured how few channels a
    lesson has, and this repair may not close another one."""
    for status in ("proposed", "critic_veto", "no_grounded_target",
                   "committed_local", "rolled_back"):
        ep = build_self_build_episode("self-build-produce",
                                      {"status": status, "reason": "x"})
        assert "lesson" in ep.tags, f"{status} lost its lesson tag"


def test_hitting_the_same_gate_twice_banks_one_episode(workspace: Path) -> None:
    """MIR-090's named missing test, verbatim: a producer that hits the same
    gate twice does not bank a second identical episode. The unattended tick
    retried one blocked gate 32 times on 2026-08-16 and banked all 32."""
    agent = _Agent(_store(workspace))

    first = record_self_build_episode(agent, kind="self-build-produce",
                                      result=_WAIT)
    second = record_self_build_episode(agent, kind="self-build-produce",
                                       result=_WAIT)

    assert first is True, "the FIRST wait is real information and is banked"
    assert second is False, "the identical repeat was banked again"
    rows = agent.episodic_store.load()
    assert len(rows) == 1, f"{len(rows)} rows for one blocked gate"


def test_a_different_gate_reason_is_new_information(workspace: Path) -> None:
    """The dedup key is content, not the status label — the consolidation
    measurement showed label-keyed dedup destroys real records (ten distinct
    answers under one question). A DIFFERENT reason is a different fact."""
    agent = _Agent(_store(workspace))

    record_self_build_episode(agent, kind="self-build-produce", result=_WAIT)
    other = record_self_build_episode(
        agent, kind="self-build-produce",
        result={"status": "dirty_tree_wait",
                "reason": "git working tree is not clean"},
    )

    assert other is True
    assert len(agent.episodic_store.load()) == 2


def test_a_repeated_genuine_veto_is_still_banked(workspace: Path) -> None:
    """Deliberate asymmetry: at write time an identical veto tomorrow might be
    'still failing', which IS information. Collapsing those is the hygiene
    collapser's call (it keeps the newest), not the writer's."""
    agent = _Agent(_store(workspace))
    veto = {"status": "critic_veto", "reason": "confidence 0.00 below threshold"}

    record_self_build_episode(agent, kind="self-build-produce", result=veto)
    again = record_self_build_episode(agent, kind="self-build-produce", result=veto)

    assert again is True
    assert len(agent.episodic_store.load()) == 2
