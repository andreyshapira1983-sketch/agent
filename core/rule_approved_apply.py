"""Применение того, на что разрешение даёт ПРАВИЛО, а не человек.

Здесь замыкается петля для одного узкого класса продукта агента — новых
документов. Замер, отвергнутые варианты и границы: MIR-173.
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any


def drain_rule_approved_proposals(
    workspace: Path,
    *,
    dry_run: bool,
    log: Callable[[str, dict], None] | None = None,
) -> dict:
    """Применить предложения, которые правило разрешает без человека.

    Замер 2026-08-27 по живому ящику и по ИСТОРИИ, а не по сегодняшнему дереву:
    из 27 предложений полосы 11 на момент подачи трогали только новые файлы, и
    все 11 — документы. Каждое ждало человека, который откроет ящик; здесь и
    рвалась петля.

    Новых полномочий шаг не даёт: создавать файлы агент уже вправе через
    `file_write` — без тестов и без отката, — а полоса делает то же самое с
    прицельными тестами, полной батареей и автоматическим откатом.

    Двое ворот перед любым действием: эффекты включены И стоячий грант
    действует. Второе спрашивается ТОЙ ЖЕ функцией, что у рантайма: вторая
    проверка разошлась бы с первой (класс H-26 проспективного аудита).

    Просьба, разрешение и исполнение остаются тремя событиями (MIR-117,
    правило B): заявка уже лежит, разрешение записывается ОТ ИМЕНИ ПРАВИЛА, а
    не «unattributed», исполнение идёт отдельно и логируется.
    """
    from core.approval_inbox import DEFAULT_APPROVAL_INBOX_PATH, ApprovalInbox
    from core.autonomous_runtime import active_standing_grant
    from core.safe_vcs import SafeVCS
    from core.self_apply_bridge import (
        SELF_APPLY_OPERATION,
        rehydrate_proposal,
        run_approved_self_apply,
    )
    from core.self_apply_lane import autonomous_execution_verdict
    from tools.run_tests import RunTestsTool

    def _say(event: str, payload: dict) -> None:
        if log is not None:
            log(event, payload)

    out: dict[str, Any] = {"considered": 0, "applied": 0, "refused": 0, "blocked": ""}
    if dry_run:
        out["blocked"] = "effects disabled"
        return out
    inbox = ApprovalInbox(path=Path(workspace) / DEFAULT_APPROVAL_INBOX_PATH)
    grant = active_standing_grant(inbox, workspace)
    if grant is None:
        out["blocked"] = "no active standing grant"
        return out

    for item in list(inbox.pending()):
        if getattr(item, "operation", "") != SELF_APPLY_OPERATION:
            continue
        out["considered"] += 1
        try:
            proposal = rehydrate_proposal(item.payload)
        except Exception:  # noqa: BLE001 — негодная заявка не вправе ронять тик
            out["refused"] += 1
            continue
        allowed, reason = autonomous_execution_verdict(proposal)
        if not allowed:
            out["refused"] += 1
            _say("rule_approval_refused", {"approval_id": item.id, "reason": reason})
            continue
        inbox.approve(item.id, reason=reason, actor="rule:documents_only")
        result = run_approved_self_apply(
            inbox=inbox,
            item_id=item.id,
            workspace=Path(workspace),
            vcs=SafeVCS(workspace=Path(workspace)),
            test_runner=RunTestsTool(workspace_root=Path(workspace)),
        )
        out["applied"] += 1
        _say("rule_approved_applied", {
            "approval_id": item.id,
            "grant_id": getattr(grant, "id", ""),
            "status": result.get("status"),
            "reason": reason,
        })
    return out


def drain_and_log(workspace: Path, *, dry_run: bool, log_tick: Callable[[dict], None]) -> None:
    """Обёртка для живого пути кампании: след пишется ВСЕГДА, ошибки глотаются.

    Первая проводка (MIR-173) стояла в хвосте `run_tick`, а плановая задача
    туда не доходит: `--campaign` выходит через `run_paced_campaign`. Найдено
    по прогону 2026-08-27 11:31 — грант списан, событий петли ноль, последний
    `tick_complete` в журнале датирован 25 августа. Отсутствие события
    неотличимо от мёртвого кода — ровно так дефект и был пойман (MIR-175).
    """
    try:
        drain = drain_rule_approved_proposals(
            workspace, dry_run=dry_run,
            log=lambda e, p: log_tick({"event": e, **p}),
        )
        log_tick({"event": "rule_approved_drain", **drain})
    except Exception as exc:  # noqa: BLE001 — петля не вправе ронять кампанию
        log_tick({"event": "rule_approval_error", "error": type(exc).__name__})
