"""Campaign I/O helpers: journal writes, cost totals, and the default signal-gathering and action-executing callbacks.

Extracted from `core/campaign` by autonomous self-build module split.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from core.best_next_action import BestNextAction
from core.campaign_types import CampaignActionOutcome, CampaignConfig


def _log(agent: Any, event: str, payload: dict[str, Any]) -> None:
    log = getattr(agent, "log", None)
    if log is None:
        return
    try:
        log.log(event, payload)
    except (AttributeError, TypeError):
        pass


def _cost_totals(agent: Any) -> tuple[int, int]:
    try:
        usage_ledger = getattr(agent.model_router, "usage_ledger", None)
        ledger = getattr(usage_ledger, "budget_ledger", None)
        if ledger is None:
            return (0, 0)
        totals = ledger.snapshot().get("totals", {})
        return (int(totals.get("llm_calls", 0)), int(totals.get("model_cost_units", 0)))
    except (AttributeError, TypeError, ValueError):
        return (0, 0)


def _action_focused_goal(goal: str, action: BestNextAction) -> str:
    reason = (action.reason or "").strip()
    evidence = "; ".join(e for e in action.evidence[:3] if e)
    parts = [
        f"Campaign goal: {goal.strip()}.",
        f"The single highest-priority signal right now is '{action.action}' — {action.title}.",
    ]
    if reason:
        parts.append(f"Why it matters: {reason}")
    if evidence:
        parts.append(f"Evidence: {evidence}.")
    parts.append(
        "Decide the ONE most useful next step a human should take to move the goal forward, and justify it with the evidence. Reason read-only — do not perform any effects."
    )
    return " ".join(parts)


def _default_gather_signals(
    agent: Any, workspace: Any, approval_inbox: Any, goal: str = "",
) -> dict[str, Any]:
    from core.alert_ack import AlertAckStore
    from core.approval_inbox import ApprovalInbox
    from core.approval_triage import triage_inbox
    from core.best_next_action import select_best_next_action
    from core.heartbeat_io import (
        heartbeat_age_seconds,
        is_stale,
        read_heartbeat,
    )

    ws = Path(workspace)
    heartbeat = read_heartbeat(ws)
    age = heartbeat_age_seconds(heartbeat)
    hb = heartbeat or {}
    inbox = approval_inbox or ApprovalInbox(path=ws / "data" / "approval_inbox.jsonl")
    triage = triage_inbox(inbox.pending())
    ack_store = AlertAckStore(path=ws / "data" / "alert_acknowledgements.jsonl")
    acknowledged = ack_store.active_actions()
    # Собственный список дефектов. Без него непригляданный путь был слеп ровно
    # на то, ради чего заведён: живой прогон 2026-08-15 остановился с
    # «healthy_idle: nothing warrants action», держа ШЕСТЬ открытых
    # самонайденных дефектов. Кандидат под них в выбирателе есть
    # (`_candidate_open_self_improvement_issue`), но вход ему давал только
    # REPL — то есть путь, где человек и так смотрит.
    # Зачем: docs/CODE_NOTES.md, «The unattended path was the blind one».
    open_issues, registry_available = _open_self_improvement_issues(ws)
    # Предмет цели разрешается ЗДЕСЬ: файловая система знает рабочую область,
    # а таблица решений остаётся чистой (MIR-158).
    from pathlib import Path as _Path

    from core.best_next_action import resolve_goal_subject

    _root = _Path(ws)
    action = select_best_next_action(
        goal=goal,
        goal_subject=resolve_goal_subject(
            goal, exists=lambda rel: (_root / rel).is_file()
        ),
        result_status=str(hb.get("result_status", "none")),
        tests_health=str(hb.get("tests_health", "none")),
        dry_run_streak=int(hb.get("dry_run_streak", 0) or 0),
        heartbeat_missing=heartbeat is None,
        heartbeat_stale=is_stale(age),
        heartbeat_age_seconds=age,
        last_event=str(hb.get("event", "")),
        tick_error=hb.get("error"),
        triage=triage,
        inbox_pending=triage.total_pending,
        acknowledged=acknowledged,
        self_improvement_registry_available=registry_available,
        open_self_improvement_issues=open_issues,
    )
    return {"heartbeat": hb, "age": age, "triage": triage, "action": action}


def _propose_repair_from_diagnosis(
    *, agent: Any, workspace: Any, config: CampaignConfig,
    action: BestNextAction, answer: str, approval_inbox: Any,
) -> str | None:
    """Подтверждённый диагноз становится долговременной заявкой на ремонт.

    Четыре условия, каждое структурное, и все обязаны сойтись:

    * действие — про собственный дефект (`improve_failure_to_idea_pipeline`):
      health-pass и прочие действия ремонта не обещали;
    * не dry-run: заявка в очереди — долговременный эффект;
    * проверка ПОЛНОСТЬЮ подтвердила диагноз (тот же стандарт, что снимает
      прокси релевантности): частично обоснованный текст патча не заслуживает;
    * диагноз называет существующий .py в репозитории — существование
      спрашивается у диска (`workspace_paths_named`), не у регулярки: именно
      регулярка по прозе завела в реестр reasoning.py и citation.py.

    Дальше — существующие рубежи без изъятий: генератор со своими гейтами
    уверенности, заявка `self_apply_lane.run` в очереди, решение человека
    (§9), лента с полным pytest и откатом. Здесь ничего не исполняется.
    """
    if action.action != "improve_failure_to_idea_pipeline" or config.dry_run:
        return None
    if not answer or approval_inbox is None:
        return None
    ver = getattr(agent, "last_verification", None)
    examined = int(getattr(ver, "total_chunks", 0) or 0)
    verified = int(getattr(ver, "verified_chunks", 0) or 0)
    if examined == 0 or verified != examined:
        return None
    try:
        from core.workspace_reference import workspace_paths_named

        targets = [
            p for p in workspace_paths_named(answer)
            if p.endswith(".py") and not p.startswith("tests/")
        ]
    except Exception:  # noqa: BLE001 — извлечение адресов не роняет кампанию
        targets = []
    if not targets:
        return None
    target = targets[0]
    try:
        gen = agent.propose_repair(
            target_path=target,
            workspace_root=Path(workspace),
            extra_context=answer[:4000],
        )
        if not getattr(gen, "ok", False):
            status = getattr(gen, "status", "?")
            _log(agent, "campaign_repair_not_proposed", {
                "target": target, "status": status,
            })
            if status == "no_failing_tests":
                # Ветка А. Ремонтник чинит только красное, и это его принцип
                # («refusing to invent a repair»). Дефект без красного теста
                # сперва ЗАРАБАТЫВАЕТ тест: Stage A формулирует задачу и
                # падающий приёмочный тест, человек благословляет тест до
                # существования реализации. Замер: 6 попыток охоты, дважды
                # диагноз 4/4 и 5/5 — и оба раза честный отказ ремонтника.
                note = _propose_failing_test_from_diagnosis(
                    agent=agent, workspace=workspace, target=target,
                    answer=answer, approval_inbox=approval_inbox,
                )
                return f"repair_declined:{status}; {note}"
            return f"repair_declined:{status}"
        from core.self_apply_bridge import build_self_apply_payload

        prop = gen.proposal
        payload = build_self_apply_payload(
            files=[{"path": prop.path, "content": prop.proposed_content}],
            reason=(prop.reason or gen.diagnosis or "")[:500],
            evidence=tuple(gen.evidence or ())[:6],
            test_paths=tuple(prop.test_paths or ("tests",)),
            test_pattern=prop.test_pattern,
            origin="campaign_diagnosis",
        )
        dedup_key = f"self_apply:{prop.path}:campaign_diagnosis"
        collision = _dedup_verdict(approval_inbox, dedup_key)
        item = approval_inbox.add(
            operation="self_apply_lane.run",
            summary=f"campaign repair proposal for {prop.path}",
            risk="reversible",
            reasons=(f"diagnosis verified {verified}/{examined}",
                     f"target={prop.path}"),
            payload=payload,
            dedup_key=dedup_key,
        )
        # F-1: при столкновении в ящике остаётся ПРЕЖНЯЯ заявка, и назвать это
        # предложением значило бы записать работу, которой не было.
        _log(agent, "campaign_repair_superseded" if collision
             else "campaign_repair_proposed", {
            "approval_id": item.id, "target": prop.path,
            "confidence": gen.confidence,
            **({"collision": collision} if collision else {}),
        })
    except Exception as exc:  # noqa: BLE001 — провод не вправе ронять кампанию
        _log(agent, "campaign_repair_proposal_failed", {
            "target": target, "error": str(exc)[:200],
        })
        return None
    else:
        if collision:
            return f"repair_{collision}"
        return f"repair_proposed:{item.id}"


def _unwrap_outer_fence(text: str) -> str:
    """Снять ОДНУ внешнюю markdown-ограду, если модель обернула ею весь ответ.

    Живой грех первого черновика (ain_848a6f8f, 2026-08-15): ```md вокруг
    всего документа вопреки инструкции. Внутренние ограды не трогаются.
    """
    lines = text.strip().splitlines()
    if len(lines) >= 2 and lines[0].startswith("```") and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    return text


