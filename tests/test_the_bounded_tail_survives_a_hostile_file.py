"""The budget tail read under the field's named log-tailing failures.

WHY THIS EXISTS. Not a new defect report — this is the AUDIT of MIR-125's own
closure (`docs/audit/CLOSURE_AUDIT_2026-08-22.md`). A bounded tail read over an
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


def test_a_truncated_last_line_does_not_hide_earlier_records(
    tmp_path: Path,
) -> None:
    """The crashed-writer shape: the final line is half-written. The partial
    record is unreadable and is lost — but everything before it must still be
    counted, or one crash would silently raise the budget ceiling."""
    ledger = _ledger(tmp_path)
    _fill(ledger, old=100, recent=30)
    path = tmp_path / "budget.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        fh.write('{"payload": {"counter": "llm_calls", "amo')  # died mid-write

    assert _used(ledger) == 30, (
        "a half-written final line cost the count more than itself"
    )


def test_a_file_that_shrinks_between_measure_and_read_is_still_counted(
    tmp_path: Path,
) -> None:
    """The rotation/truncation shape. Our production ledger is append-only, so
    this cannot arise from our own writers — but a reader that trusts a stale
    size is wrong in principle, and an external tool could truncate the file.
    """
    ledger = _ledger(tmp_path)
    _fill(ledger, old=2000, recent=25)
    path = tmp_path / "budget.jsonl"

    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join(lines[-40:]) + "\n", encoding="utf-8")  # rotated

    used = _used(ledger)
    assert used == 25, (
        f"after a truncation the gate counted {used} of the 25 surviving "
        "in-window records"
    )


def test_a_stale_size_can_only_over_read_never_under_count(
    tmp_path: Path, monkeypatch,
) -> None:
    """The concurrent-writer shape, stated as the property that matters.

    The reader measures the file, then seeks `size - span`. Between those two
    moments another PROCESS may append (our own writers serialise on the file
    lock, so this is cross-process by construction). The invariant is
    one-sided: a stale size makes the seek land EARLIER than intended, so the
    read covers more, never less — and a file that shrank makes the seek land
    past the end, which yields nothing and falls back to the full read. Under
    no ordering can it undercount, which is the only outcome that would cost
    money.

    Simulated by reporting a size smaller than the truth, the worst case for
    a reader that trusted it.
    """
    ledger = _ledger(tmp_path)
    _fill(ledger, old=1500, recent=12)
    path = tmp_path / "budget.jsonl"
    real_stat = Path.stat

    def half_size(self, *a, **kw):
        st = real_stat(self, *a, **kw)
        if self == path:
            class _S:
                st_size = st.st_size // 2
            return _S()
        return st

    monkeypatch.setattr(Path, "stat", half_size)
    assert _used(ledger) == 12, "a stale size undercounted the window"


def test_a_file_of_only_recent_records_falls_back_to_the_full_read(
    tmp_path: Path,
) -> None:
    """The coverage proof can never be satisfied when EVERY record is inside
    the window — there is no older record to prove the read reached past the
    cutoff. It must terminate on the whole file, not spin."""
    ledger = _ledger(tmp_path)
    _fill(ledger, old=0, recent=800)

    assert _used(ledger) == 800


def test_an_empty_and_a_headerless_file_are_safe(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    path = tmp_path / "budget.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")
    assert _used(ledger) == 0

    path.write_text("not json at all\nneither is this\n", encoding="utf-8")
    assert _used(ledger) == 0
