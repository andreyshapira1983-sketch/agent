"""Предмет, названный целью, доходит до рук — значением, а не пустой розеткой.

Замер, отвергнутые варианты и границы: MIR-159 в docs/audit/MASTER_ISSUE_REGISTRY.md.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import core.self_build_producer as producer_mod
from core.campaign_io import _propose_engineering_step


def _agent() -> MagicMock:
    agent = MagicMock()
    agent.model_router.for_role.return_value = MagicMock()
    return agent


def _capture(monkeypatch) -> dict:
    seen: dict = {}

    def _fake(**kwargs):
        seen.update(kwargs)
        report = MagicMock()
        report.status = "proposed"
        report.user_summary.return_value = "предложение"
        return report

    monkeypatch.setattr(producer_mod, "produce_self_apply_proposal", _fake)
    return seen


def test_a_named_target_arrives_as_the_candidate_set(monkeypatch, tmp_path: Path) -> None:
    """Красный свидетель: розетка была пуста у ОБОИХ машинных вызывающих.

    Живой замер 2026-08-19: голова выбрала цель про один модуль, руки выдали
    предложение про другой, и связать их было нечем — производитель заново
    выбирал свой топ бэклога.
    """
    seen = _capture(monkeypatch)

    _propose_engineering_step(
        agent=_agent(),
        workspace=tmp_path,
        approval_inbox=MagicMock(),
        target="core/subagent_registry.py",
    )

    assert seen.get("candidate_targets") == ("core/subagent_registry.py",), (
        "предмет, названный целью, не доехал до производителя"
    )


def test_no_named_target_leaves_the_producer_free(monkeypatch, tmp_path: Path) -> None:
    """Граница: цель без предмета ничего не навязывает — поведение прежнее.

    Отвергнут вариант «всегда что-нибудь передать»: пустой кортеж связал бы
    руки там, где голова ничего не называла.
    """
    seen = _capture(monkeypatch)

    _propose_engineering_step(
        agent=_agent(),
        workspace=tmp_path,
        approval_inbox=MagicMock(),
        target=None,
    )

    assert not seen.get("candidate_targets"), (
        "цель не называла предмета, а рукам всё равно навязали список"
    )


def test_the_socket_is_not_satisfied_by_a_hollow_keyword(monkeypatch, tmp_path: Path) -> None:
    """Инвариант нельзя закрыть, передавая пустоту.

    Проверка по РАЗБОРУ (`test_some_machine_road_hands_the_producer_a_target`)
    видит только имя аргумента и была бы довольна вечным `None`. Здесь
    проверяется значение.
    """
    seen = _capture(monkeypatch)

    _propose_engineering_step(
        agent=_agent(),
        workspace=tmp_path,
        approval_inbox=MagicMock(),
        target="core/loop.py",
    )

    targets = seen.get("candidate_targets")
    assert targets and all(isinstance(t, str) and t for t in targets)