def _dedup_verdict(approval_inbox, dedup_key: str) -> str | None:
    """Строка о СТОЛКНОВЕНИИ, если заявка с таким ключом уже ждёт человека.

    F-1 в docs/audit/FIELD_CHECK_QUEUE.md. `add` при совпадении ключа молча
    возвращает существующую заявку, и кампания писала в журнал «предложено» с
    длиной своего нового текста, тогда как в ящике лежал старый. Потеря второго
    черновика ограничена сроком жизни заявки; ложь в журнале не ограничена
    ничем, и чинится именно она.
    """
    try:
        existing = approval_inbox.find_pending_by_dedup_key(dedup_key)
    except AttributeError:  # ящик без этого метода — старый вызывающий
        return None
    if existing is None:
        return None
    return f"superseded_by_existing:{existing.id}"


def _propose_engineering_step(
    *, agent: Any, workspace: Any, approval_inbox: Any, target: str | None = None,
) -> str:
    """Turn the top real backlog candidate into a self-build lane proposal.

    The road charter → backlog (2026-08-19): the campaign may now PRODUCE an
    engineering approval item; blessing, the lane and every downstream gate
    stay human-owned. A refusal is surfaced by name, never hidden."""
    from pathlib import Path as _Path

    from core.budget_kill_switch import BudgetKillSwitch, default_path
    from core.safe_vcs import SafeVCS
    from core.self_build_producer import produce_self_apply_proposal

    try:
        kill_state = BudgetKillSwitch(
            path=default_path(_Path(workspace))).status(None)
    except Exception:  # noqa: BLE001 — статус рубильника не роняет шаг
        kill_state = None
    try:
        report = produce_self_apply_proposal(
            workspace=workspace,
            inbox=approval_inbox,
            llm=agent.model_router.for_role("synthesizer"),
            vcs=SafeVCS(workspace=_Path(workspace)),
            kill_switch=kill_state,
            max_builder_attempts=2,
            # Дорога головы к рукам (MIR-159). `None` значит «цель предмета не
            # называла» — тогда производитель выбирает сам, как и раньше.
            candidate_targets=(target,) if target else None,
        )
    except Exception as exc:  # noqa: BLE001 — отказ именуется, не прячется
        _log(agent, "campaign_engineering_error", {
            "error_type": type(exc).__name__, "error": str(exc)[:200],
        })
        return f"engineering_error:{type(exc).__name__}"
    status = str(getattr(report, "status", "?"))
    approval_id = str(getattr(report, "approval_id", "") or "")
    _log(agent, "campaign_engineering_proposed", {
        "status": status, "approval_id": approval_id,
        "target": str(getattr(report, "target_path", "") or ""),
    })
    if approval_id:
        return f"engineering_proposed:{approval_id}"
    return f"engineering_declined:{status}"


