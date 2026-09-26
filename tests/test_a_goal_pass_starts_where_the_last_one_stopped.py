"""The next pass on the operator's goal starts from what the last pass wrote and concluded.

Live run 26.09: three hours of passes on «connect the local models» re-read the same samples
while the started edit (proposals/selffix/local_models_tools/edits.txt) sat unchanged; in a
chat that named what was done and the next step, he rewrote it in 2 minutes. Each pass began
with no memory of the previous one. Remedy: a progress file between sessions (Anthropic,
«Effective harnesses for long-running agents»).
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from core.autonomous_runtime import AutonomousRuntimeConfig
from core.campaign_types import CampaignConfig

GOAL = "Подключи к себе проверенные локальные модели сервера как инструменты"
EDIT = "proposals/selffix/local_models_tools/edits.txt"


def _action(name: str = "pursue_goal") -> Any:
    return SimpleNamespace(action=name, title="t", severity="medium", priority=1, risk="reversible",
                           grounds="operator_goal", decided_by="goal_first", next_check_at=None,
                           reason="", evidence=())


def _run_passes(tmp_path: Path, monkeypatch: Any, *, goal_is_self: bool = False, action: str = "pursue_goal"):
    from core import campaign_io

    seen: list[str] = []
    agent = SimpleNamespace(log=None, compensation_log=[])
    answers = iter(["Evidence scope: I only have evidence for " + "a.md, " * 200
                    + "Conclusion: Правка ушей начата, проверка красная: нет поля registry.tools", "второй заход"])

    class _Runtime:
        def __init__(self, agent_: Any, **_kw: Any) -> None:
            pass

        def run(self, config: AutonomousRuntimeConfig) -> Any:
            seen.append(config.goal)
            agent.compensation_log.append(SimpleNamespace(
                description=f"undo creation of '{EDIT}' by deleting it"))
            task = SimpleNamespace(task=SimpleNamespace(kind="goal"), status="done",
                                   details={"answer": next(answers)})
            return SimpleNamespace(tasks=[task], status="completed", stop_reason="", to_dict=dict,
                                   semantic_result=lambda: ("completed", True))

    monkeypatch.setattr("core.autonomous_runtime.AutonomousRuntime", _Runtime, raising=True)
    for _ in range(2):
        campaign_io._default_execute_action(
            agent=agent, workspace=tmp_path, action=_action(action),
            config=CampaignConfig(goal=GOAL, dry_run=False, goal_is_self=goal_is_self))
    return seen


def test_the_second_pass_is_told_what_the_first_did(tmp_path: Path, monkeypatch: Any) -> None:
    first, second = _run_passes(tmp_path, monkeypatch)
    assert "Прогресс прошлых заходов" not in first
    assert EDIT in second and "нет поля registry.tools" in second, second[-600:]
    assert "Продолжай с последнего шага" in second


def test_the_agents_own_goal_keeps_no_progress_file(tmp_path: Path, monkeypatch: Any) -> None:
    _, second = _run_passes(tmp_path, monkeypatch, goal_is_self=True)
    assert "Прогресс прошлых заходов" not in second
    assert not (tmp_path / "data" / "goal_progress").exists()
