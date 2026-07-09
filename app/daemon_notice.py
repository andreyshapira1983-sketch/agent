"""REPL startup notices from background daemon state."""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

from cli.parsers import _truncate_text
from core.approval_inbox import ApprovalInbox


def _print_daemon_inbox_notice(workspace: Path) -> None:
    """Check the approval inbox and print a wake-up notice if items are pending.

    Called once at REPL startup so the user immediately sees anything the
    background daemon (agent_tick.py) found while they were away.
    """
    try:
        inbox = ApprovalInbox(path=workspace / "data" / "approval_inbox.jsonl")
        pending = inbox.pending()
        if not pending:
            return

        total = len(pending)
        by_operation = Counter(item.operation for item in pending)

        print(f"\n{'='*60}", file=sys.stderr)
        print(
            f"  DAEMON NOTICE: {total} pending approval item(s)",
            file=sys.stderr,
        )
        print(f"{'='*60}", file=sys.stderr)

        # Always show the breakdown by operation type — compact, one line each.
        for operation, count in by_operation.most_common():
            print(f"  - {operation}: {count}", file=sys.stderr)

        if total <= 5:
            # Small backlog: show each item on a single trimmed line.
            print("", file=sys.stderr)
            for item in pending:
                first_line = item.summary.splitlines()[0] if item.summary else ""
                summary = _truncate_text(first_line, 90)
                print(f"  [{item.id[:16]}] {summary}", file=sys.stderr)

        print(
            "\n  Use :approval-list to review  |  :approval-list all for full detail"
            f"\n{'='*60}\n",
            file=sys.stderr,
        )
    except Exception:  # noqa: BLE001
        pass   # inbox missing or unreadable — silent; don't block startup