def _propose_doctrine_draft(
    *, agent: Any, workspace: Any, goal: str, approval_inbox: Any,
) -> str | None:
    """Документная цель становится черновиком в очереди — не файлом на диске."""
    from core.best_next_action import doc_target_from_goal

    target = doc_target_from_goal(goal)
    if not target:
        return "doc_declined:no_target_doc"
    existing = ""
    target_file = Path(workspace) / target
    if target_file.exists():
        # Наполнять разрешено ТОЛЬКО файл, сам несущий баннер DRAFT в шапке
        # (скелет -> протокол, команда оператора 2026-08-15). Действующий
        # документ без баннера — отдельное решение, не черновик.
        existing = target_file.read_text(encoding="utf-8")
        if "DRAFT" not in "\n".join(existing.splitlines()[:5]):
            return "doc_declined:doc_exists"
    charter = Path(workspace) / "knowledge" / "doctrine" / "future" / "CORPORATE_MODEL.md"
    charter_text = charter.read_text(encoding="utf-8") if charter.is_file() else ""
    system = (
        "You are drafting a doctrine document for your own repository. Write "
        "the COMPLETE markdown document and nothing else. It must open with a "
        "STATUS banner naming itself DRAFT / TARGET (not implemented), honor "
        "the charter's hard invariants (human-reserved authority stays), and "
        "never claim a capability exists in code unless you can name the module."
        + (
            # Живой замер 2026-08-15 (ain_2edf62f4): «наполнение» совпало со
            # скелетом на 89% — модель пересказала требования вместо содержания.
            " You are FILLING an existing skeleton: do NOT restate its section "
            "requirements or normative 'must include/define' rules — REPLACE "
            "each of them with the actual content it demands: real tables with "
            "filled rows, concrete decision rules, named phases with their "
            "conditions. A sentence that describes what the section should "
            "contain, instead of containing it, is a failure."
            if existing else ""
        )
    )
    user = (
        f"Document to draft: {target}\nGoal: {goal}\n\n"
        + (
            "Current DRAFT skeleton to FILL — keep its structure and write "
            f"the complete content it demands:\n{existing}\n\n"
            if existing else ""
        )
        + (f"The charter it serves:\n{charter_text}" if charter_text else "")
    )
    try:
        draft = str(agent.llm.complete(
            system=system, user=user, max_tokens=6000, temperature=0.4,
        ) or "").strip()
    except Exception as exc:  # noqa: BLE001 — провод не вправе ронять кампанию
        _log(agent, "campaign_doc_draft_failed", {"target": target,
                                                  "error": str(exc)[:200]})
        return "doc_declined:generation_error"
    if not draft:
        return "doc_declined:empty_draft"
    draft = _unwrap_outer_fence(draft)
    from core.self_apply_bridge import build_self_apply_payload

    payload = build_self_apply_payload(
        files=[{"path": target, "content": draft + ("\n" if not draft.endswith("\n") else "")}],
        reason=f"charter campaign goal: {goal[:300]}",
        evidence=(f"goal: {goal[:200]}", f"target: {target}"),
        test_paths=("tests",),
        test_pattern=None,
        origin="campaign_doctrine_draft",
    )
    dedup_key = f"self_apply:{target}:campaign_doctrine_draft"
    collision = _dedup_verdict(approval_inbox, dedup_key)
    item = approval_inbox.add(
        operation="self_apply_lane.run",
        summary=f"doctrine draft for {target}",
        risk="reversible",
        reasons=(f"goal-driven doctrine draft, {len(draft)} chars",),
        payload=payload,
        dedup_key=dedup_key,
    )
    # F-1: `chars` описывал НОВЫЙ черновик, а в ящике при столкновении лежал
    # старый — запись говорила о предложении, которого не было.
    _log(agent, "campaign_doc_draft_superseded" if collision
         else "campaign_doc_draft_proposed", {
        "approval_id": item.id, "target": target, "chars": len(draft),
        **({"collision": collision} if collision else {}),
    })
    if collision:
        return f"doc_draft_{collision}"
    return f"doc_draft_proposed:{item.id}"


