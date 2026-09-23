"""Висящая заявка запрещает работу со СВОИМИ файлами, а не со всей работой.

Замер ночи на 2026-09-23. В ящике лежала одна заявка — разделить
`core/loop_step_execution.py`. Правило готовности превращало её в причину
`readiness_blocker: 1 approval item(s) pending`, и ворота блокировали ЛЮБОЕ
эффектное действие: десять циклов подряд пустые, конспект по физике в
`data/notes` не ложился из-за бумаги про файл в `core/`. Оператор отклонил
заявку — и в тот же час пошла настоящая работа.

Слово оператора в то утро: сделать блокировку соразмерной. Заявка называет
свои файлы сама (`payload.files[].path`), дальше них её запрет не идёт.
Остальные жёсткие причины — выключатель и бюджет — соразмерности не знают и
останавливают всё: это не «одна бумага», а конец денег или прямой запрет.
"""
from __future__ import annotations

from core.actuation_gateway import ActuationGateway
from core.gateway_consult import approval_paths
from core.models import Action
from core.policy import PolicyGate
from tools.base import ToolRegistry
from tools.file_write import FileWriteTool

_BLOCKER = ("1 approval item(s) pending",)
_CLAIMED = frozenset({"core/loop_step_execution.py", "core/anatomy_groups.py"})


class _Item:
    def __init__(self, paths: list[str]) -> None:
        self.payload = {"files": [{"path": p} for p in paths]}


class _Inbox:
    def __init__(self, items: list[_Item], broken: bool = False) -> None:
        self._items, self._broken = items, broken

    def pending(self) -> list[_Item]:
        if self._broken:
            raise RuntimeError("ящик не читается")
        return self._items


def _registry(tmp_path) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(FileWriteTool(workspace_root=tmp_path))
    return registry


def _gateway(paths: frozenset[str] | None) -> ActuationGateway:
    return ActuationGateway(
        PolicyGate(registry=ToolRegistry()),
        path="runtime",
        readiness_blockers=_BLOCKER,
        check_readiness=True,
        pending_approval_paths=paths,
    )


def _write(path: str) -> Action:
    return Action(type="tool_call", step_id="step_1", tool_name="file_write",
                  parameters={"path": path, "content": "x"})


def test_an_unrelated_write_is_no_longer_blocked(tmp_path) -> None:
    """Тот самый конспект по физике, который ночью не лёг."""
    decision = _gateway(_CLAIMED).evaluate(
        _write("data/notes/20260923T010000_competence_physics.md"),
        registry=_registry(tmp_path))

    assert decision.outcome != "block", decision.reasons


def test_the_claimed_file_is_still_blocked(tmp_path) -> None:
    """Граница: файл, о котором лежит заявка, по-прежнему закрыт."""
    decision = _gateway(_CLAIMED).evaluate(
        _write("core/loop_step_execution.py"), registry=_registry(tmp_path))

    assert decision.outcome == "block"
    assert any("approval item(s) pending" in r for r in decision.reasons)


def test_without_knowledge_of_the_files_the_ban_stays_general(tmp_path) -> None:
    """Пустые сведения — это «не знаю», а не разрешение."""
    decision = _gateway(None).evaluate(
        _write("data/notes/x.md"), registry=_registry(tmp_path))

    assert decision.outcome == "block"


def test_a_budget_stop_is_not_softened(tmp_path) -> None:
    """Соразмерность касается только заявок: деньги останавливают всё."""
    gateway = ActuationGateway(
        PolicyGate(registry=ToolRegistry()), path="runtime",
        readiness_blockers=("persistent budget usage is already at or above a configured limit",),
        check_readiness=True, pending_approval_paths=_CLAIMED)

    decision = gateway.evaluate(_write("data/notes/x.md"), registry=_registry(tmp_path))

    assert decision.outcome == "block"


def test_the_paths_are_read_from_the_inbox() -> None:
    inbox = _Inbox([_Item(["core/a.py", "core/b.py"]), _Item(["docs/c.md"])])

    assert approval_paths(inbox) == frozenset({"core/a.py", "core/b.py", "docs/c.md"})


def test_an_unreadable_inbox_reports_nothing_not_permission() -> None:
    """Нечитаемый ящик не превращается в разрешение: сведений нет."""
    assert approval_paths(_Inbox([], broken=True)) == frozenset()
