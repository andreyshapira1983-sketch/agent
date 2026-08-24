"""Defect 1: the charter block chose its goal before .env was loaded.

Post-mortem of the 19:31 tick (2026-08-19): every scheduled tick picked its
goal on `gpt-4o-mini` (route_reason=default) while the campaign body ran on
the pinned `gpt-5.6-sol` — because `load_dotenv` lives INSIDE `run_tick`
(agent_tick.py:776) and inside the campaign pacer (:1341), while the
charter block sits in `main()` and runs before both. Every engine
measurement of the goal-selecting brain was therefore invalid.
"""
from __future__ import annotations

import os
from pathlib import Path

import agent_tick


def test_the_helper_actually_loads_the_env_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("AGENT_PLANNER_MODEL", raising=False)
    (tmp_path / ".env").write_text(
        "AGENT_PLANNER_MODEL=gpt-5.6-sol\n", encoding="utf-8")

    agent_tick._ensure_env_loaded(tmp_path)

    assert os.environ.get("AGENT_PLANNER_MODEL") == "gpt-5.6-sol"


def test_an_ambient_pin_is_not_overridden(tmp_path: Path, monkeypatch) -> None:
    """A shell-exported pin outranks the file — the operator's live override."""
    monkeypatch.setenv("AGENT_PLANNER_MODEL", "gpt-5.6-terra")
    (tmp_path / ".env").write_text(
        "AGENT_PLANNER_MODEL=gpt-5.6-sol\n", encoding="utf-8")

    agent_tick._ensure_env_loaded(tmp_path)

    assert os.environ.get("AGENT_PLANNER_MODEL") == "gpt-5.6-terra"


def test_a_missing_env_file_is_silent(tmp_path: Path) -> None:
    agent_tick._ensure_env_loaded(tmp_path)  # must not raise


def test_the_charter_block_loads_env_before_building_the_router() -> None:
    """The wiring pin: the selector's router must be built AFTER the env is
    loaded, or the pinned engine silently degrades to the default."""
    src = Path(agent_tick.__file__).read_text(encoding="utf-8")
    charter_at = src.find("if args.campaign and args.charter:")
    # Строитель зовётся по имени с 2026-08-24 (`_charter_goal_router`); до
    # того `ModelRouter.from_env(` стоял здесь же строкой. Ищутся оба, чтобы
    # проба меряла ПОРЯДОК, а не то, где сегодня живёт построение.
    router_at = min(
        (at for at in (
            src.find("_charter_goal_router(", charter_at),
            src.find("ModelRouter.from_env(", charter_at),
        ) if at != -1),
        default=-1,
    )
    load_at = src.find("_ensure_env_loaded(", charter_at)
    assert charter_at != -1 and router_at != -1
    assert load_at != -1, "the charter block does not load .env at all"
    assert load_at < router_at, (
        "the charter block builds its router before loading .env — the "
        "pinned planner model degrades to the provider default"
    )
