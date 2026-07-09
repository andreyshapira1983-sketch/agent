"""Tests for the operator-facing ``--status`` mode of the daemon (`_print_status`).

`_print_status` is the human's window into the daemon: it answers "is the daemon
alive?" and "what is waiting for my approval?" WITHOUT creating an agent or
spending any budget. Coverage flagged the whole function as never-run, so its
branches (no heartbeat / alive / stale, empty / pending inbox) were green only
because nobody had ever exercised the operator command. These tests bind to the
real ApprovalInbox and heartbeat files and read the actual stderr output.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent_tick import (
    _print_status,
    _write_heartbeat,
    APPROVAL_INBOX_PATH,
    EXPECTED_TICK_INTERVAL_SECONDS,
    HEARTBEAT_PATH,
    STALENESS_FACTOR,
)
from core.approval_inbox import ApprovalInbox


def _write_raw_heartbeat(workspace: Path, record: dict) -> None:
    hb_path = workspace / HEARTBEAT_PATH
    hb_path.parent.mkdir(parents=True, exist_ok=True)
    hb_path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")


def test_status_no_heartbeat_no_inbox_is_clean(workspace, capsys):
    # Never-ticked daemon, empty inbox: honest "no heartbeat" + "no pending".
    rc = _print_status(workspace)
    err = capsys.readouterr().err
    assert rc == 0
    assert "no heartbeat recorded yet" in err
    assert "no pending items" in err
    # No Mode: line when there is no heartbeat to describe.
    assert "Mode:" not in err


def test_status_fresh_heartbeat_reports_alive(workspace, capsys):
    _write_heartbeat(
        workspace,
        {
            "event": "tick_complete",
            "mode": "dry_run",
            "effects": "disabled",
            "processed_effects": 0,
            "dry_run_streak": 3,
        },
    )
    rc = _print_status(workspace)
    err = capsys.readouterr().err
    assert rc == 0
    assert "Daemon: alive" in err
    assert "event=tick_complete" in err
    # Dry-run visibility is surfaced honestly.
    assert "Mode: dry_run" in err
    assert "effects=disabled" in err
    assert "dry_run_streak=3" in err


def test_status_old_heartbeat_reports_stale(workspace, capsys):
    overdue = datetime.now(timezone.utc) - timedelta(
        seconds=EXPECTED_TICK_INTERVAL_SECONDS * STALENESS_FACTOR + 120
    )
    _write_raw_heartbeat(
        workspace,
        {"ts": overdue.isoformat(), "event": "tick_complete", "mode": "dry_run"},
    )
    rc = _print_status(workspace)
    err = capsys.readouterr().err
    assert rc == 0
    assert "Daemon: STALE" in err
    assert "may not be running" in err


def test_status_lists_pending_inbox_items(workspace, capsys):
    inbox = ApprovalInbox(path=workspace / APPROVAL_INBOX_PATH)
    item = inbox.add(
        operation="file_write",
        summary="apply repair to buggy.py",
        risk="reversible",
    )
    rc = _print_status(workspace)
    err = capsys.readouterr().err
    assert rc == 0
    assert "1 pending item(s)" in err
    assert item.id in err
    assert "file_write" in err
    assert "apply repair to buggy.py" in err


def test_status_tolerates_corrupt_heartbeat_file(workspace, capsys):
    # A garbled heartbeat must not crash the operator command; it degrades to
    # the "no heartbeat" path rather than raising.
    hb_path = workspace / HEARTBEAT_PATH
    hb_path.parent.mkdir(parents=True, exist_ok=True)
    hb_path.write_text("{not json", encoding="utf-8")
    rc = _print_status(workspace)
    err = capsys.readouterr().err
    assert rc == 0
    assert "no heartbeat recorded yet" in err


# ── TD-027: self-build producer visibility in --status (read-only) ────────────

from agent_tick import (  # noqa: E402
    SELF_BUILD_STATE_PATH,
    _self_build_status_lines,
)
from core.self_apply_bridge import SELF_APPLY_OPERATION  # noqa: E402


def _write_producer_state_raw(workspace: Path, text: str) -> None:
    path = workspace / SELF_BUILD_STATE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_status_self_build_never_ran(workspace, capsys):
    # No heartbeat and no producer state: honest "never ran" / "missing".
    rc = _print_status(workspace)
    err = capsys.readouterr().err
    assert rc == 0
    assert "last self-build status: never ran" in err
    assert "state file: missing" in err
    assert "cooldown: ready (live)" in err
    assert "self_apply_lane.run: 0 pending, 0 approved" in err


def test_status_self_build_proposed_fields_are_labeled_historical(workspace, capsys):
    _write_raw_heartbeat(
        workspace,
        {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": "tick_complete",
            "self_build_status": "proposed",
            "self_build_approval_id": "ain_abc123",
            "self_build_next_human_action": "run :self-apply-run ain_abc123",
        },
    )
    rc = _print_status(workspace)
    err = capsys.readouterr().err
    assert rc == 0
    # Heartbeat status must be presented as historical, never a live gate.
    assert "last self-build status: proposed" in err
    assert "historical" in err
    assert "ain_abc123" in err
    assert "run :self-apply-run ain_abc123" in err


def test_status_self_build_cooldown_is_live_when_recently_proposed(workspace, capsys):
    now = datetime.now(timezone.utc).isoformat()
    _write_producer_state_raw(workspace, f'{{"last_proposed_at": "{now}"}}')
    rc = _print_status(workspace)
    err = capsys.readouterr().err
    assert rc == 0
    assert "cooldown:" in err
    assert "remaining (live)" in err
    assert "state file: ok" in err


def test_status_self_build_cooldown_ready_when_proposal_is_old(workspace, capsys):
    old = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
    _write_producer_state_raw(workspace, f'{{"last_proposed_at": "{old}"}}')
    rc = _print_status(workspace)
    err = capsys.readouterr().err
    assert rc == 0
    assert "cooldown: ready (live)" in err


def test_status_self_build_last_status_blocked_reason_is_historical(workspace, capsys):
    # A block reason recorded by the last tick is shown but must NOT be dressed
    # up as a live gate — only cooldown is live.
    _write_raw_heartbeat(
        workspace,
        {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": "tick_complete",
            "self_build_status": "budget_kill_switch",
        },
    )
    rc = _print_status(workspace)
    err = capsys.readouterr().err
    assert rc == 0
    assert "last self-build status: budget_kill_switch" in err
    assert "not a live gate" in err


def test_status_self_build_counts_pending_and_approved(workspace, capsys):
    inbox = ApprovalInbox(path=workspace / APPROVAL_INBOX_PATH)
    inbox.add(operation=SELF_APPLY_OPERATION, summary="pending one")
    approved = inbox.add(operation=SELF_APPLY_OPERATION, summary="approved one")
    inbox.approve(approved.id)
    inbox.add(operation="file_write", summary="unrelated")  # must not be counted
    rc = _print_status(workspace)
    err = capsys.readouterr().err
    assert rc == 0
    assert "self_apply_lane.run: 1 pending, 1 approved ready for :self-apply-run" in err


def test_status_self_build_tolerates_corrupt_state_file(workspace, capsys):
    _write_producer_state_raw(workspace, "{not json")
    rc = _print_status(workspace)
    err = capsys.readouterr().err
    assert rc == 0
    # Corrupt JSON reads as empty state → missing, and never crashes.
    assert "state file: missing" in err
    assert "cooldown: ready (live)" in err


def test_status_self_build_tolerates_bad_timestamp(workspace, capsys):
    _write_producer_state_raw(workspace, '{"last_proposed_at": "not-a-time"}')
    rc = _print_status(workspace)
    err = capsys.readouterr().err
    assert rc == 0
    assert "state file: bad-timestamp" in err
    # Unparseable timestamp never blocks: cooldown reads ready.
    assert "cooldown: ready (live)" in err


def test_status_self_build_tolerates_invalid_cooldown_env(workspace, capsys, monkeypatch):
    monkeypatch.setenv("AGENT_SELF_BUILD_COOLDOWN_HOURS", "not-a-number")
    now = datetime.now(timezone.utc).isoformat()
    _write_producer_state_raw(workspace, f'{{"last_proposed_at": "{now}"}}')
    rc = _print_status(workspace)
    err = capsys.readouterr().err
    assert rc == 0
    # Invalid env falls back to the 12h default → still shows live remaining.
    assert "remaining (live)" in err


def test_self_build_status_lines_pure_never_raises_on_none_inputs():
    # The pure formatter must degrade, not raise, on missing heartbeat/state.
    lines = _self_build_status_lines(
        None, None, cooldown_hours=12.0, pending_self_apply=0, approved_self_apply=0
    )
    assert any("never ran" in line for line in lines)
    assert any("state file: missing" in line for line in lines)
    assert any("cooldown: ready (live)" in line for line in lines)