#: Инлайн-цитаты веба в проверенном ответе — единственный источник ссылок
#: гипотезы: живой урок конденсатора 2026-08-16 — модель выдумала «Page 12».
#: Обе орфографии: сырая [web:x] и переписанная верификатором
#: [verified:web:x]/[topic-only:web:x] — жнец работает ПОСЛЕ проверки (живой
#: обрыв цепочки на первом прогоне автомата: no_web_citations при
#: состоявшемся чтении).
_WEB_CITATION_RE = re.compile(
    r"\[(?:verified:|topic-only:)?(?:web|web_fetch|web_search)[:\s]([^\]]{4,200})\]"
)


def _propose_hypothesis_from_study(
    *, agent: Any, workspace: Any, goal: str, answer: str,
) -> str:
    """Чтение внешнего мира оставляет ГИПОТЕЗУ нижней ступени — не истину."""
    refs = tuple(dict.fromkeys(
        m.group(1).strip() for m in _WEB_CITATION_RE.finditer(answer or "")
    ))
    if not refs:
        # Прибор: молчаливый отказ нельзя расследовать (урок route_reason,
        # 2026-08-16) — голова полученного текста едет в журнал.
        _log(agent, "campaign_hypothesis_declined", {
            "reason": "no_web_citations",
            "answer_chars": len(answer or ""),
            "answer_head": " ".join((answer or "").split())[:220],
            "token_counts": {tok: (answer or "").count(tok) for tok in (
                "[verified:", "[topic-only:", "[unverified", "[web",
            )},
        })
        return "hypothesis_declined:no_web_citations"
    try:
        raw = str(agent.llm.complete(
            system=(
                "From the analysis below produce EXACTLY four lines:\n"
                "ГИПОТЕЗА: <one testable claim - which external idea could "
                "improve which SPECIFIC mechanism of THIS system (name the "
                "module)>\nПРОВЕРКА: <which experiment or measurement decides "
                "it>\nИСТОЧНИК: <the page you rely on>\nСТАТУС: не проверено"
            ),
            user=(answer or "")[:4000], max_tokens=400, temperature=0.3,
        ) or "")
    except Exception as exc:  # noqa: BLE001 — провод не вправе ронять кампанию
        _log(agent, "campaign_hypothesis_failed", {"error": str(exc)[:200]})
        return "hypothesis_declined:condenser_error"
    lines = {ln.split(":", 1)[0].strip().upper(): ln.split(":", 1)[1].strip()
             for ln in raw.splitlines() if ":" in ln}
    hypothesis = lines.get("ГИПОТЕЗА", "")
    if not hypothesis:
        return "hypothesis_declined:no_block"
    from core.causal_claim_store import save_claim
    from core.causal_lesson import CausalClaim, Observation

    trace_id = str(getattr(getattr(agent, "log", None), "trace_id", "") or "")
    claim = CausalClaim(observation=Observation(
        episode_id=f"ep-study-{hashlib.sha256(goal.encode('utf-8')).hexdigest()[:12]}",
        trace_id=trace_id,
        run_id="campaign_study",
        defect_signals=("external_idea_candidate",),
        evidence_refs=tuple(f"web:{r}" for r in refs[:6]),
        observed_mismatch=(
            f"{hypothesis} || Проверка, названная автором: "
            f"{lines.get('ПРОВЕРКА', '')}"
        ),
    ))
    key = save_claim(claim, workspace=workspace)
    _log(agent, "campaign_hypothesis_recorded", {
        "claim_key": key, "sources": list(refs[:3]),
    })
    return f"hypothesis_recorded:{key}"


