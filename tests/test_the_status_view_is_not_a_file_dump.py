"""Статус для человека не должен быть свалкой тел файлов.

Замер, отвергнутые варианты и границы: MIR-167 в docs/audit/MASTER_ISSUE_REGISTRY.md.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.approval_inbox import ApprovalInbox
from core.autonomous_runtime import AutonomousRuntime
from tests.test_autonomous_runtime import _agent

_BODY = "# заголовок\n" + ("строка предлагаемого файла\n" * 400)


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    return tmp_path


def _inbox_with_a_proposal(workspace: Path) -> ApprovalInbox:
    inbox = ApprovalInbox(path=workspace / "data" / "approval_inbox.jsonl")
    for i in range(4):
        inbox.add(
            operation="self_build_task.approve",
            summary=f"предложение {i}",
            risk="reversible",
            payload={
                "path": f"core/new_{i}.py",
                "content": _BODY,
                "content_b64": _BODY.encode("utf-8").hex(),
            },
        )
    return inbox


def _status(workspace: Path) -> dict:
    inbox = _inbox_with_a_proposal(workspace)
    agent = _agent(workspace, with_tests=False)
    return AutonomousRuntime(
        agent, workspace=workspace, approval_inbox=inbox
    ).status()


def test_the_status_carries_no_file_bodies(workspace: Path) -> None:
    """Красный свидетель: 84 % статуса — тела файлов, и каждое дважды.

    Живой замер 2026-08-26: `:auto-status` напечатал **1 873 000 байт**, из них
    685 КБ в полях `content` и 890 КБ в `content_b64` — тот же текст ещё раз в
    другом виде. На собственно сводку осталось 15 %.
    """
    rendered = json.dumps(_status(workspace), ensure_ascii=False)

    assert "content_b64" not in rendered
    assert _BODY not in rendered, (
        "тело предлагаемого файла попало в статус для человека"
    )
