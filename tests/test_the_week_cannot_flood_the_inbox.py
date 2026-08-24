"""За неделю без человека ящик одобрений не переполняется.

ИСТОРИЧЕСКИЙ КЛАСС (H-18, docs/audit/HISTORICAL_FAILURE_LEDGER.md).
Atlassian, апрель 2022: скрипт удалил 883 клиентских сайта, а восстановление
заняло до двух недель — не потому, что копий не было, а потому что путь
починки был ПОШТУЧНЫМ и не масштабировался до размера происшествия. Форма
класса: скорость производства повреждений против пропускной способности
починки.

ЛОКАЛЬНАЯ ФОРМА. Производитель кладёт предложение в ящик одобрений, а разбирает
их человек. При 48 тиках в сутки неделя даёт 336 возможностей — то есть 336
заявок, если ничто не держит.

ЗАМЕР 2026-08-24. Держат ДВА разных механизма, и они не взаимозаменяемы:

  * ключ дедупа схлопывает ОДИНАКОВЫЕ предложения: 336 добавлений с одним
    ключом дают одну строку в файле (612 байт) и одну заявку. Без ключа —
    336 строк и 336 заявок;
  * затвор «одна заявка в полёте» (`_unresolved_task`) держит и на `pending`,
    и на `approved`-но-неисполненной. Он и есть настоящая граница: ключ дедупа
    ничего не сделал бы с 336 РАЗНЫМИ предложениями.
"""
from __future__ import annotations

import pathlib

from core.approval_inbox import ApprovalInbox
from core.self_task_producer import SELF_TASK_OPERATION, _unresolved_task
from core.state_integrity import read_state_jsonl


def _inbox(tmp_path: pathlib.Path) -> ApprovalInbox:
    return ApprovalInbox(path=tmp_path / "inbox.jsonl")


def test_a_week_of_identical_proposals_stays_one_row(tmp_path) -> None:
    inbox = _inbox(tmp_path)

    for tick in range(48 * 7):
        inbox.add(
            operation=SELF_TASK_OPERATION, summary=f"предложение {tick}",
            risk="read_only", reasons=("сигнал",), payload={},
            dedup_key="self_task:core/x.py:same",
        )

    assert len(read_state_jsonl(tmp_path / "inbox.jsonl")) == 1
    assert len(inbox.pending()) == 1


def test_the_in_flight_gate_holds_while_a_task_is_unresolved(tmp_path) -> None:
    """Настоящая граница: разные предложения ключом дедупа не удержать."""
    inbox = _inbox(tmp_path)
    assert _unresolved_task(inbox) is None

    first = inbox.add(
        operation=SELF_TASK_OPERATION, summary="первое", risk="read_only",
        reasons=(), payload={},
    )
    assert _unresolved_task(inbox) is not None, "затвор не держит на pending"

    inbox.approve(first.id)
    assert _unresolved_task(inbox) is not None, (
        "одобренная, но НЕ исполненная заявка перестала считаться незавершённой"
    )

    inbox.mark_executed(first.id)
    assert _unresolved_task(inbox) is None


def test_a_denial_releases_the_gate(tmp_path) -> None:
    """Граница с другой стороны: отказ обязан отпускать, иначе один «нет»
    остановил бы производителя навсегда."""
    inbox = _inbox(tmp_path)
    item = inbox.add(
        operation=SELF_TASK_OPERATION, summary="предложение", risk="read_only",
        reasons=(), payload={},
    )

    inbox.deny(item.id, reason="не нужно")

    assert _unresolved_task(inbox) is None
