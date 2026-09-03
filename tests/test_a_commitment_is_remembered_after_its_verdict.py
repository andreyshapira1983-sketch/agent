"""Denied ≠ never seen (audit L10, block 3, 2026-09-03).

Measured on the live inbox (161 items, 48 dedup keys): the doctrine draft for
MIGRATION_PATH.md was re-filed three times after denial; the split step
`self_split:core/model_router.py:4bf4e54fce97` — same digest, same bytes —
twice. Dedup consulted pending rows only, so every denial was a fresh start,
and `approval_outcomes.jsonl` carried no key to match a verdict back to the
proposal it judged.

The boundary, kept from MIR-072: a permission QUESTION (no files) may be asked
again after a verdict. Only a proposal that carries files is remembered.
"""
from __future__ import annotations

from dataclasses import replace

from core.approval_inbox import ApprovalInbox
from core.state_integrity import read_state_jsonl

_KEY = "self_split:core/model_router.py:4bf4e54fce97"
_PATH = "core/model_router.py"


def _inbox(tmp_path) -> ApprovalInbox:
    (tmp_path / "data").mkdir(exist_ok=True)
    return ApprovalInbox(path=tmp_path / "data" / "approval_inbox.jsonl")


def _file(inbox: ApprovalInbox, *, content: str = "A", path: str = _PATH,
          key: str = _KEY, summary: str = "split step"):
    return inbox.add(
        operation="self_apply_lane.run", summary=summary, risk="reversible",
        reasons=("evidence",),
        payload={"files": [{"path": path, "content": content}]},
        dedup_key=key,
    )


def test_the_same_content_after_denial_is_refused(tmp_path) -> None:
    """The live specimen: same digest, same bytes, filed again after denial."""
    inbox = _inbox(tmp_path)
    first = _file(inbox)
    inbox.deny(first.id, reason="byte-identical resubmission", actor="operator")

    again = _file(inbox)

    assert again.id == first.id and again.status == "denied", (
        "the inbox forgot the denial and filed the same bytes again"
    )
    assert inbox.pending() == []


def test_a_revision_is_admitted_and_carries_the_denial(tmp_path) -> None:
    """Control: a DIFFERENT proposal under the same key lands — marked as a
    refile that answers the denial, so the reviewer sees the history."""
    inbox = _inbox(tmp_path)
    first = _file(inbox)
    inbox.deny(first.id, reason="the split leaves a cycle", actor="operator")

    second = _file(inbox, content="B")

    assert second.id != first.id and second.status == "pending"
    assert second.payload["revises"] == first.id
    assert second.payload["prior_denial_reason"] == "the split leaves a cycle"
    assert any(r.startswith("revises denied ") for r in second.reasons), second.reasons


def test_a_denial_ages_out(tmp_path) -> None:
    """The memory is a window, not a ban: an old denial no longer answers."""
    inbox = _inbox(tmp_path)
    first = _file(inbox)
    inbox.deny(first.id, reason="not now", actor="operator")
    inbox.items = [
        replace(it, updated_at="2020-01-01T00:00:00+00:00") if it.id == first.id else it
        for it in inbox.items
    ]
    inbox._save()  # блок 8: ящик читает файл перед каждой записью — старим на диске

    again = _file(inbox)

    assert again.id != first.id and again.status == "pending"


def test_a_question_without_files_may_be_asked_again(tmp_path) -> None:
    """MIR-072's contract survives: a permission question is not an artifact."""
    inbox = _inbox(tmp_path)
    first = inbox.add(operation="autonomous_runtime.allow_effects",
                      summary="may I use effects for 'project health'?",
                      dedup_key="allow:project-health")
    inbox.deny(first.id, reason="this run is not needed", actor="operator")

    again = inbox.add(operation="autonomous_runtime.allow_effects",
                      summary="may I use effects for 'project health'?",
                      dedup_key="allow:project-health")

    assert again.id != first.id and again.status == "pending"


