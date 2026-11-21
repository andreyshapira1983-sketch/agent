"""The budget tail read under the field's named log-tailing failures.

WHY THIS EXISTS. Not a new defect report — this is the AUDIT of MIR-125's own
closure (`CLOSURE_AUDIT_2026-08-22.md in git history`). A bounded tail read over an
append-only file is a well-worn shape, and the field names how it breaks:
rotation or truncation between measuring the file and reading it; a partial
last line from a writer that died mid-append; a concurrent writer moving the
end underneath the reader; and the reader that jumps to the new file and drops
the unread tail of the old one.

The stake is not latency. `reserve()` decides whether the agent may spend
money, and an under-read UNDERCOUNTS usage — so every one of these failures,
if it landed, would silently raise the ceiling the operator set.

WHAT THE PROBES FOUND. The ledger is APPEND-ONLY in production: no site
anywhere rewrites `data/budget_ledger.jsonl` (verified by sweep), so the
rotation family cannot arise from our own code. The remaining shapes are
simulated here directly against the reader.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.budget_ledger import BudgetLedger, BudgetWindow

_NOW = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)


def _ledger(tmp_path: Path, limit: int = 100_000) -> BudgetLedger:
    return BudgetLedger(
        path=tmp_path / "budget.jsonl",
        windows=[BudgetWindow(name="hour", seconds=3600,
                              limits={"llm_calls": limit})],
    )


def _fill(ledger: BudgetLedger, *, old: int, recent: int) -> None:
    for i in range(old):
        ledger.record("llm_calls", amount=1, reason="old",
                      now=_NOW - timedelta(days=3, minutes=i))
    for i in range(recent):
        ledger.record("llm_calls", amount=1, reason="recent",
                      now=_NOW - timedelta(seconds=i + 1))


def _used(ledger: BudgetLedger) -> int:
    """`used` is only reported on refusal, so ask for more than the limit."""
    return ledger.check("llm_calls", amount=100_000, now=_NOW).used


def test_a_file_of_only_recent_records_falls_back_to_the_full_read(
    tmp_path: Path,
) -> None:
    """The coverage proof can never be satisfied when EVERY record is inside
    the window — there is no older record to prove the read reached past the
    cutoff. It must terminate on the whole file, not spin."""
    ledger = _ledger(tmp_path)
    _fill(ledger, old=0, recent=800)

    assert _used(ledger) == 800
