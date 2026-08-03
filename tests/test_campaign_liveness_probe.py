"""MIR-070 — the daemon-liveness action checks the window that raised it.

Measured live (operator's `:campaign-start`, 2026-08-03): the signal layer
correctly raised `restore_daemon_liveness` from the stale heartbeat, but the
execution path handed the question to a free-planning LLM run, which chose
`read_logs` over the agent's OWN run journal, found 0 events, and honestly
answered «не подтверждает и не опровергает» — 2 model calls, 63 cost units,
signal not cleared, campaign stalled.

The ruling-consistent fix: this action is answerable by READING ACTUAL STATE
(`core/heartbeat_io`) — the same store the signal came from — so the campaign
executes it deterministically: zero model calls, and the artifact answers the
operator's five points (что проверял / как / доказательство / что осталось
непроверенным / уверенность).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.best_next_action import BestNextAction
from core.campaign_io import _default_execute_action
from core.campaign_types import CampaignConfig


def _action() -> BestNextAction:
    return BestNextAction(
        action="restore_daemon_liveness",
        title="Verify the autonomous daemon is actually running",
        severity="critical",
        priority=100,
        reason="stale heartbeat",
        evidence=("last heartbeat long ago",),
        unknowns=("whether the scheduler is alive",),
        risk="read_only",
        recommended_command="agent_tick.py --status",
        confidence=0.6,
    )


class _BombAgent:
    """Explodes on ANY attribute access the LLM path would need — proof the
    deterministic branch never builds a runtime or touches a model."""

    def __getattr__(self, name):
        raise AssertionError(f"deterministic probe touched agent.{name}")


def _write_heartbeat(ws: Path, *, age_seconds: float) -> None:
    ts = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
    hb = {
        "event": "tick_complete",
        "ts": ts.isoformat(),
        "result_status": "none",
    }
    path = ws / "data" / "daemon_heartbeat.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(hb), encoding="utf-8")


def test_fresh_heartbeat_clears_the_signal_without_a_model_call(tmp_path):
    _write_heartbeat(tmp_path, age_seconds=10)
    outcome = _default_execute_action(
        agent=_BombAgent(),
        workspace=tmp_path,
        action=_action(),
        config=CampaignConfig(goal="project health", dry_run=True),
    )
    assert outcome.result == "completed"
    assert outcome.llm_calls_spent == 0
    assert outcome.cost_units_spent == 0
    art = outcome.artifact or ""
    assert "fresh" in art or "жив" in art.lower() or "свеж" in art.lower()


def test_stale_heartbeat_reports_honestly_and_names_the_operator_step(tmp_path):
    _write_heartbeat(tmp_path, age_seconds=6 * 3600)
    outcome = _default_execute_action(
        agent=_BombAgent(),
        workspace=tmp_path,
        action=_action(),
        config=CampaignConfig(goal="project health", dry_run=True),
    )
    assert outcome.result == "completed"
    assert outcome.llm_calls_spent == 0
    art = outcome.artifact or ""
    assert "agent_tick" in art or "install_daemon" in art
    # The five points, in human language (MIR-069's shape, honoured here).
    for marker in ("Проверял:", "Способ:", "Доказательство:", "Непроверенным осталось:", "Уверенность:"):
        assert marker in art, f"five-point explanation missing {marker!r}"


def test_missing_heartbeat_is_named_missing(tmp_path):
    outcome = _default_execute_action(
        agent=_BombAgent(),
        workspace=tmp_path,
        action=_action(),
        config=CampaignConfig(goal="project health", dry_run=True),
    )
    assert outcome.result == "completed"
    assert outcome.llm_calls_spent == 0
    assert "никогда" in (outcome.artifact or "") or "missing" in (outcome.artifact or "")


def test_malformed_heartbeat_is_named_unreadable_not_zero_seconds(tmp_path):
    """A file WITHOUT a readable timestamp must not report «0 минут назад» —
    the verdict names the broken record honestly."""
    path = tmp_path / "data" / "daemon_heartbeat.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"event": "tick_complete"}', encoding="utf-8")
    outcome = _default_execute_action(
        agent=_BombAgent(),
        workspace=tmp_path,
        action=_action(),
        config=CampaignConfig(goal="project health", dry_run=True),
    )
    assert outcome.result == "completed"
    assert outcome.llm_calls_spent == 0
    art = outcome.artifact or ""
    assert "повреждён" in art or "без метки" in art
    assert "0.0 мин" not in art
