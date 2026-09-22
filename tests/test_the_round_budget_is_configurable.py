"""Число кругов хода задаётся окружением (AGENT_MAX_REPLANS).

2026-09-22 14:49: агент в разговоре прошёл «записал правку → patch_check
красный → исправил → снова красный» и упёрся в три круга: на третье
исправление хода не хватило. Без переменной — прежние три.
"""
from __future__ import annotations

from pathlib import Path

from app.bootstrap import build_agent


def test_default_is_three(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("AGENT_MAX_REPLANS", raising=False)
    agent = build_agent(tmp_path, with_memory=False, with_persistent=False)
    assert agent.replan_policy.max_total_replans == 3


def test_the_environment_raises_it(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_MAX_REPLANS", "6")
    agent = build_agent(tmp_path, with_memory=False, with_persistent=False)
    assert agent.replan_policy.max_total_replans == 6
