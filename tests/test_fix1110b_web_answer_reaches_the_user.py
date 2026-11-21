"""Вопрос «последняя версия X»: факт со страницы доходит до человека, выдуманный — нет."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.answer_format import format_human_response
from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.planner import LLMPlanner
from core.policy import PolicyGate
from tests.conftest import FakeLLM
from tests.test_fix1110b_web_claim_in_page import _BODY, _NAV, _TAIL
from tests.test_output_policy_integration import StaticWebFetchTool
from tools.base import ToolRegistry

_URL = "https://www.python.org/downloads/"
_QUESTION = "Найди в интернете последнюю стабильную версию Python и назови источник."
_PAGE = _NAV + _BODY + _TAIL


def _ask(workspace: Path, monkeypatch: pytest.MonkeyPatch, version: str) -> str:
    monkeypatch.setenv("AGENT_HUMAN_CHAT", "1")
    plan = json.dumps({"reasoning": "fetch the downloads page",
                       "steps": [{"tool": "web_fetch", "arguments": {"url": _URL}, "rationale": "source"}]})
    draft = (
        f"Conclusion: Последняя стабильная версия Python — {version}, источник — {_URL} [web:{_URL}]\n"
        "Facts:\n"
        f"- На странице загрузок стоит «Download Python {version}» [web:{_URL}]\n"
        f"- В списке релизов верхняя запись — Python {version} [web:{_URL}]\n"
        "Sources:\n"
        f"1. web:{_URL}\n"
        "Confidence: high\n"
        "Unverified: nothing\n"
        "Safety: nothing\n"
    )
    llm = FakeLLM(responses=[plan, draft])
    registry = ToolRegistry()
    registry.register(StaticWebFetchTool(_PAGE))
    logger = TraceLogger(trace_id=new_trace_id(), log_dir=workspace / "logs", verbose=False)
    agent = AgentLoop(registry=registry, policy=PolicyGate(registry), llm=llm, logger=logger,
                      planner=LLMPlanner(llm=llm, registry=registry))
    return format_human_response(agent.run(user_question=_QUESTION))


def test_the_version_from_the_page_reaches_the_user_with_its_source(
    workspace: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    shown = _ask(workspace, monkeypatch, "3.15.2")
    assert "3.15.2" in shown and "python.org/downloads" in shown, shown
    assert "realtime" not in shown and "не подтвержден" not in shown.lower(), shown


def test_a_version_the_page_does_not_have_is_not_presented_as_checked(
    workspace: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    shown = _ask(workspace, monkeypatch, "3.15.7")
    assert "3.15.7" not in shown or "не провер" in shown.lower(), shown
