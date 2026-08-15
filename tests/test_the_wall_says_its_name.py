"""Стена обязана называть себя: id и НАСТОЯЩИЙ статус блокиратора.

Background: docs/CODE_NOTES.md, "The wall that would not say its name".
"""
from __future__ import annotations

from dataclasses import dataclass

from core.self_task_producer import SELF_TASK_OPERATION, _unresolved_task


@dataclass
class _Item:
    id: str
    operation: str
    status: str


class _Inbox:
    def __init__(self, *items: _Item) -> None:
        self._items = list(items)

    def list(self):
        return list(self._items)


_APPROVED = _Item("ain_5755a5a5", SELF_TASK_OPERATION, "approved")
_PENDING = _Item("ain_aaaa1111", SELF_TASK_OPERATION, "pending")


def test_an_approved_unexecuted_task_is_the_blocker():
    """Живой случай 2026-08-15: сообщение говорило «pending», блокировала
    ОДОБРЕННАЯ `ain_5755a5a5` от 2026-08-03. В списке `pending` её нет по
    определению, и оператор искал её там впустую — двенадцать дней.
    """
    found = _unresolved_task(_Inbox(_APPROVED))

    assert found is not None
    assert found.id == "ain_5755a5a5"
    assert found.status == "approved"


def test_a_pending_task_is_still_a_blocker():
    """Улов не отдан: ждущая решения — тоже незакрытая работа."""
    assert _unresolved_task(_Inbox(_PENDING)) is not None


def test_a_decided_task_stops_blocking():
    """Отклонённая и исполненная — закрыты, и путь свободен."""
    assert _unresolved_task(_Inbox(_Item("a", SELF_TASK_OPERATION, "denied"))) is None
    assert _unresolved_task(_Inbox(_Item("b", SELF_TASK_OPERATION, "executed"))) is None
    assert _unresolved_task(_Inbox(_Item("c", SELF_TASK_OPERATION, "aborted"))) is None


def test_another_operation_is_not_this_gate_s_business():
    """Затвор про задачи Stage-A, а не про любую заявку в очереди."""
    assert _unresolved_task(_Inbox(_Item("d", "self_apply_lane.run", "pending"))) is None


def test_an_unreadable_inbox_does_not_invent_a_blocker():
    """Нечитаемая очередь — не повод выдумать стену; прежнее поведение цело."""
    class _Broken:
        def list(self):
            raise OSError("нет доступа")

    assert _unresolved_task(_Broken()) is None


def test_the_report_names_the_id_and_the_real_status():
    """Вторая половина дороги: найти блокиратор мало — надо его НАЗВАТЬ.
    Именно молчание о статусе и стоило двенадцати дней.
    """
    import inspect

    from core import self_task_producer

    source = inspect.getsource(self_task_producer.produce_coding_task)

    assert "is {_status} and not executed" in source or "_status} and not executed" in source
    assert ":self-task-build to execute it" in source
