"""Отказ обязан нести причину — иначе самый ценный сигнал уходит пустым.

ЧТО ЗАМЕРЕНО (2026-08-23, живой инбокс). Работу, которую агент выбрал сам,
оператор отклоняет в 55–60 % случаев: `self_build_task.approve` — 3 отказа из
5 и НИ ОДНОГО исполнения за всю историю; `self_apply_lane.run` — 15 из 27.
Для сравнения, разрешение на прогон, заданный человеком, отклоняется в 19 %.
Разрыв втрое: отсутствующие ворота «а правда ли этот сигнал» существуют — их
исполняет человек вручную и задним числом.

ЧТО УЖЕ БЫЛО. Мост вердиктов (2026-08-19) полон с обеих сторон: причина едет
в `data/approval_outcomes.jsonl`, а `core/charter_goal._recent_verdicts`
читает её обратно в выбор цели. Три отказа после постройки моста несут
причину, все 35 прежних пусты — они старше его.

ЧТО ОСТАВАЛОСЬ ДЫРОЙ. Причину ничто не ТРЕБОВАЛО: `:approval-deny <id>` без
слов принимался, и в исход уезжала пустая строка. Канал обучения существовал и
мог быть молча опустошён ровно в тот момент, ради которого заведён.

ПОЧЕМУ ТОЛЬКО ОТКАЗ. Одобрение говорит «да, как предложено» — его содержание
лежит в самой заявке. У отказа содержания нет нигде, кроме причины: без неё
запись сообщает, что что-то было не так, и не сообщает, что именно.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.approval_inbox import ApprovalInbox


def _inbox(tmp_path: Path) -> tuple[ApprovalInbox, str]:
    inbox = ApprovalInbox(path=tmp_path / "data" / "approval_inbox.jsonl")
    item = inbox.add(
        operation="self_build_task.approve",
        summary="починить сенсор размера",
        risk="read_only",
        reasons=("сигнал из бэклога",),
        payload={},
    )
    return inbox, item.id


def _outcomes(tmp_path: Path) -> list[dict]:
    path = tmp_path / "data" / "approval_outcomes.jsonl"
    if not path.exists():
        return []
    rows = [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [r.get("payload", r) for r in rows]


def test_a_denial_without_a_reason_is_refused(tmp_path: Path) -> None:
    inbox, item_id = _inbox(tmp_path)

    with pytest.raises(ValueError, match="reason"):
        inbox.deny(item_id)

    assert [i.status for i in inbox.list()] == ["pending"], (
        "заявка сменила статус, хотя отказ был отвергнут — половина операции "
        "прошла, и это хуже, чем пустая причина"
    )


def test_a_blank_reason_is_not_a_reason(tmp_path: Path) -> None:
    """Пробелы и табы не должны обходить требование."""
    inbox, item_id = _inbox(tmp_path)

    with pytest.raises(ValueError, match="reason"):
        inbox.deny(item_id, reason="   \t  ")


def test_a_denial_with_a_reason_records_it(tmp_path: Path) -> None:
    inbox, item_id = _inbox(tmp_path)

    inbox.deny(item_id, reason="цель уже не держится: файл переехал", actor="operator")

    denials = [r for r in _outcomes(tmp_path) if r.get("verdict") == "denied"]
    assert len(denials) == 1
    assert denials[0]["reason"] == "цель уже не держится: файл переехал"


def test_an_approval_still_needs_no_reason(tmp_path: Path) -> None:
    """Граница: содержание одобрения лежит в самой заявке, а не в причине."""
    inbox, item_id = _inbox(tmp_path)

    inbox.approve(item_id)

    assert [i.status for i in inbox.list()] == ["approved"]


def test_the_reason_reaches_the_reader_that_exists(tmp_path: Path) -> None:
    """Полная петля: причина обязана доехать до того, кто выбирает следующую
    цель, иначе требование — просто новая графа."""
    from core.charter_goal import _recent_verdicts

    inbox, item_id = _inbox(tmp_path)
    inbox.deny(item_id, reason="сигнал оказался про прокси, а не про объект")

    verdicts = _recent_verdicts(tmp_path)

    assert verdicts, "исход не доехал до читателя вердиктов"
    assert verdicts[-1][0] == "denied"
    assert "прокси" in verdicts[-1][2], verdicts[-1]
