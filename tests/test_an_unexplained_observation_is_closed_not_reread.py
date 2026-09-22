"""The itch of «unexplained» is closed by the climb, not by re-reading a journal.

Day run 2026-09-19/20: 230 of ~370 drive tasks were «разобрать наблюдение», the
same three or four observations again and again. Two causes, both here:

* the observation text named no journal (14 of 15), the model guessed
  data/charter_decisions.jsonl — four fields, no detectors — and honestly
  answered «данных нет» every time;
* prose closes nothing. An observation stops being unexplained only when a
  claim with competing, falsifiable explanations covers its fingerprint, and
  only `explain_causal_observation` writes one.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from core.best_next_action import BestNextAction
from core.campaign import CampaignActionOutcome, CampaignConfig, run_campaign
from core.causal_lesson import observation_from_episode
from core.drive_goal import _observation_goal


def test_the_observation_names_the_journal_where_the_signals_live() -> None:
    episode = SimpleNamespace(id="ep-7", defect_signals=("reasoning_action_mismatch", "self_contradiction"),
                              completion_state="blocked", source_labels=(), run_id="run-7")
    obs = observation_from_episode(episode, trace_id="trace_abc")
    assert obs is not None
    text = obs.observed_mismatch
    assert "data/episodic_memory.jsonl" in text and "defect_signals" in text
    # 2026-09-22: здесь было закреплено «logs/trace_trace_abc.jsonl» — адрес
    # несуществующего файла; номер трассы уже начинается с «trace_».
    assert "ep-7" in text and "logs/trace_abc.jsonl" in text and "trace_trace_" not in text
    assert "charter_decisions" not in text


def test_the_uncertainty_goal_is_the_climb_itself(tmp_path: Path, monkeypatch) -> None:
    record = SimpleNamespace(observed_mismatch="детекторы X при завершении blocked; объяснения не выдвинуты",
                             fingerprint="cobs_dead")
    monkeypatch.setattr("core.causal_climb_action.unexplained_observations", lambda _root: (record,))
    pick = _observation_goal(tmp_path)
    assert pick is not None
    assert pick.action == "explain_causal_observation", "прозой наблюдение не закрывается"
    assert "cobs_dead" in pick.success_check and "causal_claims.jsonl" in pick.success_check
    assert "детекторы X" in pick.goal


def test_no_observation_means_no_goal(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("core.causal_climb_action.unexplained_observations", lambda _root: ())
    assert _observation_goal(tmp_path) is None


class _MenuWantsSomethingElse:
    def __call__(self, agent, workspace, approval_inbox, goal="", exhausted_actions=frozenset()):
        return {"action": BestNextAction(action="improve_failure_to_idea_pipeline", title="t", severity="medium",
                                         priority=59, reason="r", decided_by="priority_table",
                                         grounds="retained_record")}


def test_the_campaign_runs_the_action_the_goal_names(tmp_path: Path) -> None:
    ran: list[str] = []

    def execute(*, agent, workspace, action, config, approval_inbox=None):
        ran.append(action.action)
        return CampaignActionOutcome(result="completed", llm_calls_spent=1, work_done=True)

    run_campaign(
        CampaignConfig(goal="Объяснить наблюдение о себе: детекторы X", max_cycles=2, max_idle_streak=1,
                       dry_run=False, max_unproductive_streak=0, goal_first=True,
                       goal_action="explain_causal_observation"),
        agent=SimpleNamespace(log=None), workspace=str(tmp_path),
        gather_signals=_MenuWantsSomethingElse(), execute_action=execute,
        now_fn=lambda: datetime(2026, 9, 20, 6, 0, tzinfo=timezone.utc), sleep_fn=lambda _s: None,
    )
    assert ran[:1] == ["explain_causal_observation"], ran
