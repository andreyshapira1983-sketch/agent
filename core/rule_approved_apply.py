"""Применение того, на что разрешение даёт ПРАВИЛО, а не человек.

Здесь замыкается петля для одного узкого класса продукта агента — новых
документов. Замер, отвергнутые варианты и границы: MIR-173.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class _Authority:
    """Кто разрешает это применение, чем ограничен и что пропускает.

    Двое возможных: правило («только новые документы», под стоячим грантом
    оператора) и песочница («код внутри изолированной копии», под явным
    полномочием burn-in). Форма одна нарочно — тело слива не должно знать, под
    чьей властью работает, иначе вторая власть заводит вторую ветку, а вторая
    ветка расходится с первой.
    """

    id: str
    actor: str
    verdict: Callable[[Any], tuple[bool, str]]
    #: Занять одну единицу потолка. True — занято, можно применять; False —
    #: остатка нет. Решение и запись неразделимы (ревизия PR #333): пара
    #: «спросить остаток → записать расход» пропускала два одновременных слива.
    reserve: Callable[[], bool]
    #: Снимок остатка на входе. Только для быстрого отказа и журнала; истина о
    #: потолке живёт в `reserve`, и спорить надо с ней.
    remaining: int
    sandbox: bool


#: Умолчание набора стоков для слива. Это НЕ `None`: `None` на лестнице
#: означает «человек за клавиатурой, ограничений нет», а слив по своей природе
#: безнадзорен. Вызывающий, забывший назвать профиль, обязан получить самый
#: узкий, а не самый широкий ответ (ревизия PR #333).
_UNATTENDED: frozenset[str] = frozenset()


def _authority_for(
    workspace: Path, inbox: Any, *, env: Any, say: Callable[[str, dict], None]
) -> _Authority | None:
    """Действующая власть, или None, если разрешать некому.

    Песочница спрашивается первой; без включённого полномочия функция ведёт
    себя ровно как прежний код: стоячий грант или отказ.
    """
    from core.autonomous_runtime import (
        active_standing_grant,
        reserve_standing_grant_use,
        standing_grant_remaining_runs,
    )
    from core.burn_in_sandbox import (
        load_sandbox_authority,
        reserve_sandbox_apply,
        sandbox_applies_today,
        sandbox_execution_verdict,
    )
    from core.self_apply_lane import autonomous_execution_verdict

    sandbox = load_sandbox_authority(workspace, env=env)
    if sandbox is not None:
        say("sandbox_authority_active", sandbox.to_dict())
        return _Authority(
            id=sandbox.id,
            actor="sandbox:burn_in",
            verdict=lambda p: sandbox_execution_verdict(p, workspace=workspace),
            reserve=lambda: reserve_sandbox_apply(workspace, sandbox),
            remaining=sandbox.max_applies_per_day - sandbox_applies_today(workspace, sandbox),
            sandbox=True,
        )
    grant = active_standing_grant(inbox, workspace)
    if grant is None:
        return None
    grant_id = getattr(grant, "id", "")
    grant_cap = int((getattr(grant, "payload", None) or {}).get("max_runs_per_day") or 0)
    return _Authority(
        id=grant_id,
        actor="rule:documents_only",
        verdict=autonomous_execution_verdict,
        reserve=lambda: reserve_standing_grant_use(
            workspace, grant_id, max_per_day=grant_cap
        ),
        # Остаток на сегодня, а не потолок: журнал расхода общий с рантаймом.
        # Аудит автономности 2026-09-17 нашёл здесь проход без учёта — «3
        # прогона в день» означало 3 прогона рантайма ПЛЮС неограниченное
        # число применений правилом, то есть не означало ничего.
        remaining=standing_grant_remaining_runs(workspace, grant),
        sandbox=False,
    )


def _authorised_candidates(inbox: Any, authority: _Authority) -> list[Any]:
    """Retry only this sandbox's approvals; a human's approval is not its queue."""
    candidates = list(inbox.pending())
    if authority.sandbox:
        candidates += [
            item for item in inbox.list(status="approved")
            if item.decided_by == authority.actor
        ]
    return candidates


def stale_proposal_files(workspace, proposal) -> list[str]:
    """Пути заявки, чей файл на диске уже новее её, — ДО расхода полномочия.

    Живой слив 2026-09-20: правка оператора в core/step_sanitizer.py сделала
    вчерашнее предложение устаревшим; полоса честно отказала у своих базовых
    ворот, но единица гранта уже была списана — три цикла подряд сожгли грант
    до нуля, не сделав ничего и не запустив ни одного теста. Правило то же
    самое, что у полосы (`core.self_apply_lane._stale_changes`).
    """
    from core.self_apply_lane import _stale_changes

    return _stale_changes(Path(workspace), getattr(proposal, "files", ()))


def drain_rule_approved_proposals(
    workspace: Path,
    *,
    dry_run: bool,
    log: Callable[[str, dict], None] | None = None,
    env: Any = None,
    durable_writes: Any = _UNATTENDED,
) -> dict:
    """Применить предложения, которые правило разрешает без человека.

    Замер 2026-08-27 по живому ящику и по ИСТОРИИ, а не по сегодняшнему дереву:
    из 27 предложений полосы 11 на момент подачи трогали только новые файлы, и
    все 11 — документы. Каждое ждало человека, который откроет ящик; здесь и
    рвалась петля.

    Производственное правило не даёт новых полномочий: `file_write` уже умеет
    создавать файлы; полоса добавляет тесты и автоматический откат.

    Двое ворот перед любым действием: эффекты включены И стоячий грант
    действует. Второе спрашивается ТОЙ ЖЕ функцией, что у рантайма: вторая
    проверка разошлась бы с первой (класс H-26 проспективного аудита).

    Просьба, разрешение и исполнение остаются тремя событиями (MIR-117,
    правило B): заявка уже лежит, разрешение записывается ОТ ИМЕНИ ПРАВИЛА, а
    не «unattributed», исполнение идёт отдельно и логируется.

    ВТОРАЯ ВЛАСТЬ, и она названа. С 2026-09-17 то же место умеет действовать
    под полномочием песочницы (`core/burn_in_sandbox.py`) — явным, срочным,
    включаемым двумя независимыми жестами. Пока полномочие не включено, всё
    ниже идёт прежним путём буква в букву: другой ветки для производства здесь
    нет. Что меняет песочница — КТО разрешает и какой класс файлов проходит;
    чего она не меняет — сухость прогона (проверяется раньше всего), полосу
    применения, её тесты, ветку, откат и отсутствие push.
    """
    from core.approval_inbox import DEFAULT_APPROVAL_INBOX_PATH, ApprovalInbox
    from core.safe_vcs import SafeVCS
    from core.self_apply_bridge import (
        SELF_APPLY_OPERATION,
        TERMINAL_LANE_STATUSES,
        rehydrate_proposal,
        run_approved_self_apply,
    )
    from core.self_build_rules import blocking_lesson, record_lessons_from_result
    from tools.run_tests import RunTestsTool

    def _say(event: str, payload: dict) -> None:
        if log is not None:
            log(event, payload)

    out: dict[str, Any] = {
        "considered": 0, "applied": 0, "unapplied": 0, "attempted": 0,
        "refused": 0, "blocked": "",
    }
    if dry_run:
        out["blocked"] = "effects disabled"
        return out
    inbox = ApprovalInbox(path=Path(workspace) / DEFAULT_APPROVAL_INBOX_PATH)

    # Власть спрашивается ПОСЛЕ сухости: эксперимент вправе расширить класс
    # изменений, но не вправе отменить «ничего не менять».
    authority = _authority_for(workspace, inbox, env=env, say=_say)
    if authority is None:
        out["blocked"] = "no active standing grant"
        return out
    remaining = authority.remaining
    # Полномочие называет себя СВОИМ именем. Ревизия Copilot по PR #333:
    # исчерпанная песочница отказывала словами «стоячий грант» и событием
    # `standing_grant_exhausted` — разрешением другой природы, с другим сроком
    # и другим владельцем. Журнал, называющий не то разрешение, хуже молчания:
    # оператор идёт искать грант, которого нет.
    spent_word = "sandbox daily cap reached" if authority.sandbox else "standing grant spent for today"
    spent_event = "sandbox_cap_exhausted" if authority.sandbox else "standing_grant_exhausted"

    for item in _authorised_candidates(inbox, authority):
        if getattr(item, "operation", "") != SELF_APPLY_OPERATION:
            continue
        out["considered"] += 1
        if remaining <= 0:
            out["refused"] += 1
            out["blocked"] = spent_word
            _say(spent_event, {
                "approval_id": item.id, "grant_id": authority.id,
            })
            continue
        try:
            proposal = rehydrate_proposal(item.payload)
        except Exception:  # noqa: BLE001 — негодная заявка не вправе ронять тик
            out["refused"] += 1
            continue
        allowed, reason = authority.verdict(proposal)
        if not allowed:
            out["refused"] += 1
            _say("rule_approval_refused", {"approval_id": item.id, "reason": reason})
            continue
        # Опыт прошлых заходов — ворота, а не архив. Откат по этому адресу уже
        # случался: автомат не повторяет проверку, которую один раз провалил,
        # заявка остаётся в ящике для человека (MIR-173, находка 10 аудита
        # автономности 2026-09-17).
        targets = [getattr(f, "path", "") for f in getattr(proposal, "files", ())]
        lesson = blocking_lesson(workspace, targets)
        if lesson is not None:
            out["refused"] += 1
            out["blocked"] = out["blocked"] or "prior rollback lesson"
            _say("rule_approval_refused_by_lesson", {
                "approval_id": item.id,
                "scope": list(lesson.scope),
                "failure": lesson.failure[:200],
                "learned_at": lesson.created_at,
            })
            continue
        stale = stale_proposal_files(workspace, proposal)
        if stale:
            out["refused"] += 1
            out["blocked"] = out["blocked"] or "stale proposal"
            _say("rule_approval_refused_stale", {
                "approval_id": item.id, "stale_files": stale,
                "next_human_action": "rebuild the proposal against the current file",
            })
            continue
        # Расход занимается ДО применения и НЕРАЗДЕЛИМО с проверкой остатка:
        # прерванный тик должен оставить пережатую оценку расхода, а не
        # незамеченное полномочие, а два одновременных слива не должны
        # потратить одну и ту же последнюю единицу (ревизия PR #333).
        if not authority.reserve():
            out["refused"] += 1
            out["blocked"] = spent_word
            _say(spent_event, {
                "approval_id": item.id, "grant_id": authority.id,
            })
            continue
        remaining -= 1
        inbox.approve(item.id, reason=reason, actor=authority.actor)
        result = run_approved_self_apply(
            inbox=inbox,
            item_id=item.id,
            workspace=Path(workspace),
            vcs=SafeVCS(workspace=Path(workspace)),
            test_runner=RunTestsTool(workspace_root=Path(workspace)),
            # Sandbox authority is per proposal, not a veto from unrelated requests.
            approvals_pending=0 if authority.sandbox else None,
        )
        out["attempted"] += 1
        status = str(result.get("status") or "")
        # `applied` считает ПРИМЕНЁННОЕ, а не начатое. Ревизия PR #333: счётчик
        # рос на любом исходе, включая откат и обрыв, поэтому по журналу выходило
        # больше применений, чем изменений в дереве, и разница молча копилась.
        # Расход полномочия при этом остаётся потраченным — попытка стоила
        # прогона батареи, — а `attempted` хранит честный итог.
        if status == "committed_local":
            out["applied"] += 1
        else:
            out["unapplied"] += 1
        # Нетерминальный исход — про состояние МИРА, а не про заявку: очередь
        # одобрений, бюджет, шлюз. Следующая заявка упрётся в ту же стену,
        # заняв ещё единицу потолка. Ревизия Copilot по PR #333; замерено:
        # `core/self_apply_bridge._pending_excluding` считает ОСТАЛЬНЫЕ
        # ожидающие, поэтому слив из трёх заявок тратил три единицы и не
        # применял ничего. Заявка остаётся одобренной: песочница повторяет
        # собственное решение при следующем сливе, снова проверяя полномочие.
        # Производственное решение по-прежнему повторяет оператор.
        if status not in TERMINAL_LANE_STATUSES:
            out["blocked"] = out["blocked"] or f"lane refused: {status or 'unknown'}"
            _say("rule_approved_lane_wall", {
                "approval_id": item.id,
                "grant_id": authority.id,
                "status": status,
                "reason": str(result.get("reason") or "")[:200],
                "grant_runs_left": remaining,
            })
            break
        # Проверенный кандидат ПРЕДЪЯВЛЯЕТСЯ принимающему. Не принимается:
        # предъявление прав не даёт и голову опыта не двигает — оно лишь
        # сужает множество того, что `core/burn_in_supervisor.adopt_offer`
        # вообще станет рассматривать. Только в песочнице: производственный
        # путь никакого опыта не ведёт и предъявлять ему нечему.
        if authority.sandbox and status == "committed_local":
            _offer_to_supervisor(workspace, result, item_id=item.id, log=_say)
        # Исход становится знанием ЗДЕСЬ, без команды человека. До 2026-09-17
        # запись уроков жила ровно в одном месте — `cli/commands_self_apply.py`,
        # то есть опыт появлялся только когда за клавиатурой сидел человек.
        learned = record_lessons_from_result(
            workspace, result,
            origin="burn_in_sandbox" if authority.sandbox else "rule_approved_apply",
            reason=getattr(proposal, "reason", ""),
            # Тот же набор стоков, что у сборки агента: урок — долговременная
            # запись, и проходит он теми же воротами, а не мимо них.
            durable_writes=durable_writes,
            log=log,
        )
        _say("rule_approved_applied", {
            "approval_id": item.id,
            "grant_id": authority.id,
            "status": result.get("status"),
            "reason": reason,
            "grant_runs_left": remaining,
            "lessons_recorded": learned.get("lessons", 0),
            "rules_recorded": learned.get("rules", 0),
        })
    return out


def _offer_to_supervisor(
    workspace: Path, result: dict, *, item_id: str, log: Callable[[str, dict], None]
) -> None:
    """Положить SHA проверенного кандидата в реестр предложений.

    Best-effort по образцу соседей: сбой реестра не вправе ронять применение —
    но и не вправе пройти молча, иначе разомкнутая петля выглядит как
    замкнутая. Отсутствие события неотличимо от мёртвого кода.
    """
    sha = str(result.get("commit_hash") or "")
    try:
        from core.burn_in_supervisor import offer_verified_commit

        taken = offer_verified_commit(
            Path(workspace), sha=sha, proposal_id=item_id,
            tests_run=result.get("tests_run") or (),
            reason=str(result.get("reason") or ""),
        )
    except Exception as exc:  # noqa: BLE001 — реестр не роняет применение
        log("burn_in_offer_failed", {"approval_id": item_id,
                                     "error": type(exc).__name__})
        return
    log("burn_in_offer" if taken else "burn_in_offer_rejected",
        {"approval_id": item_id, "sha": sha})


def drain_and_log(
    workspace: Path,
    *,
    dry_run: bool,
    log_tick: Callable[[dict], None],
    durable_writes: Any = _UNATTENDED,
) -> None:
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
            durable_writes=durable_writes,
        )
        log_tick({"event": "rule_approved_drain", **drain})
    except Exception as exc:  # noqa: BLE001 — петля не вправе ронять кампанию
        log_tick({"event": "rule_approval_error", "error": type(exc).__name__})
