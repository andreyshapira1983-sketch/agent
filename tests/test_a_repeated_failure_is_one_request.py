"""Одна неустранённая поломка — одна заявка, а не одна на тик.

WHY THIS EXISTS. Аудит автономности 2026-09-17, находка 3: `_maybe_propose_repair`
звал `inbox.add(...)` без `dedup_key`, а `ApprovalInbox.add` дедуплицирует
ТОЛЬКО при переданном ключе. Пока падающий тест не починен — а не починен он
ровно потому, что заявка ждёт человека, — каждый следующий тик клал в ящик ещё
одну такую же просьбу.

Цена этого не косметическая. Ящик — вход человека в работу агента, и он же
ворота: `run_self_apply_lane` отказывается работать, когда `approvals_pending >
0`. Размножение заявок топит настоящие просьбы в шуме И само себя блокирует.

Материально ДРУГАЯ поломка обязана открывать новую заявку: дедупликация —
защита от повтора, а не способ проглотить вторую беду.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agent_tick import _maybe_propose_repair
from core.approval_inbox import ApprovalInbox
from core.self_repair import RepairProposal


class _FakeReport:
    def __init__(self, proposal: RepairProposal) -> None:
        self.status = "proposed"
        self.proposal = proposal
        self.confidence = 0.9
        self.evidence = ("ev1",)
        self.diagnosis = "diag"


class _FakeGen:
    """Генератор, который каждый раз предлагает одну и ту же починку."""

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs

    def generate(self, *, target_path: str, **kwargs: Any) -> _FakeReport:
        return _FakeReport(RepairProposal(
            path=target_path,
            proposed_content="# fixed\n",
            reason="fix the thing",
            confidence=0.9,
        ))


class _FakeAgent:
    llm = object()


@pytest.fixture()
def repo(tmp_path: Path, monkeypatch: Any) -> Path:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("# x\n", encoding="utf-8")
    (tmp_path / "tests" / "test_y.py").write_text("# y\n", encoding="utf-8")
    monkeypatch.setattr(
        "core.repair_proposal.RepairProposalGenerator", _FakeGen, raising=True
    )
    return tmp_path


def _tick(workspace: Path, inbox: ApprovalInbox, *, test_file: str) -> dict:
    return _maybe_propose_repair(
        workspace,
        {"failed": 1, "errors": 0, "failed_tests": [f"tests/{test_file}::test_a"]},
        inbox,
        _FakeAgent(),
    )


def test_repeated_failure_does_not_multiply_inbox(repo: Path) -> None:
    """Красный свидетель: три тика с одной поломкой — три одинаковых просьбы.

    Демон тикает по расписанию; человек отвечает раз в день. Между ответами
    ящик рос линейно по времени, а не по числу настоящих проблем.
    """
    inbox = ApprovalInbox(path=repo / "data" / "approval_inbox.jsonl")

    for _ in range(3):
        assert _tick(repo, inbox, test_file="test_x.py")["repair_proposed"] is True

    repairs = [i for i in inbox.pending() if i.operation == "repair_proposal"]
    assert len(repairs) == 1, (
        f"одна неустранённая поломка породила {len(repairs)} заявок"
    )


def test_a_different_failure_still_opens_its_own_request(repo: Path) -> None:
    """Вторая беда — вторая просьба. Дедупликация не вправе её проглотить."""
    inbox = ApprovalInbox(path=repo / "data" / "approval_inbox.jsonl")

    _tick(repo, inbox, test_file="test_x.py")
    _tick(repo, inbox, test_file="test_y.py")

    repairs = [i for i in inbox.pending() if i.operation == "repair_proposal"]
    assert len(repairs) == 2
    assert {i.payload["target_file"] for i in repairs} == {
        "tests/test_x.py", "tests/test_y.py",
    }


def test_the_request_carries_its_dedup_key(repo: Path) -> None:
    """Ключ лежит В заявке: иначе повтор нечем узнать после перезапуска."""
    inbox = ApprovalInbox(path=repo / "data" / "approval_inbox.jsonl")

    _tick(repo, inbox, test_file="test_x.py")

    item = next(i for i in inbox.pending() if i.operation == "repair_proposal")
    assert item.payload.get("dedup_key"), "заявка без ключа неотличима от новой"

    # Перезапуск: новый объект ящика читает тот же файл и узнаёт повтор.
    reopened = ApprovalInbox(path=repo / "data" / "approval_inbox.jsonl")
    _tick(repo, reopened, test_file="test_x.py")
    assert len([i for i in reopened.pending() if i.operation == "repair_proposal"]) == 1


def test_a_resolved_request_lets_the_next_one_through(repo: Path) -> None:
    """Дедупликация смотрит только на ОЖИДАЮЩИЕ: решённое не блокирует новое.

    Если поломка вернулась после того, как человек закрыл заявку, агент обязан
    спросить снова — иначе один ответ навсегда заглушает один класс беды.
    """
    inbox = ApprovalInbox(path=repo / "data" / "approval_inbox.jsonl")

    _tick(repo, inbox, test_file="test_x.py")
    first = next(i for i in inbox.pending() if i.operation == "repair_proposal")
    inbox.approve(first.id, reason="принято", actor="test")

    _tick(repo, inbox, test_file="test_x.py")

    assert len([i for i in inbox.pending() if i.operation == "repair_proposal"]) == 1
