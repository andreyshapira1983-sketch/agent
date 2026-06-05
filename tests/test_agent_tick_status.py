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
