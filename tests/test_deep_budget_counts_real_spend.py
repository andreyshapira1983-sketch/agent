"""The deep-call ceiling must count spend, not attempts.

`AGENT_DEEP_MAX_CALLS_PER_SESSION` exists to cap what an autonomous repair may
spend on the expensive model. `deep_budget_ok` measures that from the usage
ledger, which is the right source — it cannot disagree with what was billed.
But it counts every record carrying a `deep_approved:` route reason, whatever
became of the call.

Measured live on 2026-07-29 with a ceiling of 2, from `data/model_usage.jsonl`:

    01:13  claude-opus-5  status=error    0 tokens
    01:18  claude-opus-5  status=error    0 tokens
    01:30  claude-opus-5  status=success  54 794 tokens

The first two were HTTP 400s rejected by the provider before any inference ran.
Nothing was billed for them and nothing came back. Yet both consumed a slot, so
an operator who funded two deep calls could be left with zero results and no
budget — the ceiling spent on requests that never cost anything.

The invariant these tests pin: a ceiling on spend is decremented by spend. A
call that consumed no tokens consumed no budget.

Deliberately NOT asserted: that a failed call is always free. A call can fail
after the provider has already billed for it — that is exactly what happened at
01:30's predecessor, where a successful first leg was followed by a raising
continuation. The token count is therefore the test, not the status field: it
is the one number that tracks money whatever the outcome label says.
"""
from __future__ import annotations

from dataclasses import dataclass

from core.deep_escalation import deep_budget_ok


@dataclass
class _Record:
    """The fields of `ModelUsageRecord` that the budget rule reads."""

    route_reason: str
    status: str
    total_tokens: int


class _Ledger:
    def __init__(self, *records: _Record) -> None:
        self.records = list(records)


def _rejected() -> _Record:
    """A request the provider refused — no inference, no tokens, no bill."""
    return _Record("deep_approved:high_value_repair", "error", 0)


def _billed(tokens: int = 54_794) -> _Record:
    """A call that ran and was charged for."""
    return _Record("deep_approved:high_value_repair", "success", tokens)


def test_rejected_calls_do_not_consume_the_ceiling():
    """Today's exact shape: two 400s against a ceiling of two."""
    ledger = _Ledger(_rejected(), _rejected())
    assert deep_budget_ok(ledger, limit=2) is True, (
        "two provider rejections billed nothing, so the operator's two funded "
        "calls are both still available"
    )


def test_billed_calls_do_consume_the_ceiling():
    """The half that must keep working, or the ceiling stops capping anything."""
    ledger = _Ledger(_billed(), _billed())
    assert deep_budget_ok(ledger, limit=2) is False


def test_a_failure_after_billing_still_counts():
    """Status is not the test — tokens are.

    A call can be charged and then fail. Reading `status` alone would hand that
    spend back to the operator as free budget.
    """
    ledger = _Ledger(_Record("deep_approved:high_value_repair", "error", 38_474))
    assert deep_budget_ok(ledger, limit=1) is False


def test_mixed_history_counts_only_what_was_billed():
    ledger = _Ledger(_rejected(), _billed(), _rejected())
    assert deep_budget_ok(ledger, limit=2) is True   # one real call spent
    assert deep_budget_ok(ledger, limit=1) is False  # …and it filled a ceiling of one


# ── the guards: none of the above may loosen the off switch ──────────────────

def test_a_zero_ceiling_is_off_whatever_the_ledger_says():
    """Unset budget means no deep call, and no ledger state can unlock it."""
    assert deep_budget_ok(_Ledger(), limit=0) is False
    assert deep_budget_ok(_Ledger(_rejected()), limit=0) is False
    assert deep_budget_ok(_Ledger(), limit=-1) is False


def test_other_route_reasons_are_not_counted():
    """Only approved deep calls spend the deep budget."""
    ledger = _Ledger(
        _Record("deep_downgraded:budget_block", "success", 9_000),
        _Record("complexity:standard:openai", "success", 11_388),
    )
    assert deep_budget_ok(ledger, limit=1) is True


def test_an_empty_ledger_leaves_the_whole_ceiling_available():
    assert deep_budget_ok(_Ledger(), limit=1) is True
