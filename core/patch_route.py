"""Самопочинка без человека в петле: дефект → правка → patch_check → свой код.

Слово оператора 2026-09-22: «автомат — это когда он работает сам и зовёт меня
только ради денег или настоящего решения». Замер того дня: за четыре дня из
его заявок на правку дошли до кода две; цели «самоулучшения» кончались
разборами и прозой, а путь применения ждал человека на каждой правке.

Путь: драйв самоулучшения берёт открытый дефект из реестра (`defect_goal`);
цель требует файл правки proposals/selffix/<дефект>/edits.txt и зелёный
patch_check; после цели рантайм САМ прогоняет patch_check с полным набором
(`settle_patch`) — агенту на слово не верит — и зелёную правку ставит штатный
путь применения (ветка, свои тесты, откат при провале), после чего основная
ветка перематывается на его коммит. Дефект закрывается с уликой-коммитом.

Стены (прежние правила оператора, не одобрение каждой правки): правка не
трогает деньги, ключи, выход наружу, политику, одобрения и сам этот путь
(`_FORBIDDEN`); не больше `DAILY_CAP` правок в сутки; всё пишется в
data/self_repair_log.jsonl — сводка для человека после факта.
"""
from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

DAILY_CAP = 5
#: Пометка дефекта, который правкой кода не закрывается (среда, доступ, деньги):
#: его решает человек, и путь самопочинки такие пропускает.
OPERATOR_DECISION = "operator_decision"
RETRY_AFTER = timedelta(hours=6)
STATE_RELPATH = "data/patch_route_state.json"
LOG_RELPATH = "data/self_repair_log.jsonl"
_PATCH_RE = re.compile(r"proposals/selffix/[\w.\-]+/edits\.txt")
#: Куда самопочинка не входит: деньги и бюджет, ключи и редактирование
#: секретов, выход наружу, политика и одобрения, сам путь применения и этот
#: модуль, сборка агента.
_FORBIDDEN = (
    "core/approval", "core/policy", "core/secret", "core/redaction", "core/dlp",
    "core/budget", "core/standing_grant", "core/self_apply", "core/patch_route",
    "core/actuation_gateway", "core/kill", "core/safe_vcs", "tools/web_", "tools/shell_exec",
    "tools/patch_check", "tools/run_tests", "app/", "cli/", ".env", ".git",
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _state(root: Path) -> dict[str, Any]:
    try:
        return json.loads((root / STATE_RELPATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"attempted": {}, "applied": {}}


def _save_state(root: Path, state: dict[str, Any]) -> None:
    path = root / STATE_RELPATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def _log(root: Path, event: dict[str, Any]) -> None:
    path = root / LOG_RELPATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": _now().isoformat(), **event}, ensure_ascii=False) + "\n")


#: Через сколько неизменный пропуск дефекта записывается в сводку снова.
SKIP_LOG_REPEAT = timedelta(hours=24)


def _log_skip_once(root: Path, state: dict[str, Any], now: datetime, fingerprint: str, title: str) -> None:
    """«Дефект без задачи пропущен» — один раз, а не на каждом цикле.

    Сводка для человека получала ту же строку при каждом выборе цели: замер
    24.09 — 396 из 408 строк data/self_repair_log.jsonl повторы, одна ×102.
    Новая строка — только если пропуск новый, заголовок изменился или прошли
    сутки (repeat_interval Alertmanager: неизменное повторять редко, изменение —
    сразу). Сводку никто не пересчитывает, поэтому редкий повтор ничего не ломает.
    """
    seen = state.setdefault("skip_logged", {}).get(fingerprint) or {}
    try:
        fresh = now - datetime.fromisoformat(seen.get("at", "")) < SKIP_LOG_REPEAT
    except ValueError:
        fresh = False
    if fresh and seen.get("title") == title:
        return
    _log(root, {"event": "defect_without_a_task_skipped", "issue": fingerprint, "title": title})
    state["skip_logged"][fingerprint] = {"at": now.isoformat(), "title": title}
    _save_state(root, state)


def _slug(fingerprint: str) -> str:
    return re.sub(r"[^\w.\-]+", "-", fingerprint)[:60].strip("-") or "defect"


def defect_goal(root: Path) -> Any:
    """Цель: починить один открытый дефект реестра правкой с зелёным patch_check."""
    from core.defect_intake import intake_observations
    from core.drive_goal import DriveGoal
    from core.self_improvement_issues import DEFAULT_ISSUE_PATH, SelfImprovementIssueRegistry

    state, now = _state(root), _now()

    registry = SelfImprovementIssueRegistry(root / DEFAULT_ISSUE_PATH)
    # Приём своих сигналов живёт здесь, а не в команде одобрения: проверка
    # 2026-09-22 перед прогоном показала, что `sync_self_improvement_issue_registry`
    # зовёт только cli/commands_approval.py, а в автомате кампании этот путь не
    # проходит ни разу — починенный накануне корень был там мёртв.
    try:
        intake_observations(root, registry)
    except Exception as exc:  # noqa: BLE001 — приём не должен рушить выбор цели
        _log(root, {"event": "defect_intake_failed", "error": f"{type(exc).__name__}: {exc}"})
    for issue in registry.unresolved():
        # Дефект среды (поставить пакет, дать доступ) правкой кода не
        # закрывается: 2026-09-22 18:40 цикл ушёл на «в пробе нет numpy».
        if issue.action == OPERATOR_DECISION:
            continue
        # Дефект без НАЗВАННОЙ задачи правкой не закрывается: цель выходит
        # «Почини дефект X. Что делать: » — и агент, не зная, что менять,
        # пишет отписку или мусор. Замер ночи на 2026-09-23: из шестнадцати
        # открытых дефектов трое были без задачи, и ровно они дали ТРИ
        # последние красные правки подряд («объяснил, почему не могу»,
        # слипшиеся разделители на 305 КБ, тест поверх существующего файла).
        # Такому дефекту сначала нужна формулировка — это работа человека
        # или отдельного хода, а не слепая правка.
        if not str(issue.suggested_next_action or "").strip():
            _log_skip_once(root, state, now, issue.fingerprint, (issue.title or "")[:120])
            continue
        tried = state["attempted"].get(issue.fingerprint)
        if tried and now - datetime.fromisoformat(tried) < RETRY_AFTER:
            continue
        patch = f"proposals/selffix/{_slug(issue.fingerprint)}/edits.txt"
        state["attempted"][issue.fingerprint] = now.isoformat()
        state.setdefault("patch_to_defect", {})[patch] = issue.fingerprint
        _save_state(root, state)
        evidence = "; ".join(issue.evidence[:3])[:900]
        # «Меняется ровно 1 файл» — иначе ворота уточнения (core/loop_gates.py)
        # отвечают вопросом вместо работы, а в автомате на вопрос никто не
        # ответит: 2026-09-22 18:21 три цикла подряд ушли в clarify.
        # Живой прогон в ночь на 2026-09-23 дал три красных подряд и два
        # разных промаха, оба лечатся словом в задании:
        #   * в файл правки легла ОТПИСКА — «шаг 1 не вернул точный текст строк
        #     150-172, поэтому SEARCH собрать не могу». Это его же открытый
        #     дефект «объяснил стену вместо того, чтобы её проверить»: не
        #     хватило чтения — надо дочитать, а не объяснять;
        #   * разделители слиплись в одну строку («<<<<<<< LINES 95-135 /
        #     >>>>>>> REPLACE»), и 305 КБ содержимого ушли в мусор. Форма
        #     показана дословно, чтобы её не приходилось угадывать.
        goal = (f"Почини свой дефект «{issue.title}» ({issue.fingerprint}). Улики: {evidence}. "
                f"Что сделать: {issue.suggested_next_action[:400]}. "
                f"Меняется ровно 1 файл: {patch}. Все остальные файлы только читаются. "
                f"В {patch} пиши ТОЛЬКО блоки правки — каждая метка на своей строке:\n"
                "FILE:core/имя.py\n<<<<<<< SEARCH\n<старый текст дословно>\n=======\n"
                "<новый текст>\n>>>>>>> REPLACE\n"
                "Вместо SEARCH можно «<<<<<<< LINES a-b» с номерами строк из file_read. "
                "Новый файл (тест) — блок с ПУСТЫМ SEARCH. Свой тест обязателен: правка без "
                "теста не зелёная. "
                "Если чтение не дало точного текста для SEARCH — ПРОЧИТАЙ ЕЩЁ РАЗ нужные строки "
                "(file_read с номерами) и собери блок по прочитанному. Объяснение, почему не "
                f"вышло, в {patch} не кладётся: это не правка, и проверка её отвергнет. "
                f"Затем вызывай patch_check с путём {patch}, пока verdict не станет green. "
                "Не трогай деньги, ключи, политику, одобрения и сеть — такие правки не лягут.")
        return DriveGoal(status="proposed", goal=goal, success_check=f"Файл {patch} создан",
                         drive="self_improvement_need", action="pursue_goal")
    return None


def patch_goal_verdict(root: Path | str, success_check: str) -> dict[str, Any] | None:
    """Вердикт цели самопочинки: правка настоящая или файл ради файла.

    Замер 2026-09-23, первая же живая цель после разблокировки: агент создал
    `proposals/selffix/sii_5dab4ac83cc87892/edits.txt` с пустым каркасом
    блоков — ни одной строки замены. Файловый судья сказал «verified», потому
    что критерий требовал ровно «файл создан», а patch_check в ту же минуту
    сказал «red: text outside blocks». Два прибора о той же работе, и наружу
    шёл тот, который меряет не то.

    Здесь вердикт ставит сам patch_check — без полного набора, без применения:
    пустышка и неприменимая правка не проходят, а настоящая ещё встретит
    полный набор в `settle_patch`. `None` — цель не про правку, судит обычный.
    """
    match = _PATCH_RE.search(success_check or "")
    if not match:
        return None
    from tools.patch_check import PatchCheckTool

    rel, root = match.group(0), Path(root)
    if not (root / rel).is_file():
        return {"verdict": "missing", "reason": f"файла правки {rel} нет",
                "artifacts_named": [rel], "artifacts_observed": []}
    try:
        check = PatchCheckTool(workspace_root=root).run(path=rel, full=False)
    except Exception as exc:  # noqa: BLE001 — сломанный инструмент не судит
        return {"verdict": "unverifiable", "reason": f"patch_check не отработал: {exc}"[:200],
                "artifacts_named": [rel], "artifacts_observed": [rel]}
    green = check.get("verdict") == "green"
    why = str(check.get("why") or "")
    errors = "; ".join(str(e) for e in (check.get("errors") or ()))[:200]
    return {
        "verdict": "verified" if green else "missing",
        "reason": (f"patch_check: {check.get('verdict')}"
                   + (f" — {why} {errors}".rstrip() if not green else "")),
        "artifacts_named": [rel],
        "artifacts_observed": [rel] if green else [],
    }


def _forbidden(paths: list[str]) -> list[str]:
    from core.self_apply_lane import PROTECTED_CORE

    return [p for p in paths if p.startswith(_FORBIDDEN) or p.replace("\\", "/") in PROTECTED_CORE]


def _merge(root: Path, branch: str) -> str:
    """Перемотать основную ветку на коммит пути применения (он сам не сливает)."""
    done = subprocess.run(["git", "-C", str(root), "merge", "--ff-only", branch],  # noqa: S603, S607
                          capture_output=True, text=True, check=False, timeout=120)
    return "" if done.returncode == 0 else (done.stderr or done.stdout).strip()[:300]


def settle_patch(agent: Any, workspace: Path | str, success_check: str) -> dict[str, Any] | None:
    """Прогнать правку цели и поставить её, если всё зелёное; None — это не цель правки."""
    from core.approval_inbox import ApprovalInbox
    from core.budget_kill_switch import BudgetKillSwitch, default_path
    from core.safe_vcs import SafeVCS
    from core.self_apply_bridge import build_self_apply_payload, run_approved_self_apply
    from tools.patch_check import PatchCheckTool, patched_contents
    from tools.run_tests import RunTestsTool

    match = _PATCH_RE.search(success_check or "")
    if not match:
        return None
    root, rel = Path(workspace), match.group(0)
    if not (root / rel).is_file():
        return {"verdict": "missing", "reason": f"файла правки {rel} нет"}
    check = PatchCheckTool(workspace_root=root).run(path=rel, full=True)
    if check.get("verdict") != "green" or check.get("full_exit_code") != 0:
        _log(root, {"patch": rel, "result": "red", "why": check.get("why"), "errors": check.get("errors")})
        return {"verdict": "missing", "reason": f"patch_check: {check.get('why')} {check.get('errors') or ''}"[:300]}
    blocked = _forbidden(check.get("files") or [])
    if blocked:
        _log(root, {"patch": rel, "result": "forbidden", "files": blocked})
        return {"verdict": "missing", "reason": "правка трогает запретное: " + ", ".join(blocked)}
    state, today = _state(root), _now().date().isoformat()
    if state["applied"].get(today, 0) >= DAILY_CAP:
        _log(root, {"patch": rel, "result": "daily_cap"})
        return {"verdict": "missing", "reason": f"дневной потолок {DAILY_CAP} правок исчерпан"}
    files = [{"path": p, "content": c} for p, c in patched_contents(root, rel).items()]
    inbox = ApprovalInbox(path=root / "data" / "approval_inbox.jsonl")
    item = inbox.add(operation="self_apply_lane.run", summary=f"self-repair {rel}", risk="reversible",
                     reasons=("patch_check green with full suite",),
                     payload=build_self_apply_payload(files=files, reason=f"self-repair {rel}",
                                                      evidence=(rel,), origin="self_repair_route",
                                                      workspace=root),
                     dedup_key=f"self_repair:{rel}")
    inbox.approve(item.id, reason="стоячее правило самопочинки (оператор 2026-09-22)",
                  actor="standing_rule:self_repair")
    report = run_approved_self_apply(inbox=inbox, item_id=item.id, workspace=root, vcs=SafeVCS(workspace=root),
                                     test_runner=RunTestsTool(workspace_root=root),
                                     kill_switch=BudgetKillSwitch(path=default_path(root)))
    report = report if isinstance(report, dict) else report.to_dict()
    if report.get("status") != "committed_local" or not report.get("branch"):
        _log(root, {"patch": rel, "result": report.get("status"), "why": report.get("reason")})
        return {"verdict": "missing", "reason": f"путь применения: {report.get('status')} {report.get('reason')}"[:300]}
    merge_error = _merge(root, report["branch"])
    if merge_error:
        _log(root, {"patch": rel, "result": "merge_failed", "why": merge_error, "branch": report["branch"]})
        return {"verdict": "missing", "reason": "не перемоталось: " + merge_error}
    state["applied"][today] = state["applied"].get(today, 0) + 1
    _save_state(root, state)
    fingerprint = state.get("patch_to_defect", {}).get(rel)
    if fingerprint:
        from core.self_improvement_issues import DEFAULT_ISSUE_PATH, SelfImprovementIssueRegistry

        SelfImprovementIssueRegistry(root / DEFAULT_ISSUE_PATH).transition(
            status="resolved", observed_at=_now().isoformat(), fingerprint=fingerprint,
            evidence=f"починено самим агентом: {rel}, patch_check green + полный набор, коммит {report.get('commit_hash')}")
    _log(root, {"patch": rel, "result": "applied", "commit": report.get("commit_hash"),
                "files": report.get("files_changed")})
    return {"verdict": "verified", "reason": f"правка поставлена, коммит {report.get('commit_hash')}"}
