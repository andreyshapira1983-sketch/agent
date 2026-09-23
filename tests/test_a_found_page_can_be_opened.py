"""Страницу, найденную поиском, можно открыть в том же плане.

Замер 2026-09-23 (экзамен оператора «найди реальную работу»): шаг web_fetch с
url={{step:1.output}} снимался санитайзером ДО запуска — «url must start with
http:// or https://», — потому что проверялся шаблон, а не адрес. Связка
«нашёл поиском -> открыл найденное» была невозможна в принципе; свежесть
задач подтвердить первоисточником было нельзя. А шапка при этом велела
планировать «[web_search, web_fetch <best_url>]» — адрес, которого при
составлении плана ещё нет.

Как лечат (движки задач — Airflow, GitHub Actions): шаблон проверяется ПОСЛЕ
подстановки, теми же правилами. Как выбирать нужное из выдачи — ReWOO (Xu et
al. 2023): между поиском и открытием стоит шаг, который вынимает нужное из
найденного; у нас это python_probe, печатающий один адрес.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.step_references import UnresolvedStepReference, resolve_step_references
from core.step_sanitizer import sanitize_step

_REPO = Path(__file__).resolve().parents[1]
_STEPS = {"1", "2", "3"}
_SEARCH = [
    {"title": "Why Do Multi-Agent LLM Systems Fail?", "url": "https://arxiv.org/abs/2503.13657",
     "snippet": "…", "source": "ddg"},
    {"title": "other", "url": "https://example.org/x", "snippet": "…", "source": "ddg"},
]


def test_a_fetch_of_a_step_output_survives_planning() -> None:
    warnings: list[str] = []
    spec = sanitize_step("web_fetch", {"url": "{{step:2.output}}"}, None, 3, warnings)
    assert spec is not None, warnings
    assert spec["arguments"]["url"] == "{{step:2.output}}"


def test_the_whole_chain_search_pick_fetch() -> None:
    """Настоящая лаборатория выбирает адрес, web_fetch получает его одной строкой."""
    from core.step_sanitizer import resolved_url_refusal
    from tools.python_probe import PythonProbeTool

    code = 'results = {{step:1.output}}\nprint(results[0]["url"])'
    probe_spec = sanitize_step("python_probe", {"code": code}, None, 2, [])
    fetch_spec = sanitize_step("web_fetch", {"url": "{{step:2.output}}"}, None, 3, [])
    outputs = {"1": _SEARCH}
    probe_args = resolve_step_references(probe_spec["arguments"], outputs, plan_steps=_STEPS)
    outputs["2"] = PythonProbeTool(workspace_root=_REPO).run(**probe_args)

    fetch_args = resolve_step_references(fetch_spec["arguments"], outputs, plan_steps=_STEPS)

    assert fetch_args == {"url": "https://arxiv.org/abs/2503.13657"}
    assert resolved_url_refusal("web_fetch", fetch_args) is None


def test_the_locks_hold_after_substitution() -> None:
    """Замок не снят, а перенесён туда, где виден настоящий адрес."""
    from core.step_sanitizer import resolved_url_refusal

    assert resolved_url_refusal("web_fetch", {"url": "http://127.0.0.1/admin"})
    assert resolved_url_refusal("web_fetch", {"url": "http://169.254.169.254/latest/meta-data"})
    assert resolved_url_refusal("web_fetch", {"url": "ftp://x"})
    assert resolved_url_refusal("web_fetch", {"url": ["https://a.org"]})
    assert resolved_url_refusal("file_read", {"path": "x"}) is None


def test_a_result_list_is_not_an_address() -> None:
    with pytest.raises(UnresolvedStepReference, match="one name"):
        resolve_step_references({"url": "{{step:1.output}}"}, {"1": _SEARCH}, plan_steps=_STEPS)


def test_a_literal_address_is_judged_at_planning_as_before() -> None:
    warnings: list[str] = []
    assert sanitize_step("web_fetch", {"url": "ftp://x"}, None, 1, warnings) is None
    assert "must start with http:// or https://" in warnings[0]
    warnings.clear()
    assert sanitize_step("web_fetch", {"url": "http://localhost/x"}, None, 1, warnings) is None
    assert "local network" in warnings[0]


def test_the_prompt_teaches_the_chain_instead_of_an_unknowable_url() -> None:
    from core.planner_prompt import PLANNER_SYSTEM

    i = PLANNER_SYSTEM.find("- web_fetch(")
    block = PLANNER_SYSTEM[i:PLANNER_SYSTEM.find("\n- ", i + 1)]
    assert "<best_url>" not in block, "шапка велит планировать адрес, которого ещё нет"
    assert '{"url": "{{step:2.output}}"}' in block
    assert "results = {{step:1.output}}" in block
