"""Tests for the operator-facing ``--status`` mode of the daemon (`_print_status`).

`_print_status` is the human's window into the daemon: it answers "is the daemon
alive?" and "what is waiting for my approval?" WITHOUT creating an agent or
spending any budget. Coverage flagged the whole function as never-run, so its
branches (no heartbeat / alive / stale, empty / pending inbox) were green only
because nobody had ever exercised the operator command. These tests bind to the
real ApprovalInbox and heartbeat files and read the actual stderr output.
"""
from __future__ import annotations

from agent_tick import (
    HEARTBEAT_PATH,
    _print_status,
)


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