def _propose_failing_test_from_diagnosis(
    *, agent: Any, workspace: Any, target: str, answer: str, approval_inbox: Any,
) -> str:
    """Диагноз без красного теста едет в Stage A — за тестом, не за патчем."""
    from types import SimpleNamespace

    from core.self_task_producer import produce_coding_task

    try:
        from core.safe_vcs import SafeVCS

        vcs = SafeVCS(Path(workspace))
    except Exception:  # noqa: BLE001 — без VCS гейт дерева просто не спросится
        vcs = None
    try:
        report = produce_coding_task(
            workspace=workspace,
            inbox=approval_inbox,
            llm=getattr(agent, "llm", None),
            vcs=vcs,
            task_selector=lambda: SimpleNamespace(
                target_path=target,
                problem_quote=" ".join(answer.split())[:400],
                evidence_ref=f"verified_diagnosis:{getattr(getattr(agent, 'log', None), 'trace_id', '?')}",
            ),
            source_kind="verified_diagnosis",
        )
    except Exception as exc:  # noqa: BLE001 — провод не вправе ронять кампанию
        _log(agent, "campaign_test_proposal_failed", {
            "target": target, "error": str(exc)[:200],
        })
        return "test_proposal_error"
    status = getattr(report, "status", "?")
    if status == "proposed":
        _log(agent, "campaign_test_proposed", {
            "approval_id": report.approval_id, "target": target,
        })
        return f"test_proposed:{report.approval_id}"
    _log(agent, "campaign_test_not_proposed", {
        "target": target, "status": status,
        "reason": str(getattr(report, "reason", ""))[:160],
    })
    return f"test_declined:{status}"


