"""The budget gate reads a bounded tail, and proves it reached far enough.

WHY THIS EXISTS. MIR-125: `BudgetLedger.reserve()` calls `load_records()`,
which re-reads the ENTIRE append-only ledger — and MIR-116 wired a reservation
into the path of every model call. The cost gate is O(file size) per call on a
file that only grows: the protection added for a long-lived agent carries the
seed of its own slowdown, and a week-long unattended run is exactly the
scenario it was added for.

THE SAFETY PROPERTY THAT SHAPES THE FIX. Windows are time-bounded (hour, day),
so only recent records can matter — but an under-read UNDERCOUNTS usage, and
undercounting a budget means spending past the limit. So the tail read must
PROVE it reached back far enough: it expands until the oldest record it has
seen is older than the cutoff, or until it has read the whole file. Reading
too much is slow; reading too little would be wrong, and this asymmetry is
the whole design.

WHAT THIS DOES NOT CHANGE. `snapshot()` keeps the full read — it is an
operator diagnostic, not a per-call gate, and it reports totals over all time.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.budget_ledger import BudgetLedger, BudgetWindow

_NOW = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)


def _ledger(tmp_path: Path) -> BudgetLedger:
    return BudgetLedger(
        path=tmp_path / "budget.jsonl",
        windows=[BudgetWindow(name="hour", seconds=3600, limits={"llm_calls": 100})],
    )


def _fill(ledger: BudgetLedger, *, old: int, recent: int) -> None:
    for i in range(old):
        ledger.record("llm_calls", amount=1, reason="old",
                      now=_NOW - timedelta(days=3, minutes=i))
    # Seconds apart: 100 records a MINUTE apart would overflow the hour window
    # and the test would be measuring its own arithmetic, not the read.
    for i in range(recent):
        ledger.record("llm_calls", amount=1, reason="recent",
                      now=_NOW - timedelta(seconds=i + 1))


def test_the_gate_counts_every_record_inside_the_window(tmp_path: Path) -> None:
    """The safety property: never undercount. A tail read that stopped early
    would let the agent spend past its limit."""
    ledger = _ledger(tmp_path)
    _fill(ledger, old=2000, recent=40)

    # `used` is only populated on refusal (the decision contract), so the
    # count is probed at the boundary: with 40 used and a limit of 100, a
    # request for 61 must be refused and report exactly 40.
    decision = ledger.check("llm_calls", amount=61, now=_NOW)

    assert decision.allowed is False
    assert decision.used == 40, (
        f"the gate counted {decision.used} of 40 in-window records — an "
        "under-read means spending past the budget"
    )
    assert ledger.check("llm_calls", amount=60, now=_NOW).allowed is True


def test_the_gate_blocks_at_the_limit_with_a_long_history(tmp_path: Path) -> None:
    """Same property at the boundary that matters."""
    ledger = _ledger(tmp_path)
    _fill(ledger, old=2000, recent=100)

    decision = ledger.check("llm_calls", amount=1, now=_NOW)

    assert decision.allowed is False
    assert decision.used == 100


def test_the_gate_does_not_read_the_whole_file(tmp_path: Path) -> None:
    """The point of the repair: cost must not grow with total history."""
    ledger = _ledger(tmp_path)
    _fill(ledger, old=3000, recent=5)

    seen: list[int] = []
    original = BudgetLedger.load_records

    def counting(self):
        rows = original(self)
        seen.append(len(rows))
        return rows

    BudgetLedger.load_records = counting  # type: ignore[method-assign]
    try:
        ledger.check("llm_calls", amount=1, now=_NOW)
    finally:
        BudgetLedger.load_records = original  # type: ignore[method-assign]

    assert seen == [], (
        "the per-call gate still re-reads the entire ledger — O(file size) on "
        "a file that only grows"
    )


def test_reserve_writes_and_still_counts_correctly(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    _fill(ledger, old=500, recent=3)

    decision = ledger.reserve("llm_calls", amount=1, reason="x", now=_NOW)

    assert decision.allowed is True
    # 4 used after the reservation: a request for 97 must be refused.
    after = ledger.check("llm_calls", amount=97, now=_NOW)
    assert after.allowed is False and after.used == 4


def test_snapshot_still_sees_all_of_history(tmp_path: Path) -> None:
    """The boundary: the operator diagnostic reports over all time, and the
    bounded read must not silently truncate it."""
    ledger = _ledger(tmp_path)
    _fill(ledger, old=300, recent=7)

    snap = ledger.snapshot(now=_NOW)

    assert snap["totals"]["llm_calls"] == 307


def test_the_read_expands_until_it_covers_the_window(tmp_path: Path) -> None:
    """The coverage proof itself, pinned.

    The earlier tests all fit inside the FIRST tail span, so removing the
    proof left them green — caught by break-testing. Here the in-window
    records deliberately exceed one span: a read that stopped at the first
    chunk would undercount, and undercounting a budget means spending past
    the limit.
    """
    ledger = BudgetLedger(
        path=tmp_path / "budget.jsonl",
        windows=[BudgetWindow(name="hour", seconds=3600,
                              limits={"llm_calls": 5000})],
    )
    for i in range(200):
        ledger.record("llm_calls", amount=1, reason="old",
                      now=_NOW - timedelta(days=3, minutes=i))
    in_window = 1200
    for i in range(in_window):
        ledger.record("llm_calls", amount=1, reason="recent",
                      now=_NOW - timedelta(seconds=i + 1))

    size = (tmp_path / "budget.jsonl").stat().st_size
    from core.budget_ledger import _TAIL_READ_BYTES
    assert size > _TAIL_READ_BYTES * 2, (
        "the fixture no longer exceeds one tail span, so this test would pass "
        "for the wrong reason"
    )

    decision = ledger.check("llm_calls", amount=5000, now=_NOW)

    assert decision.allowed is False
    assert decision.used == in_window, (
        f"counted {decision.used} of {in_window} in-window records — the read "
        "stopped before it covered the window"
    )
