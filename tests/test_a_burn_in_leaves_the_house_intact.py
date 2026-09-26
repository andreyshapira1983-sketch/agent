"""Десять часов безнадзорной работы не должны портить дом.

WHY THIS EXISTS. Аудит автономности 2026-09-17 предложил приёмочный набор для
десятичасового прогона в песочнице. Четыре пункта из набора живут по-разному:

* «повтор не превышает своего бюджета» уже стоит красным свидетелем в
  `tests/test_replan.py::TestReplanIsBounded` — дублировать нечего;
* «очередь не оставляет строк в работе» уже стоит в
  `tests/test_agent_tick_task_lifecycle.py::test_raising_run_does_not_leave_the_task_running`
  и `tests/test_task_lifecycle.py::test_exception_leaves_the_task_terminal_not_running`;
* двух оставшихся не было НИ В КАКОМ виде, и именно они отвечают на вопрос
  прогона: остался ли дом там же, где стоял, и можно ли прочесть то, что агент
  о себе записал.

Первый инвариант — границы. Безнадзорный проход обязан писать внутрь рабочей
копии и только внутрь: замер здесь идёт по всему дереву репозитория, а не по
списку ожидаемых путей, потому что дефект этого класса выглядит как запись
ТУДА, КУДА НИКТО НЕ СМОТРЕЛ.

Второй инвариант — читаемость. Прогон, оставивший после себя обрывок строки в
журнале состояния, хуже прогона, не оставившего ничего: следующий запуск на
такой строке либо падает, либо молча теряет память. Проверка идёт в конце
прохода и построчно, как читает сам код.

Оба теста нарочно требуют, чтобы внутри песочницы файлы РЕАЛЬНО появились:
зелёный цвет на пустом проходе доказывал бы только то, что ничего не работало.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from core.approval_inbox import DEFAULT_APPROVAL_INBOX_PATH, ApprovalInbox
from core.best_next_action import BestNextAction
from core.campaign import CampaignActionOutcome, CampaignConfig, run_campaign
from core.self_apply_bridge import SELF_APPLY_OPERATION, build_self_apply_payload

REPO_ROOT = Path(__file__).resolve().parents[1]
_SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".venv"}


def _house_print(root: Path) -> dict[str, tuple[int, int]]:
    """Слепок дерева: путь → (размер, время правки в наносекундах)."""
    out: dict[str, tuple[int, int]] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for name in filenames:
            p = Path(dirpath) / name
            try:
                st = p.stat()
            except OSError:
                out[str(p)] = (-1, -1)
                continue
            out[str(p)] = (st.st_size, st.st_mtime_ns)
    return out


class _Lane:
    """Подделка полосы применения: не зовёт ни git, ни pytest."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, **kwargs: Any) -> dict:
        self.calls += 1
        return {"status": "committed_local", "proposal_id": kwargs["item_id"]}


@pytest.fixture()
def sandbox(tmp_path: Path) -> Path:
    (tmp_path / "data").mkdir()
    (tmp_path / "logs").mkdir()
    return tmp_path


def _unattended_pass(sandbox: Path) -> dict:
    """Один безнадзорный проход: кампания, затем слив разрешённых правилом заявок.

    Взяты ровно те две артерии, которые пишут долговременное состояние без
    человека: `run_campaign` (леджер кампании) и `drain_rule_approved_proposals`
    (ящик, журнал расхода полномочия, уроки).
    """
    from core.rule_approved_apply import drain_rule_approved_proposals

    action = BestNextAction(
        action="propose_minimal_test_repair",
        title="Propose one minimal fix",
        severity="high", priority=80,
        reason="tests are failing with concrete names",
        risk="reversible",
    )

    def gather(agent, workspace, approval_inbox):
        return {"action": action}

    def execute(*, agent, workspace, action, config, approval_inbox=None):
        return CampaignActionOutcome(status="completed", result="готово", spent=True)

    run_campaign(
        CampaignConfig(goal="g", max_cycles=2, max_unproductive_streak=2),
        agent=SimpleNamespace(log=None),
        workspace=str(sandbox),
        gather_signals=gather,
        execute_action=execute,
        now_fn=lambda: datetime(2026, 9, 17, 12, 0, tzinfo=timezone.utc),
    )

    inbox = ApprovalInbox(path=sandbox / DEFAULT_APPROVAL_INBOX_PATH)
    item = inbox.add(
        operation="autonomous_runtime.standing_grant",
        summary="standing effects grant: 3 runs/day",
        risk="irreversible",
        payload={"max_runs_per_day": 3},
        expires_at=(datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
    )
    inbox.approve(item.id)
    inbox.add(
        operation=SELF_APPLY_OPERATION,
        summary="self-apply: note.md",
        risk="reversible",
        payload=build_self_apply_payload(
            files=[{"path": "knowledge/doctrine/future/note.md", "content": "# заметка\n"}],
            reason="черновик",
            origin="burn_in",
            workspace=sandbox,
        ),
    )
    return drain_rule_approved_proposals(sandbox, dry_run=False)


def test_no_write_outside_sandbox(sandbox: Path, monkeypatch: Any) -> None:
    """Безнадзорный проход не трогает ни одного файла вне рабочей копии."""
    import core.self_apply_bridge as bridge

    monkeypatch.setattr(bridge, "run_approved_self_apply", _Lane())

    before = _house_print(REPO_ROOT)
    out = _unattended_pass(sandbox)
    after = _house_print(REPO_ROOT)

    assert out["applied"] == 1, "проход обязан был что-то сделать, иначе тест ни о чём"
    written = [p for p in sandbox.rglob("*") if p.is_file()]
    assert written, "внутри песочницы не появилось ни одного файла состояния"

    changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
    assert changed == [], f"проход изменил файлы вне песочницы: {changed[:10]}"


def test_state_files_parse_at_the_end(sandbox: Path, monkeypatch: Any) -> None:
    """Всё, что проход записал о себе, читается тем же способом, что и пишется."""
    import core.self_apply_bridge as bridge

    monkeypatch.setattr(bridge, "run_approved_self_apply", _Lane())

    _unattended_pass(sandbox)

    checked = 0
    for path in sorted(sandbox.rglob("*")):
        if not path.is_file() or path.suffix not in {".json", ".jsonl"}:
            continue
        text = path.read_text(encoding="utf-8")
        if path.suffix == ".json":
            json.loads(text)
            checked += 1
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                json.loads(line)
            except json.JSONDecodeError as exc:
                pytest.fail(f"{path.name}:{lineno} не читается: {exc}")
            checked += 1

    assert checked > 0, "проход не оставил ни одной записи состояния — проверять нечего"