def _open_self_improvement_issues(workspace: Path) -> tuple[tuple[dict, ...], bool]:
    """Открытые самонайденные дефекты и признак «реестр вообще читается»."""
    try:
        from core.self_improvement_issues import SelfImprovementIssueRegistry

        registry = SelfImprovementIssueRegistry(
            path=workspace / "data" / "self_improvement_issues.jsonl"
        )
        return tuple(i.to_dict() for i in registry.unresolved()), bool(registry.list())
    except Exception:  # noqa: BLE001 — совет важнее, чем причина его неполноты
        return (), False


def _execute_daemon_liveness_probe(workspace: Any) -> CampaignActionOutcome:
    """MIR-070: answer `restore_daemon_liveness` by READING ACTUAL STATE."""
    from core.heartbeat_io import (
        HEARTBEAT_PATH,
        heartbeat_age_seconds,
        is_stale,
        read_heartbeat,
    )

    ws = Path(workspace)
    heartbeat = read_heartbeat(ws)
    age = heartbeat_age_seconds(heartbeat)
    if heartbeat is None:
        verdict = (
            "Пульса нет вообще — демон никогда не тикал в этом workspace "
            "(файл отсутствует)."
        )
        step = "Запустить один тик: agent_tick.py --workspace . ; для постоянной жизни — scripts/install_daemon.ps1"
        confidence = "высокая (файл фактически отсутствует)"
    elif age is None:
        # The file exists but carries no readable timestamp — saying
        # «0.0 мин назад» here would be a lie about a broken record.
        verdict = (
            "Файл пульса есть, но повреждён или без метки времени — возраст "
            "тика неизвестен."
        )
        step = "Запустить один тик: agent_tick.py --workspace . — свежий тик перепишет файл; для постоянной жизни — scripts/install_daemon.ps1"
        confidence = "высокая в том, что файл нечитаем; возраст неизвестен"
    elif is_stale(age):
        age_min = age / 60.0
        verdict = (
            f"Пульс протух: последний тик {age_min:.1f} мин назад "
            f"(event={heartbeat.get('event', '?')}) — тики не идут по расписанию."
        )
        step = "Запустить один тик: agent_tick.py --workspace . ; для постоянной жизни — scripts/install_daemon.ps1"
        confidence = "высокая (возраст прочитан из файла пульса)"
    else:
        age_s = age or 0
        verdict = (
            f"Пульс свежий: последний тик {age_s:.0f} с назад "
            f"(event={heartbeat.get('event', '?')}) — демон жив; сигнал будет снят на следующем сборе."
        )
        step = "Не требуется — сигнал снимется на следующем сборе."
        confidence = "высокая (свежесть прочитана из файла пульса)"
    artifact = (
        f"{verdict}\n"
        f"Проверял: жив ли автономный демон.\n"
        f"Способ: чтение фактического состояния — файл {HEARTBEAT_PATH} "
        f"(то же окно, из которого поднят сигнал), без вызова модели.\n"
        f"Доказательство: возраст пульса = "
        f"{'нет файла' if age is None else f'{age:.0f} с'}; порог свежести — "
        f"интервал тика × коэффициент из core/heartbeat_io.\n"
        f"Непроверенным осталось: жив ли планировщик/процесс сам по себе и не "
        f"падали ли ранние тики молча — это видно только изнутри тика.\n"
        f"Уверенность: {confidence}\n"
        f"Шаг оператора: {step}"
    )
    return CampaignActionOutcome(
        result="completed",
        llm_calls_spent=0,
        cost_units_spent=0,
        artifact=artifact,
    )


