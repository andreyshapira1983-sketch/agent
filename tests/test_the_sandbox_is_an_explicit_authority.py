"""Песочница — ЯВНОЕ полномочие, а не щель в производственных воротах.

WHY THIS EXISTS. Аудит автономности 2026-09-17, находки 5 и 6: без человека
агент не может починить даже собственную изолированную копию — автономный путь
применения пропускает ровно один класс («новый документ .md»), а
`core/self_repair.py` без `approval_provider` обрывается на `unavailable`.
Значит десятичасовой прогон в песочнице измерял бы не самопочинку, а скорость,
с которой агент упирается в ожидание человека.

Отсюда режим: полномочие, которое нужно ВКЛЮЧИТЬ двумя независимыми жестами, и
которое ничего не меняет, пока оба не сделаны. Что здесь проверяется:

* производство fail-closed по умолчанию — ни переменная окружения, ни файл
  метки поодиночке полномочия не дают;
* метка обязана называть ИМЕННО эту рабочую копию: файл, скопированный в
  производственное дерево, полномочия там не включает;
* включённая песочница применяет КОД без человека, но той же полосой — с
  прицельными тестами, полной батареей, локальной веткой и откатом;
* песочница не вправе писать вне себя и не вправе двигать собственный забор;
* у песочницы есть суточный потолок, и он считается тем же журналом, что у
  стоячего гранта;
* каждая попытка — событие в журнале, и отказ тоже.

Анти-требование: это НЕ обход PolicyGate и не тихий флаг. Полномочие названо,
записано в журнал и ограничено сроком.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from core.approval_inbox import DEFAULT_APPROVAL_INBOX_PATH, ApprovalInbox
from core.burn_in_sandbox import (
    SANDBOX_ENV_FLAG,
    SANDBOX_MARKER,
    load_sandbox_authority,
    sandbox_execution_verdict,
)
from core.self_apply_bridge import SELF_APPLY_OPERATION, build_self_apply_payload


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "logs").mkdir()
    (tmp_path / "data").mkdir()
    (tmp_path / "config").mkdir()
    return tmp_path


def _marker(
    workspace: Path,
    *,
    path: str | None = None,
    days: int = 1,
    max_applies: int = 20,
    sandbox: bool = True,
) -> None:
    (workspace / SANDBOX_MARKER).parent.mkdir(parents=True, exist_ok=True)
    (workspace / SANDBOX_MARKER).write_text(
        json.dumps({
            "sandbox": sandbox,
            "workspace": path if path is not None else str(workspace),
            "max_applies_per_day": max_applies,
            "expires_at": (datetime.now(timezone.utc) + timedelta(days=days)).isoformat(),
            "reason": "10-часовой burn-in, слово оператора",
        }),
        encoding="utf-8",
    )


def _env(on: bool) -> dict:
    return {SANDBOX_ENV_FLAG: "on"} if on else {}


def _inbox(workspace: Path) -> ApprovalInbox:
    return ApprovalInbox(path=workspace / DEFAULT_APPROVAL_INBOX_PATH)


def _pending_code(inbox: ApprovalInbox, workspace: Path, rel: str, body: str = "x = 1\n"):
    payload = build_self_apply_payload(
        files=[{"path": rel, "content": body}],
        reason=f"починка {rel}",
        origin="autonomous",
        workspace=workspace,
    )
    return inbox.add(
        operation=SELF_APPLY_OPERATION,
        summary=f"self-apply: {rel}",
        risk="reversible",
        payload=payload,
    )


class _Lane:
    def __init__(self, status: str = "committed_local") -> None:
        self.status = status
        self.item_ids: list[str] = []

    def __call__(self, **kwargs: Any) -> dict:
        self.item_ids.append(kwargs["item_id"])
        return {
            "proposal_id": kwargs["item_id"],
            "status": self.status,
            "reason": "",
            "files_changed": [],
            "tests_run": ["tests"],
            "rollback_status": "none",
        }


@pytest.fixture()
def lane(monkeypatch: Any) -> _Lane:
    import core.self_apply_bridge as bridge

    fake = _Lane()
    monkeypatch.setattr(bridge, "run_approved_self_apply", fake)
    return fake


# ── что включает полномочие, и что его НЕ включает ──────────────────────────


def test_production_is_fail_closed_by_default(workspace: Path) -> None:
    """Ни метки, ни переменной — полномочия нет. Это производственный путь."""
    assert load_sandbox_authority(workspace, env={}) is None


def test_the_env_flag_alone_is_not_the_authority(workspace: Path) -> None:
    """Переменная окружения — самый лёгкий жест, и одна она ничего не решает.

    Иначе полномочие включалось бы опечаткой в планировщике задач.
    """
    assert load_sandbox_authority(workspace, env=_env(True)) is None


def test_the_marker_alone_is_not_the_authority(workspace: Path) -> None:
    """Файл в дереве — тоже один жест. Забытая метка не вправе включать режим."""
    _marker(workspace)
    assert load_sandbox_authority(workspace, env={}) is None


def test_the_marker_must_name_this_workspace(workspace: Path) -> None:
    """Метка, скопированная в другое дерево, там не действует.

    Ровно тот случай, которого стоит бояться: песочницу клонировали в
    производство вместе с её меткой.
    """
    _marker(workspace, path=str(workspace / "somewhere-else"))
    assert load_sandbox_authority(workspace, env=_env(True)) is None


def test_an_expired_sandbox_is_not_an_authority(workspace: Path) -> None:
    """Срок — стена: вчерашний эксперимент не полномочие на сегодня."""
    _marker(workspace, days=-1)
    assert load_sandbox_authority(workspace, env=_env(True)) is None


def test_a_marker_that_does_not_claim_sandbox_is_not_one(workspace: Path) -> None:
    """`sandbox: false` — это отказ, а не наличие файла."""
    _marker(workspace, sandbox=False)
    assert load_sandbox_authority(workspace, env=_env(True)) is None


def test_both_gestures_make_an_authority_with_a_named_reason(workspace: Path) -> None:
    """Включённое полномочие себя называет: срок, потолок и повод."""
    _marker(workspace, max_applies=7)
    authority = load_sandbox_authority(workspace, env=_env(True))

    assert authority is not None
    assert authority.max_applies_per_day == 7
    assert authority.reason
    assert authority.id.startswith("sandbox:")


# ── что песочнице можно и чего нельзя ───────────────────────────────────────


def _proposal(workspace: Path, rel: str):
    from core.self_apply_bridge import rehydrate_proposal

    payload = build_self_apply_payload(
        files=[{"path": rel, "content": "x = 1\n"}],
        reason="починка",
        origin="autonomous",
        workspace=workspace,
    )
    return rehydrate_proposal(payload)


def test_the_sandbox_may_change_its_own_code(workspace: Path) -> None:
    """Смысл режима: правка кода в изолированной копии без ожидания человека."""
    allowed, reason = sandbox_execution_verdict(
        _proposal(workspace, "core/foo.py"), workspace=workspace
    )
    assert allowed, reason


def test_the_sandbox_cannot_write_outside_itself(workspace: Path) -> None:
    """Выход за пределы копии — отказ, названный до всякой полосы."""
    allowed, reason = sandbox_execution_verdict(
        _proposal(workspace, "../outside.py"), workspace=workspace
    )
    assert not allowed
    assert "outside" in reason or "вне" in reason


def test_the_sandbox_cannot_move_its_own_fence(workspace: Path) -> None:
    """Забор не двигается изнутри: ни метка, ни модуль полномочия.

    Самоизменяющаяся граница — это отсутствие границы.
    """
    for rel in ("config/burn_in_sandbox.json", "core/burn_in_sandbox.py"):
        allowed, reason = sandbox_execution_verdict(
            _proposal(workspace, rel), workspace=workspace
        )
        assert not allowed, f"{rel} пропущен: песочница вправе снять сама себя"
        assert "fence" in reason or "забор" in reason or "sandbox" in reason


def test_the_sandbox_cannot_touch_secrets_or_ci(workspace: Path) -> None:
    """Запрещённые классы полосы остаются запрещёнными и в песочнице."""
    for rel in (".env", ".github/workflows/ci.yml"):
        allowed, _ = sandbox_execution_verdict(
            _proposal(workspace, rel), workspace=workspace
        )
        assert not allowed, f"{rel} пропущен"


def test_the_production_verdict_is_unchanged(workspace: Path) -> None:
    """Производственные ворота не тронуты: код без человека там не проходит."""
    from core.self_apply_lane import autonomous_execution_verdict

    allowed, reason = autonomous_execution_verdict(_proposal(workspace, "core/foo.py"))
    assert not allowed
    assert "not a document" in reason


# ── поведение слива под песочницей ──────────────────────────────────────────


def test_the_sandbox_applies_code_without_a_standing_grant(
    workspace: Path, lane: _Lane
) -> None:
    """Главный смысл: десять часов эксперимента не упираются в ящик человека.

    Без песочницы тот же слив отказал бы дважды — нет стоячего гранта и класс
    файла не документ.
    """
    from core.rule_approved_apply import drain_rule_approved_proposals

    inbox = _inbox(workspace)
    _marker(workspace)
    item = _pending_code(inbox, workspace, "core/foo.py")

    out = drain_rule_approved_proposals(workspace, dry_run=False, env=_env(True))

    assert out["applied"] == 1, out
    assert lane.item_ids == [item.id], "полоса обойдена — это был бы скрытый путь"
    assert inbox.get(item.id).status in {"approved", "executed", "aborted"}


def test_the_sandbox_is_off_without_the_env_flag(workspace: Path, lane: _Lane) -> None:
    """Та же заявка без второго жеста: обычный производственный отказ."""
    from core.rule_approved_apply import drain_rule_approved_proposals

    inbox = _inbox(workspace)
    _marker(workspace)
    _pending_code(inbox, workspace, "core/foo.py")

    out = drain_rule_approved_proposals(workspace, dry_run=False, env={})

    assert out["applied"] == 0
    assert out["blocked"] == "no active standing grant"
    assert lane.item_ids == []


def test_a_dry_run_sandbox_still_applies_nothing(workspace: Path, lane: _Lane) -> None:
    """Сухость старше полномочия: эксперимент не отменяет «ничего не менять»."""
    from core.rule_approved_apply import drain_rule_approved_proposals

    inbox = _inbox(workspace)
    _marker(workspace)
    _pending_code(inbox, workspace, "core/foo.py")

    out = drain_rule_approved_proposals(workspace, dry_run=True, env=_env(True))

    assert out["applied"] == 0
    assert out["blocked"] == "effects disabled"
    assert lane.item_ids == []


def test_the_sandbox_has_a_daily_ceiling(workspace: Path, lane: _Lane) -> None:
    """Полномочие без потолка — не полномочие, а состояние."""
    from core.rule_approved_apply import drain_rule_approved_proposals

    inbox = _inbox(workspace)
    _marker(workspace, max_applies=2)
    for name in ("a", "b", "c", "d"):
        _pending_code(inbox, workspace, f"core/{name}.py")

    out = drain_rule_approved_proposals(workspace, dry_run=False, env=_env(True))

    assert out["applied"] == 2, out
    assert len(lane.item_ids) == 2


def test_every_sandbox_attempt_is_logged(workspace: Path, lane: _Lane) -> None:
    """Прогон, о котором нечего прочитать, не эксперимент."""
    from core.rule_approved_apply import drain_rule_approved_proposals

    inbox = _inbox(workspace)
    _marker(workspace)
    _pending_code(inbox, workspace, "core/foo.py")
    _pending_code(inbox, workspace, ".env")

    events: list[tuple[str, dict]] = []
    drain_rule_approved_proposals(
        workspace, dry_run=False, env=_env(True),
        log=lambda e, p: events.append((e, p)),
    )

    kinds = [e for e, _ in events]
    assert "sandbox_authority_active" in kinds, "включённое полномочие не объявлено"
    assert any(k.startswith("rule_approval_refused") for k in kinds), (
        "отказ песочницы не записан"
    )
