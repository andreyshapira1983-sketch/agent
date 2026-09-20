"""A proposal built on an older file costs no permission.

Live drain 2026-09-20 09:28–09:29: the operator had edited
`core/step_sanitizer.py`, so yesterday's split proposal no longer matched the
file. The lane refused it correctly at its base-state gate — but the grant unit
was spent BEFORE the attempt, so three cycles burned the grant to zero without
running a single test. The same staleness rule now runs before the spend.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core.approval_inbox import DEFAULT_APPROVAL_INBOX_PATH, ApprovalInbox
from core.rule_approved_apply import drain_rule_approved_proposals
from core.self_apply_bridge import SELF_APPLY_OPERATION, build_self_apply_payload


@pytest.fixture
def sandbox(tmp_path: Path) -> Path:
    (tmp_path / "data").mkdir()
    (tmp_path / "logs").mkdir()
    (tmp_path / "knowledge" / "doctrine" / "future").mkdir(parents=True)
    return tmp_path


def _granted_inbox(sandbox: Path) -> ApprovalInbox:
    inbox = ApprovalInbox(path=sandbox / DEFAULT_APPROVAL_INBOX_PATH)
    grant = inbox.add(
        operation="autonomous_runtime.standing_grant",
        summary="standing effects grant: 3 runs/day",
        risk="irreversible",
        payload={"max_runs_per_day": 3},
        expires_at=(datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
    )
    inbox.approve(grant.id)
    return inbox


def _propose(inbox: ApprovalInbox, sandbox: Path, rel: str, content: str) -> None:
    inbox.add(
        operation=SELF_APPLY_OPERATION,
        summary=f"self-apply: {rel}",
        risk="reversible",
        payload=build_self_apply_payload(
            files=[{"path": rel, "content": content}],
            reason="черновик", origin="burn_in", workspace=sandbox,
        ),
    )


def test_a_stale_proposal_is_refused_before_the_grant_is_touched(sandbox: Path) -> None:
    rel = "knowledge/doctrine/future/note.md"
    inbox = _granted_inbox(sandbox)
    _propose(inbox, sandbox, rel, "# заявка построена, когда файла не было\n")
    # Пока заявка ждала решения, файл появился — чужая работа новее заявки.
    (sandbox / rel).write_text("# новее заявки\n", encoding="utf-8")

    events: list[tuple[str, dict]] = []
    out = drain_rule_approved_proposals(sandbox, dry_run=False,
                                        log=lambda e, p: events.append((e, p)))
    assert out["refused"] == 1 and out["attempted"] == 0 and out["applied"] == 0
    stale = [p for e, p in events if e == "rule_approval_refused_stale"]
    assert len(stale) == 1 and stale[0]["stale_files"] == [rel]
    assert not [e for e, _ in events if e in ("sandbox_cap_exhausted", "standing_grant_exhausted")]
    assert (sandbox / rel).read_text(encoding="utf-8") == "# новее заявки\n", "чужая работа цела"


def test_a_fresh_proposal_still_goes_through(sandbox: Path) -> None:
    rel = "knowledge/doctrine/future/fresh.md"
    inbox = _granted_inbox(sandbox)
    _propose(inbox, sandbox, rel, "# новая заметка\n")
    events: list[tuple[str, dict]] = []
    out = drain_rule_approved_proposals(sandbox, dry_run=False,
                                        log=lambda e, p: events.append((e, p)))
    assert out["attempted"] == 1, [e for e, _ in events]
    assert not [e for e, _ in events if e == "rule_approval_refused_stale"]