def test_an_executed_proposal_does_not_block_a_refile(tmp_path) -> None:
    """Only denial is remembered: approved-and-executed work under the same
    key is history, not a refusal."""
    inbox = _inbox(tmp_path)
    first = _file(inbox)
    inbox.approve(first.id, actor="operator")
    inbox.mark_executed(first.id)

    again = _file(inbox)

    assert again.id != first.id and again.status == "pending"


def test_the_reason_lives_on_the_item_and_survives_reload(tmp_path) -> None:
    inbox = _inbox(tmp_path)
    first = _file(inbox)
    inbox.deny(first.id, reason="restates the skeleton", actor="operator")

    reloaded = ApprovalInbox(path=tmp_path / "data" / "approval_inbox.jsonl")

    assert reloaded.get(first.id).decision_reason == "restates the skeleton"
    assert reloaded.recently_denied(_KEY).id == first.id


def test_the_outcome_names_its_key_and_files(tmp_path) -> None:
    """The verdict journal can now be matched back to WHAT was judged."""
    inbox = _inbox(tmp_path)
    first = _file(inbox)
    inbox.deny(first.id, reason="not this", actor="operator")

    rows = read_state_jsonl(tmp_path / "data" / "approval_outcomes.jsonl")
    last = rows[-1].get("payload") if isinstance(rows[-1].get("payload"), dict) else rows[-1]

    assert last["verdict"] == "denied"
    assert last["dedup_key"] == _KEY
    assert last["targets"] == [_PATH]


def test_waiting_and_denied_files_are_readable(tmp_path) -> None:
    """The commitments view: which files wait for a human, which were denied."""
    inbox = _inbox(tmp_path)
    _file(inbox, path="core/a.py", key="k:a")
    denied = _file(inbox, path="core/b.py", key="k:b")
    inbox.deny(denied.id, reason="no", actor="operator")

    assert inbox.pending_targets(operation="self_apply_lane.run") == frozenset({"core/a.py"})
    assert inbox.recently_denied_targets(operation="self_apply_lane.run") == frozenset({"core/b.py"})

    inbox.add(operation="self_apply_lane.run", summary="names no files")
    assert inbox.pending_targets(operation="self_apply_lane.run") is None, (
        "an item without a file list must read as «unknown», not as «nothing»"
    )


def test_the_doctrine_hand_reads_the_denial_and_does_not_refile_the_same_draft(
    tmp_path,
) -> None:
    """The live path: the campaign's document hand after a human said no."""
    import core.campaign_io as mod

    target = "docs/CODE_NOTES.md"
    goal = f"draft {target}"
    inbox = _inbox(tmp_path)
    draft = "# STATUS: DRAFT / TARGET\n\nthe same draft\n"

    class _Llm:
        def __init__(self) -> None:
            self.prompts: list[str] = []

        def complete(self, **kw) -> str:
            self.prompts.append(str(kw.get("user", "")))
            return draft

    class _Log:
        def __init__(self) -> None:
            self.events: list[tuple[str, dict]] = []

        def log(self, event, payload=None, **extra) -> None:
            self.events.append((event, dict(payload or {})))

    class _Agent:
        def __init__(self) -> None:
            self.llm = _Llm()
            self.log = _Log()

    agent = _Agent()
    first = mod._propose_doctrine_draft(
        agent=agent, workspace=tmp_path, goal=goal, approval_inbox=inbox,
    )
    assert first and first.startswith("doc_draft_proposed:"), first
    inbox.deny(first.split(":", 1)[1], reason="restates the skeleton", actor="operator")

    second = mod._propose_doctrine_draft(
        agent=agent, workspace=tmp_path, goal=goal, approval_inbox=inbox,
    )

    assert "restates the skeleton" in agent.llm.prompts[-1], (
        "the denial reason never reached the generator — a «revision» would "
        "be a new roll of the dice"
    )
    assert second is None, f"the same draft after denial was called a proposal: {second}"
    superseded = [p for e, p in agent.log.events if e == "campaign_doc_draft_superseded"]
    assert superseded and str(superseded[-1].get("collision", "")).startswith(
        "refused_repeat_of_denied:"
    ), agent.log.events
