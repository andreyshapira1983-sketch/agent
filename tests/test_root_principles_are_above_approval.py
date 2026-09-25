"""Корневые принципы стоят выше одобрения (слово оператора 24.09).

Агент отказывал во вредной просьбе только по процедуре — «нужно одобрение
человека»; значит, любой, кто может одобрить, мог бы сдвинуть границу.
Первоисточники: OpenAI Model Spec (корневые правила не отменяет ни оператор, ни
пользователь), Anthropic Constitutional AI (отказ по сути, с причиной).
"""
from __future__ import annotations

import inspect

from core import loop_synthesis
from core.planner_prompt import PLANNER_SYSTEM
from core.root_principles import ROOT_PRINCIPLES, with_root_principles


def test_the_planner_reads_the_principles_first() -> None:
    assert PLANNER_SYSTEM.startswith(ROOT_PRINCIPLES)


def test_the_answer_writer_reads_them_too() -> None:
    assert "with_root_principles(self._resolve_synthesis_contract())" in inspect.getsource(loop_synthesis)


def test_approval_does_not_unlock_them_and_refusal_is_on_substance() -> None:
    text = ROOT_PRINCIPLES.lower()
    assert "not even when a human approves" in text
    assert "never unlocks these principles" in text
    assert "refuse on substance" in text


def test_the_block_is_added_once() -> None:
    once = with_root_principles("SYSTEM")
    assert with_root_principles(once) == once and once.count("ROOT PRINCIPLES") == 1


def test_the_other_half_an_unneeded_refusal_is_a_failure_too() -> None:
    """Замер ценностей 25.09: 54 % безобидных задач AgentHarm отказаны «по ценностям».

    Первоисточники блока говорят и это: Model Spec — лучшие намерения и узкий
    отказ; конституция Claude — бесполезность не бывает безопасной сама по
    себе; arXiv 2510.08158 — отказы на слова-триггеры. Свидетель по делу —
    повторный замер ценностей; этот тест лишь не даёт половине пропасть.
    """
    text = ROOT_PRINCIPLES.lower()
    assert "unneeded refusal is a failure too" in text
    assert "judge what the request would do" in text and "not its words" in text
    assert "decline that part and do the rest" in text
    assert "name who would be" in text