def _default_execute_action(
    *,
    agent: Any,
    workspace: Any,
    action: BestNextAction,
    config: CampaignConfig,
    approval_inbox: Any = None,
) -> CampaignActionOutcome:
    # MIR-070: liveness is answerable from state — never spend a model on it.
    if action.action == "restore_daemon_liveness":
        return _execute_daemon_liveness_probe(workspace)

    from core.autonomous_runtime import AutonomousRuntime, AutonomousRuntimeConfig
    from core.budget_governor import BudgetLimits

    llm_before, cost_before = _cost_totals(agent)
    focused_goal = _action_focused_goal(config.goal, action)
    runtime = AutonomousRuntime(agent, workspace=workspace, approval_inbox=approval_inbox)
    report = runtime.run(
        AutonomousRuntimeConfig(
            goal=focused_goal,
            dry_run=config.dry_run,
            limit=3,
            include_tests=False,
            include_goal=True,
            budgets=BudgetLimits(max_agent_runs=1),
            enable_reflection=False,
            # Учебному действию открывается ровно веб (узкая разблокировка,
            # решение оператора 2026-08-16; поле пересекается с
            # _UNBLOCKABLE_TOOLS и ничего другого открыть не может).
            unblock_tools=(
                frozenset({"web_search", "web_fetch"})
                if action.action == "study_external_source" else frozenset()
            ),
        )
    )
    llm_after, cost_after = _cost_totals(agent)
    pending = 0
    try:
        pending = int(report.approvals.get("pending", 0) or 0)
    except (AttributeError, TypeError, ValueError):
        pending = 0
    proposal = f"approvals_pending={pending}" if pending else None
    artifact = None
    goal_answer = ""
    for task_report in getattr(report, "tasks", []) or []:
        if getattr(task_report.task, "kind", "") == "goal":
            answer = (task_report.details or {}).get("answer")
            if answer:
                goal_answer = str(answer)
                digest = " ".join(goal_answer.split())[:160]
                if digest:
                    artifact = f"reasoning: {digest}"
            break
    # Переход «диагноз -> ремонт». До 2026-08-15 подтверждённый диагноз умирал
    # здесь в 160-значном дайджесте: четвёртый прогон дня процитировал свой
    # дефект из настоящей трассы, получил 6 из 6 подтверждённых — и кампания
    # выбросила это, как и три прогона до него.
    # Зачем и границы: docs/CODE_NOTES.md, «A diagnosis that dies in a digest».
    repaired = _propose_repair_from_diagnosis(
        agent=agent, workspace=workspace, config=config,
        action=action, answer=goal_answer, approval_inbox=approval_inbox,
    )
    if repaired:
        proposal = f"{proposal}; {repaired}" if proposal else repaired
    # Учебные руки: чтение внешнего мира оставляет гипотезу нижней ступени.
    if action.action == "study_external_source" and not config.dry_run:
        studied = _propose_hypothesis_from_study(
            agent=agent, workspace=workspace, goal=config.goal,
            answer=goal_answer,
        )
        if studied:
            proposal = f"{proposal}; {studied}" if proposal else studied
    # Документные руки: цель, просящая документ доктрины, рождает черновик
    # заявкой в очередь — см. _propose_doctrine_draft.
    if action.action == "draft_doctrine_document" and not config.dry_run:
        drafted = _propose_doctrine_draft(
            agent=agent, workspace=workspace, goal=config.goal,
            approval_inbox=approval_inbox,
        )
        if drafted:
            proposal = f"{proposal}; {drafted}" if proposal else drafted
    # Инженерные руки: дорога хартия → бэклог (2026-08-19). Продукт — заявка
    # ленты, все ворота ниже по течению стоят как стояли.
    if action.action == "propose_engineering_task" and not config.dry_run:
        # Предмет берётся из РЕШЕНИЯ, а если решение его не назвало — из цели
        # тем же существованием в рабочей области (MIR-158/159). Голова и руки
        # связаны значением, а не совпадением тика.
        from core.best_next_action import resolve_goal_subject

        _ws = Path(workspace)
        engineered = _propose_engineering_step(
            agent=agent, workspace=workspace, approval_inbox=approval_inbox,
            target=action.target_path or resolve_goal_subject(
                config.goal, exists=lambda rel: (_ws / rel).is_file()
            ),
        )
        if engineered:
            proposal = f"{proposal}; {engineered}" if proposal else engineered
    return CampaignActionOutcome(
        result=report.status,
        llm_calls_spent=max(0, llm_after - llm_before),
        cost_units_spent=max(0, cost_after - cost_before),
        proposal=proposal,
        artifact=artifact,
    )
