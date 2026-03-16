"""
Tests for planner extended goals: fetch, prioritize, aggregate, parse json.
"""
from __future__ import annotations

from src.planning.planner import make_plan


def test_make_plan_time():
    plan = make_plan("what time is it")
    tools = [s.tool for s in plan.steps]
    assert "get_current_time" in tools


def test_make_plan_fetch():
    plan = make_plan("fetch data from API")
    tools = [s.tool for s in plan.steps]
    assert "fetch_url" in tools


def test_make_plan_priority():
    plan = make_plan("prioritize tasks")
    tools = [s.tool for s in plan.steps]
    assert "suggest_priority" in tools


def test_make_plan_aggregate():
    plan = make_plan("aggregate stats")
    tools = [s.tool for s in plan.steps]
    assert "aggregate_simple" in tools


def test_make_plan_parse_json():
    plan = make_plan("parse json")
    tools = [s.tool for s in plan.steps]
    assert "parse_json" in tools


def test_make_plan_ask_question():
    plan = make_plan("ask a question")
    tools = [s.tool for s in plan.steps]
    assert "generate_question" in tools
