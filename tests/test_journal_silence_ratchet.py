"""A handler that writes nothing to the journal must not multiply.

Census item A7. `tests/test_except_audit_ratchet.py` already holds a rule about
these handlers — that a silent one must carry a comment — and it stands at zero.
The census then found three defects whose handlers all satisfied it:

    the answer-safety check whose failure looked like a clean success (A2)
    the referent resolver that could stop working unnoticed (A3)
    the task-specific prompt contract silently replaced (A4)

Every one of them was commented. The comment helps whoever reads the code; it
does nothing for an operator reading logs while the agent runs unattended. So
the existing rule is not wrong, it is answering a different question — and the
question it does not ask is the one the operator asks at three in the morning.

This ratchet asks it: how many handlers report NOTHING to the journal? Two
exclusions, both structural: a handler that re-raises has reported by the
strongest means there is, and a handler nested inside a reporting one is the
last-resort guard around reporting itself, where reporting again is a recursion.

The number is not a defect count. Seventeen handlers being silent is not
seventeen bugs — some are genuinely best-effort paths whose caller reports. It
is a budget that may only shrink, so the next silent handler has to displace an
existing one or be argued for.

The scope is the loop layer, deliberately. That is where the census traced
consequences, one handler at a time. Across all of `core/` the same counter
reports 281, and asserting those are defects would be the mistake this whole
exercise is about — claiming past the measurement. Widening the scope is a
separate decision that wants its own evidence.
"""
from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from except_audit import journal_silent_in  # noqa: E402

#: Measured 2026-08-05 after A2, A3 and A4 closed six of the census's original
#: twenty-eight. Lower it when a handler starts reporting; never raise it
#: without saying here why the new silence is right.
JOURNAL_SILENT_BASELINE = 17

_LAYER = Path("core")


def _loop_layer_rows() -> list[dict]:
    return [
        row for row in journal_silent_in(_LAYER)
        if Path(row["file"]).name.startswith("loop")
    ]


def test_journal_silence_does_not_grow():
    rows = _loop_layer_rows()
    addresses = [f"{Path(r['file']).name}:{r['line']}" for r in rows]

    assert len(rows) <= JOURNAL_SILENT_BASELINE, (
        "a handler stopped reporting to the journal. A comment satisfies the "
        "older audit and still leaves an operator reading logs with nothing. "
        f"Now {len(rows)}, budget {JOURNAL_SILENT_BASELINE}:\n  "
        + "\n  ".join(addresses)
    )


def test_the_budget_is_not_left_above_the_measurement():
    """Bank the win when one is earned, the same rule the length ratchet uses.

    A budget that drifts above the real number stops being a budget: it admits
    new silence for free, which is how a closed campaign leaves a live defect.
    """
    rows = _loop_layer_rows()

    assert len(rows) >= JOURNAL_SILENT_BASELINE - 2, (
        f"only {len(rows)} silent handlers remain but the budget still says "
        f"{JOURNAL_SILENT_BASELINE} — lower it to bank the win"
    )


def test_a_reraising_handler_is_not_counted_as_silent():
    """It reported by the strongest means available."""
    from except_audit import journal_silent_handlers

    src = (
        "def f():\n"
        "    try:\n"
        "        g()\n"
        "    except ValueError:\n"
        "        raise\n"
    )
    assert journal_silent_handlers(src, "x.py") == []


def test_a_guard_around_reporting_itself_is_not_counted():
    """Reporting from inside a failed report is a recursion, not a fix."""
    from except_audit import journal_silent_handlers

    src = (
        "def f(self):\n"
        "    try:\n"
        "        g()\n"
        "    except ValueError:\n"
        "        try:\n"
        "            self.log.log('failed', {})\n"
        "        except Exception:\n"
        "            pass\n"
    )
    rows = journal_silent_handlers(src, "x.py")
    assert rows == [], rows


def test_a_plainly_silent_handler_is_counted():
    """The counter must catch the thing it exists for, or it proves nothing."""
    from except_audit import journal_silent_handlers

    src = (
        "def f():\n"
        "    try:\n"
        "        g()\n"
        "    except ValueError:\n"
        "        pass\n"
    )
    rows = journal_silent_handlers(src, "x.py")
    assert [r["line"] for r in rows] == [4], rows


def test_the_layer_helper_counts_as_reporting():
    """`_sensor_failed` journals on the caller's behalf.

    The older audit matched a literal `.log(` call and scored fifteen reporting
    handlers silent — which is how a correctly reporting handler ended up having
    to carry a justifying comment to satisfy the very tool meant to find silence.
    """
    from except_audit import journal_silent_handlers

    src = (
        "def f(self):\n"
        "    try:\n"
        "        g()\n"
        "    except ValueError as exc:\n"
        "        self._sensor_failed('thing', exc)\n"
    )
    assert journal_silent_handlers(src, "x.py") == []
