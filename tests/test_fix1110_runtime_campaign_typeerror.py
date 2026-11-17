"""A TypeError from inside the campaign must not start a second full run."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from agent_tick import run_paced_campaign


def _result():
    return SimpleNamespace(
        status="completed", stop_reason="", cycles_run=1,
        totals={"llm_calls": 1, "cost_units": 0, "wall_clock_seconds": 0.0},
        user_summary=lambda: "=== fake campaign ===",
    )


def _run(workspace: Path, fn, hb: list[dict]) -> int:
    return run_paced_campaign(
        workspace, dry_run=True, max_cycles=1, opening_spend=(2, 3),
        heartbeat_fn=lambda ws, payload: hb.append(payload),
        run_campaign_fn=fn,
        build_agent_fn=lambda ws: SimpleNamespace(log=None),
    )


def test_an_inner_typeerror_is_reported_not_retried(workspace: Path):
    calls: list[dict] = []

    def _campaign(config, *, agent, workspace, approval_inbox, ledger, on_cycle,
                  next_goal=None, opening_spend=(0, 0)):
        calls.append({"opening_spend": opening_spend})
        raise TypeError("unsupported operand type(s) for +: 'int' and 'NoneType'")

    hb: list[dict] = []
    rc = _run(workspace, _campaign, hb)

    assert len(calls) == 1, "the campaign was run twice"
    assert rc == 1
    errors = [p for p in hb if p.get("event") == "campaign_error"]
    assert errors and "TypeError" in errors[0]["error"]


def test_an_old_signature_still_runs_without_the_new_arguments(workspace: Path):
    calls: list[int] = []

    def _campaign(config, *, agent, workspace, approval_inbox, ledger, on_cycle):
        calls.append(1)
        return _result()

    assert _run(workspace, _campaign, []) == 0
    assert len(calls) == 1


def test_a_current_signature_receives_the_new_arguments(workspace: Path):
    seen: dict = {}

    def _campaign(config, *, agent, workspace, approval_inbox, ledger, on_cycle,
                  next_goal=None, opening_spend=(0, 0)):
        seen["opening_spend"] = opening_spend
        return _result()

    assert _run(workspace, _campaign, []) == 0
    assert seen["opening_spend"] == (2, 3)
