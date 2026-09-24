"""Поиск в интернете не запрещают по словам вопроса — решает планировщик.

След logs/trace_e5bb2e6cc49b3cb012770f70b758b714.jsonl (2026-09-24): оператор
просил «зайди в интернет и найди, как это делают хорошо», планировщик шесть раз
ставил web_search, и шесть раз его вырезал словарь самоанализа — вопрос звучал
«про себя». Словарь чинили фразой за фразой; литература (Adaptive-RAG,
Self-RAG) оставляет решение искать планировщику.

Отсев выдачи по косинусу e5 (как «оценщик» CRAG) замерен на 618 выдачах из
следов агента 2026-09-24 и отвергнут: медиана полезных 0.815, прочих 0.814 —
любой порог режет полезное так же, как шум. В CRAG оценщик — дообученный
T5-large, не косинус.
"""
from __future__ import annotations

import json
from pathlib import Path

from core.planner import LLMPlanner
from tests.conftest import FakeLLM
from tools.base import ToolRegistry
from tools.list_dir import ListDirTool
from tools.web_search import WebSearchTool

# Вопрос «про себя», на котором словарь глушил поиск.
SELF_QUESTION = (
    "Прежде чем переделывать своего персонажа в своём коде, зайди в интернет "
    "и найди, как делают rigging хорошо."
)


def test_planner_keeps_its_web_search_on_a_question_about_itself(tmp_path: Path) -> None:
    reg = ToolRegistry()
    reg.register(ListDirTool(workspace_root=tmp_path))
    reg.register(WebSearchTool())
    canned = json.dumps({
        "reasoning": "Operator asked to look it up first.",
        "steps": [
            {"tool": "web_search", "arguments": {"query": "character rigging best practices"}},
            {"tool": "list_dir", "arguments": {"path": "."}},
        ],
    })
    out = LLMPlanner(llm=FakeLLM(responses=[canned]), registry=reg).plan(
        question=SELF_QUESTION, file_hint=None,
    )
    assert "web_search" in [s["tool"] for s in out.sources], out.warnings
