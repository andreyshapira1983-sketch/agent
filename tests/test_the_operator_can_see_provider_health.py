"""Provider health is visible in the operator's status — audit of MIR-132.

WHY THIS EXISTS. Not a new defect report: this came out of AUDITING MIR-132's
own closure against the field's named circuit-breaker failure modes
(2026-08-22). Five were tested against this code; three passed (fail-open when
no substitute exists, per-model errors do not demote a whole provider,
OpenAI's real rate-limit messages do not demote while its out-of-credit
message does). Two did not:

* **visibility** — every source calls a breaker without observable trips
  untunable, and ours was invisible: a demotion nobody can see is
  indistinguishable from a provider that simply stopped being chosen, which is
  precisely the unexplained quiet a week-long unattended run must not produce;
* **half-open recovery** — the field returns ONE probe after a cooldown; ours
  returns full traffic, so a still-dead provider costs up to three wasted
  calls per window instead of one. Recorded, not fixed: three fast refusals
  per two hours is a smaller cost than the machinery to avoid them, and the
  measurement that would change that answer is a provider which HANGS rather
  than refusing. Reversed by one word from the operator.

AND THE LINE'S OWN FIRST DRAFT WAS WRONG, caught by running it live: it
printed «anthropic ok» for a provider with 391 credit refusals and zero
successes, because stale failures fall out of the cooldown window. "ok" was
reporting "not in cooldown right now", which the operator would read as
"working". It reports the last recorded outcome now.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent_tick import _provider_health_line
from core.model_usage import ModelUsageLedger, ModelUsageLimits

_CREDIT = "BadRequestError: Your credit balance is too low to access the API."
_NOW = datetime.now(timezone.utc)


def _record(workspace: Path, *, provider: str, status: str,
            error: str | None, at: datetime) -> None:
    ledger = ModelUsageLedger(
        path=workspace / "data" / "model_usage.jsonl", limits=ModelUsageLimits()
    )
    ledger.record(role="planner", provider=provider, model="m", route_reason="t",
                  cost_tier="low", status=status, input_tokens=1, output_tokens=1,
                  estimated=True, started_at=at.isoformat(),
                  completed_at=at.isoformat(), duration_ms=1, error=error)


def test_a_demoted_provider_is_named_in_the_status(workspace: Path) -> None:
    for i in range(3):
        _record(workspace, provider="anthropic", status="error", error=_CREDIT,
                at=_NOW - timedelta(minutes=10 - i))

    line = _provider_health_line(workspace)

    assert "anthropic" in line and "SKIPPED" in line, (
        f"the operator cannot see that a provider is being skipped: {line!r}"
    )


def test_ok_means_the_last_call_worked_not_merely_out_of_cooldown(
    workspace: Path,
) -> None:
    """The first draft's live bug: 391 refusals five days ago read as «ok»."""
    for i in range(3):
        _record(workspace, provider="anthropic", status="error", error=_CREDIT,
                at=_NOW - timedelta(days=5, minutes=i))

    line = _provider_health_line(workspace)

    assert "anthropic ok" not in line, (
        f"a provider whose every recorded call failed is reported healthy: {line!r}"
    )
    assert "last=error" in line


def test_a_working_provider_reads_ok(workspace: Path) -> None:
    _record(workspace, provider="openai", status="success", error=None,
            at=_NOW - timedelta(minutes=1))

    assert "openai ok" in _provider_health_line(workspace)


def test_the_status_line_never_crashes(workspace: Path) -> None:
    """Operator status must survive a broken store — the whole block is
    wrapped, and this pins that the health line is no exception."""
    path = workspace / "data" / "model_usage.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not json\n", encoding="utf-8")

    assert isinstance(_provider_health_line(workspace), str)
