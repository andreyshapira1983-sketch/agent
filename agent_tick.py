"""Autonomous agent daemon tick.

This script is designed to be called by Windows Task Scheduler (or any cron
equivalent) on a fixed interval — e.g. every 30 minutes. It does NOT stay
running; it wakes up, does one bounded health pass, writes results to disk,
and exits. Zero risk of runaway processes.

What one tick does
------------------
1. Load scheduler store → find schedules that are due.
2. Enqueue due schedules as RuntimeTasks.
3. Claim pending RuntimeTasks → run AutonomousRuntime (dry-run by default).
4. If any pytest tests FAILED → call RepairProposalGenerator → put result
   in ApprovalInbox so the human sees it next time they open the REPL.
5. Write a tick summary to logs/daemon_tick.jsonl.
6. Exit with code 0 (success) or 1 (hard error).

Environment variables
---------------------
AGENT_WORKSPACE   path to workspace root (default: directory of this file)
AGENT_PROVIDER    mock | openai | anthropic  (unset falls back to anthropic;
                  .env.example ships mock so a fresh clone stays offline)
AGENT_TICK_DRY_RUN  1 = never write real files, 0 = allow effects (default: 1)

Usage
-----
python agent_tick.py                    # run once, workspace = .
python agent_tick.py --workspace C:/x   # explicit workspace
python agent_tick.py --allow-effects    # disable dry-run (use with care)
python agent_tick.py --status           # print pending inbox items and exit
python agent_tick.py --standing-grant 20 48   # open unattended permission, exit

Windows Task Scheduler quick-start
-----------------------------------
Run scripts/install_daemon.ps1 to register the scheduled task automatically.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import traceback
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from core import heartbeat_io as _hb
from core.approval_inbox import DEFAULT_APPROVAL_INBOX_PATH
from core.llm import ensure_roster_home
from core.rule_approved_apply import drain_and_log
from core.task_queue import DEFAULT_RUNTIME_TASKS_PATH

if TYPE_CHECKING:
    from core.approval_inbox import ApprovalInbox


# ── paths ─────────────────────────────────────────────────────────────────────

WORKSPACE_DEFAULT = Path(os.environ.get("AGENT_WORKSPACE", Path(__file__).parent)).resolve()

DATA_DIR           = "data"
# Tick-journal path is owned by core/heartbeat_io.py (writer+reader, MIR-135).
LOGS_DIR           = "logs"
APPROVAL_INBOX_PATH = DEFAULT_APPROVAL_INBOX_PATH
SCHEDULES_PATH     = "data/runtime_schedules.jsonl"
# ONE queue, and it is the one the operator writes to. Until 2026-08-05 the
# daemon read `data/task_queue.jsonl`, which no other module mentions, while
# `:task-add`, the scheduler CLI and the health command all used
# `data/runtime_tasks.jsonl` (`app/bootstrap.py`, `app/task_scheduler_cli.py`,
# `cli/commands_health.py`). A task queued by hand was therefore invisible
# here — measured on the live workspace: 27 consecutive ticks logged
# `no_pending_tasks` while an `auto_run` task sat `pending` in the other
# store. The daemon was not refusing the work; it could not see it.
#
# Imported rather than re-declared, so a third path cannot appear the same
# quiet way. `tests/test_one_task_store.py` fails if it does.
TASK_QUEUE_PATH    = str(DEFAULT_RUNTIME_TASKS_PATH)
INCIDENT_LOG_PATH  = "data/incidents.jsonl"
HEARTBEAT_PATH     = _hb.HEARTBEAT_PATH
# The single-instance lock every queue consumer must hold while draining. Same
# path app/single_instance.py documents, so a future always-on daemon and this
# cron tick exclude each other rather than both claiming tasks.
DAEMON_LOCK_PATH   = "data/daemon.lock"

#: Сколько раз пробовать выбрать цель на старте кампании. Замер
#: 2026-09-01T23:27: одна попытка — и прогон умирал на первой отвергнутой
#: цели, хотя внутри прогона право сменить исчерпанную цель уже есть.
_GOAL_PICK_ATTEMPTS = 3
BUDGET_LEDGER_PATH = "data/budget_ledger.jsonl"
BUDGET_CONFIG_PATH = "config/budget_limits.json"
# Persistent cooldown state for the autonomous self-build producer (TD-026).
# Holds the ISO timestamp of the last *successfully proposed* item so the daemon
# does not create a new self-build proposal more often than the cooldown window.
SELF_BUILD_STATE_PATH = "data/self_build_producer_state.json"
# Default hours between autonomous self-build proposals. Conservative by design;
# override via AGENT_SELF_BUILD_COOLDOWN_HOURS.
SELF_BUILD_COOLDOWN_HOURS_DEFAULT = 12.0

# Liveness constants and helpers live in core/heartbeat_io.py: whether the daemon
# is alive is an input to a decision (best_next_action asks it), and the core may
# not import this script to find out. Re-exported here under the original names
# so existing callers and tests are unaffected.
EXPECTED_TICK_INTERVAL_SECONDS = _hb.EXPECTED_TICK_INTERVAL_SECONDS
STALENESS_FACTOR = _hb.STALENESS_FACTOR

# Memory profile for the unattended (daemon/cron) agent. Defined once here
# because all three build sites below must stay identical.
#
# Phase 1 — connect experience memory to the core WITHOUT granting it a voice
# in durable state:
#   with_experience=True     the unattended agent finally holds episodic and
#                            procedural stores (before this it held none, so it
#                            could neither record nor recall experience)
#   episodic_replay=False    it may read that experience, but must never serve
#                            a stored answer in place of running a real cycle
#   durable_writes={"episode", "hygiene"}
#                            a TWO-SINK allowlist: episodes so the work leaves
#                            a trace, hygiene so housekeeping is recorded,
#                            while procedural promotion, consolidation,
#                            knowledge, source registry, profile and
#                            assumptions all stay denied by default-deny
#   with_memory=False        unchanged — no cross-run session memory
#
# The claim that episodes are quarantined (`usage_eligible=False`) was measured
# false on 2026-08-20: 136 of 200 banked episodes are eligible, 127 of them
# admitted by the unconditional `lesson` tag exemption. See MIR-115.
UNATTENDED_MEMORY_PROFILE = {
    "with_memory": False,
    "with_experience": True,
    "episodic_replay": False,
    "durable_writes": frozenset({"episode", "hygiene"}),
}


def unattended_memory_profile(workspace, env=None) -> dict:
    """Профиль памяти для безнадзорной сборки агента.

    Производственный ответ — константа выше, буква в букву. Единственное
    исключение названо и ограничено сроком: включённое полномочие песочницы
    (`core/burn_in_sandbox.py`, две независимые подписи) добавляет два стока,
    чтобы проверенный урок мог пережить прогон. Без полномочия функция
    возвращает ровно `UNATTENDED_MEMORY_PROFILE`.

    Функция, а не константа, потому что ответ зависит от рабочей копии: одна и
    та же сборка в песочнице и в производстве обязана давать разные списки, и
    место, где эта разница решается, должно быть одно.

    Зовите её с переменной, названной `workspace`: четыре производственных
    места сборки агента сверяются сторожем
    `tests/test_ownership_must_not_change_authority.py` БУКВАЛЬНО, и разные
    имена переменной читаются там как разный конверт полномочий.
    """
    from core.burn_in_sandbox import memory_profile_for

    return memory_profile_for(UNATTENDED_MEMORY_PROFILE, workspace, env=env)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log_tick(workspace: Path, payload: dict) -> None:
    log_path = _hb.tick_log_path(workspace)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": _now_iso(), **payload}, ensure_ascii=False) + "\n")


_write_heartbeat = _hb.write_heartbeat
_read_heartbeat = _hb.read_heartbeat
_heartbeat_age_seconds = _hb.heartbeat_age_seconds
_is_stale = _hb.is_stale


def _check_budget_kill_switch(workspace: Path):
    """Evaluate (and latch) the persistent budget kill-switch for this tick.

    Reads the persistent budget ledger directly — no agent / LLM is built —
    so the check is cheap and happens *before* any planner/synthesizer work.
    Returns a ``KillSwitchState``; ``.active`` is True when the autonomous day
    budget is exhausted and the daemon must skip LLM-heavy work.
    """
    from core.budget_kill_switch import BudgetKillSwitch, default_path
    from core.budget_ledger import BudgetLedger

    ledger = BudgetLedger.from_env(
        path=workspace / BUDGET_LEDGER_PATH,
        config_path=workspace / BUDGET_CONFIG_PATH,
    )
    kill_switch = BudgetKillSwitch(path=default_path(workspace))
    return kill_switch.engage_if_needed(ledger.snapshot())


def _dry_run_visibility(
    *,
    dry_run: bool,
    previous_streak: int = 0,
    processed_effects: int = 0,
    did_work: bool = True,
) -> dict:
    """Honest observability fields for the current tick — PURE, NO behaviour change.

    A pure function: no file/env/clock reads, no side effects. The caller is
    responsible for sourcing ``previous_streak`` (e.g. from the prior heartbeat)
    and ``processed_effects`` (count of effects actually applied this tick).

    This block only *describes* what the daemon is (not) doing; it never enables
    effects, never touches approval policy, and never alters proposal logic. It
    is also kept SEPARATE from run_status / result_status — those answer "did the
    run finish" and "what did it establish"; these answer "what mode am I in and
    did I apply anything".

    Args:
      - ``dry_run``          — whether this tick runs without applying effects.
      - ``previous_streak``  — dry_run_streak carried from the previous tick.
      - ``processed_effects``— effects actually applied this tick (normally 0).
      - ``did_work``         — whether this tick actually ran a dry-run pass
                               (``tasks_processed > 0``). An IDLE no-op tick
                               (nothing due, nothing pending) did no work and
                               carries no information, so it must NOT inflate the
                               streak. Defaults to ``True`` so callers that only
                               care about mode/effects keep the simple behaviour.

    Returns:
      - ``mode``             — ``"dry_run"`` or ``"live"``.
      - ``effects``          — ``"disabled"`` in dry-run, else ``"enabled"``
                               (i.e. *policy-allowed*, not "already applied").
      - ``processed_effects``— echoed back, clamped to a non-negative int. In
                               practice 0: dry-run applies nothing and the live
                               path is still gated into the approval inbox.
      - ``dry_run_streak``   — consecutive *meaningful* dry-run passes. On a
                               dry-run tick that did work it is ``previous_streak
                               + 1`` (regardless of test health — a failed /
                               inconclusive dry-run pass is STILL a dry-run pass).
                               On an IDLE dry-run tick (``did_work`` false) it is
                               carried forward UNCHANGED — an idle tick adds no
                               information and must not dilute the stall signal.
                               A live tick resets it to 0.
    """
    try:
        prev = max(0, int(previous_streak))
    except (TypeError, ValueError):
        prev = 0
    try:
        applied = max(0, int(processed_effects))
    except (TypeError, ValueError):
        applied = 0
    if not dry_run:
        streak = 0
    elif did_work:
        streak = prev + 1
    else:
        streak = prev  # idle no-op tick — carry forward, do not inflate
    return {
        "mode": "dry_run" if dry_run else "live",
        "effects": "disabled" if dry_run else "enabled",
        "processed_effects": applied,
        "dry_run_streak": streak,
    }


def _log_idle_tick(workspace: Path) -> None:
    """Empty queue: say so, then what the agent would do anyway. Sensor, not
    gate — decision in `core.self_build_memory.idle_self_direction`."""
    _log_tick(workspace, {"event": "no_pending_tasks"})
    try:
        from core.self_build_memory import idle_self_direction
        _log_tick(workspace, {"event": "self_direction",
                              **idle_self_direction(workspace)})
    except Exception as exc:  # noqa: BLE001
        _log_tick(workspace, {"event": "self_direction_error",
                              "error": f"{type(exc).__name__}: {exc}"})


def _classify_test_health(tests_result: dict | None) -> str:
    """Map a ``tests_result`` payload to a single honest health verdict.

    Returns one of:
      - ``"none"``         — no tests ran this tick (nothing to claim).
      - ``"pass"``         — tests finished cleanly with at least one pass.
      - ``"fail"``         — at least one test failed or errored.
      - ``"inconclusive"`` — the run timed out, had no exit code, or collected
                             zero tests. This is the key fix: a timed-out run
                             must NEVER be read as ``"pass"`` just because its
                             failure count is zero.
    """
    if not tests_result:
        return "none"
    if tests_result.get("timed_out") or tests_result.get("exit_code") is None:
        return "inconclusive"
    failed = int(tests_result.get("failed", 0) or 0) + int(
        tests_result.get("errors", 0) or 0
    )
    if failed > 0:
        return "fail"
    if int(tests_result.get("passed", 0) or 0) == 0:
        # exit_code present, zero failures, but nothing actually ran:
        # this is not evidence of health.
        return "inconclusive"
    return "pass"



# ── status-only mode ──────────────────────────────────────────────────────────

#: Сколько одинаковых действий по одной цели подряд считать траекторией-петлёй.
#: Не порог суждения, а порог ВНИМАНИЯ: строка ничего не запрещает, она зовёт
#: посмотреть. Три — потому что два повтора бывают у любой честной работы, а
#: живая петля была 150.
_TRAJECTORY_REPEAT_MIN = 3

#: Окно, за которым петля считается прошедшей. Наблюдённая шла 27 часов.
_TRAJECTORY_WINDOW_HOURS = 48


def _trajectory_status_lines(workspace: Path) -> list[str]:
    """Строки о повторяющейся траектории — или пусто, если её нет.

    Замер, отвергнутые варианты и границы: MIR-149 в docs/audit/MASTER_ISSUE_REGISTRY.md.
    Только чтение и никогда не бросает.
    """
    lines: list[str] = []
    try:
        from datetime import datetime, timedelta, timezone

        from core.state_integrity import read_state_jsonl

        path = workspace / DATA_DIR / "campaign_ledger.jsonl"
        if not path.exists():
            return lines
        cutoff = datetime.now(timezone.utc) - timedelta(
            hours=_TRAJECTORY_WINDOW_HOURS
        )
        groups: dict[tuple[str, str], list[dict]] = {}
        for row in read_state_jsonl(path):
            try:
                when = datetime.fromisoformat(str(row.get("ts")))
            except (TypeError, ValueError):
                continue
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            if when < cutoff:
                continue
            key = (str(row.get("goal") or ""), str(row.get("action") or ""))
            groups.setdefault(key, []).append(row)
    except (OSError, ValueError):  # pragma: no cover — чтение не вправе ронять строку
        return []

    for (goal, action), rows in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        if len(rows) < _TRAJECTORY_REPEAT_MIN or not action:
            continue
        cost = sum(int(r.get("cost_units_spent") or 0) for r in rows)
        calls = sum(int(r.get("llm_calls_spent") or 0) for r in rows)
        lines.append(
            f"Trajectory: {len(rows)} cycles of ONE action "
            f"'{action}' in {_TRAJECTORY_WINDOW_HOURS}h — {calls} model call(s), "
            f"{cost} cost unit(s) — goal: {goal[:60]!r}"
        )
    return lines[:3]


def _quarantine_status_lines(workspace: Path) -> list[str]:
    """Строки о потерянных строках состояния — или пусто, когда терять нечего.

    H-29 в docs/audit/HISTORICAL_FAILURE_LEDGER.md. Порченая строка уезжает в
    `.quarantine`, файл переписывается без неё, и читатель получает список
    короче, ничем не отличая его от полного. У обычного журнала это потеря
    памяти; у СЧЁТЧИКА-ПОТОЛКА это выданное разрешение — замер показал, что
    исчерпанный суточный потолок после порчи одной строки снова разрешает
    тратить. Решение (разрешать или отказывать) здесь не меняется: оно про
    деньги и принадлежит оператору. Закрывается молчание.

    Только чтение и никогда не бросает: строка состояния обязана печататься
    даже на повреждённом дереве.
    """
    from core.state_integrity import quarantine_dir_for

    lines: list[str] = []
    try:
        qdir = quarantine_dir_for(workspace / DATA_DIR / "x.jsonl")
        if not qdir.exists():
            return lines
        entries = sorted(f for f in qdir.iterdir() if f.is_file())
        if not entries:
            return lines
        lines.append(
            f"State integrity: {len(entries)} quarantined row file(s) — "
            f"rows dropped from state stores, and a dropped row in a CAP "
            f"store reads as spending room:"
        )
        for f in entries[:10]:
            lines.append(f"  {f.name}")
        if len(entries) > 10:
            lines.append(f"  ... and {len(entries) - 10} more")
    except OSError:
        return []
    return lines


#: Отметки пульса, означающие «тик кончился отказом». Подстрокой, а не точным
#: списком: события такого рода заводятся по мере надобности, и новый
#: `*_error` не должен молча вернуть строке слово «alive» (H-38).
def _is_failure_event(event: object) -> bool:
    text = str(event or "").lower()
    return "error" in text or "exception" in text or "fail" in text


def _print_status(workspace: Path) -> int:
    """Print pending inbox items and exit. No agent is created."""
    from core.approval_inbox import ApprovalInbox

    # Daemon liveness first: distinguish "idle (nothing due)" from "not running".
    heartbeat = _read_heartbeat(workspace)
    age = _heartbeat_age_seconds(heartbeat)
    if heartbeat is None:
        print("Daemon: no heartbeat recorded yet (never ticked).", file=sys.stderr)
    else:
        age_min = (age or 0) / 60.0
        last_event = heartbeat.get("event", "?")
        if _is_stale(age):
            print(
                f"Daemon: STALE — last tick {age_min:.1f} min ago "
                f"(event={last_event}); expected every "
                f"{EXPECTED_TICK_INTERVAL_SECONDS // 60} min. "
                "Daemon may not be running.",
                file=sys.stderr,
            )
        elif _is_failure_event(last_event):
            # H-38: пульс пишется и на ветке ОТКАЗА, поэтому свежесть отметки
            # говорила «жив», а правда лежала в скобках после неё. Замер
            # 2026-08-24: три подряд упавших тика печатали
            # «Daemon: alive — last tick 0.0 min ago (event=tick_error)».
            # Оператор, глянувший за неделю одну строку, читает первое слово.
            # Решение о вечном повторе не меняется (MIR-135, открыт отдельно) —
            # меняется СЛОВО, которым строка ведёт.
            print(
                f"Daemon: FAILING — last tick {age_min:.1f} min ago ended in "
                f"{last_event}. The clock is ticking and no work is landing.",
                file=sys.stderr,
            )
        else:
            print(
                f"Daemon: alive — last tick {age_min:.1f} min ago "
                f"(event={last_event}).",
                file=sys.stderr,
            )

    # Block 7 (W1): open incidents reach the one line the operator reads.
    from core.incident import IncidentLog

    incident_line = IncidentLog(path=workspace / INCIDENT_LOG_PATH).status_line()
    if incident_line:
        print(incident_line, file=sys.stderr)

    # Dry-run visibility from the last heartbeat: honest "what was (not) applied".
    if heartbeat is not None:
        mode = heartbeat.get("mode", "?")
        effects = heartbeat.get("effects", "?")
        streak = heartbeat.get("dry_run_streak", "?")
        # F-5 (docs/audit/FIELD_CHECK_QUEUE.md): `processed_effects` убрано из
        # строки. Оно передаётся ЖЁСТКИМ НУЛЁМ в обоих местах записи пульса, то
        # есть постоянная, а в строке читалось как счётчик применённых эффектов.
        # Обоснование в докстринге («живой путь упирается в ящик одобрений»)
        # неполно: при выданном разрешении (H-41) тик применяет эффекты сам и
        # поле всё равно показывает ноль. Из пульса поле НЕ убрано — схему
        # парсят кампания и память; убрано только оттуда, где число попадает
        # человеку на глаза и выглядит измерением.
        print(
            f"Mode: {mode} (effects={effects}, dry_run_streak={streak})",
            file=sys.stderr,
        )

    inbox = ApprovalInbox(path=workspace / APPROVAL_INBOX_PATH)
    pending = inbox.pending()
    if not pending:
        print("Inbox: no pending items.", file=sys.stderr)
    else:
        print(f"Inbox: {len(pending)} pending item(s):", file=sys.stderr)
        for item in pending:
            print(f"  [{item.id}] {item.operation}: {item.summary}", file=sys.stderr)

    # Self-build producer visibility (TD-027) — read-only, never raises.
    for line in _self_build_status_block(workspace, heartbeat, pending, inbox):
        print(line, file=sys.stderr)
    for line in _quarantine_status_lines(workspace):
        print(line, file=sys.stderr)
    for line in _trajectory_status_lines(workspace):
        print(line, file=sys.stderr)
    from core.charter_goal import charter_status_lines

    for line in charter_status_lines(workspace):
        print(line, file=sys.stderr)
    from core.subagent_quarantine import quarantine_status_lines

    for line in quarantine_status_lines(workspace):
        print(line, file=sys.stderr)
    return 0


# ── repair proposal after failed tests ───────────────────────────────────────

def _repair_target_from_failures(failed_names: list[str], workspace: Path) -> str | None:
    """Pick a single concrete repair target from failing test names.

    `RepairProposalGenerator.generate` requires one existing `target_path`;
    it does NOT auto-pick a target. Each failing name looks like
    ``tests/foo.py::Klass::test_bar`` — the part before ``::`` is the test
    file. We return that file only when the failures point at exactly one
    existing file; otherwise we return None so the caller refuses cleanly
    instead of guessing across multiple files.
    """
    files: list[str] = []
    for name in failed_names:
        path = (name or "").split("::", 1)[0].strip()
        if not path:
            continue
        if (workspace / path).is_file() and path not in files:
            files.append(path)
    return files[0] if len(files) == 1 else None


#: Имена исключений — устойчивая часть диагноза. Всё прочее в нём
#: переформулируется от тика к тику.
_CAUSE_RE = re.compile(r"\b([A-Z][A-Za-z0-9]*(?:Error|Exception|Warning|Timeout))\b")


def _cause_fingerprint(text: str) -> str:
    """Причина сбоя, сведённая к устойчивому следу.

    Ревизия PR #333: прежний ключ склеивал цель починки и имена упавших
    тестов, поэтому ТОТ ЖЕ тест, упавший по ДРУГОЙ причине, давал ту же
    строку — материально другая беда молча пряталась за первой заявкой.

    Осторожность обратной стороны важнее прямой. Диагноз — свободный текст, и
    взять его целиком значило бы вернуть размножение заявок с другого конца:
    каждая переформулировка открывала бы новую. Поэтому берётся только
    множество имён исключений; диагноз без такого имени даёт пустой след,
    то есть прежнее поведение и никакой выдуманной причины.
    """
    found = sorted({m.group(1) for m in _CAUSE_RE.finditer(text or "")})
    return "|".join(found)


def _repair_dedup_key(
    target_path: str, failed_names: list[str], cause: str = ""
) -> str:
    """Устойчивое имя ОДНОЙ поломки: цель починки + упавшие тесты + причина.

    Порядок имён не значим (pytest выдаёт их как придётся), поэтому множество
    сортируется. Зачем ключ вообще: см. место вызова в `_maybe_propose_repair`.
    """
    fingerprint = "|".join(sorted({str(n) for n in failed_names}))
    if cause:
        fingerprint += "##" + cause
    digest = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:16]
    return f"repair:{target_path}:{digest}"


def _maybe_propose_repair(
    workspace: Path,
    test_report: dict,
    inbox: ApprovalInbox,
    agent: object,
) -> dict:
    """If tests failed, ask RepairProposalGenerator for a plan and inbox it."""
    failed = int(test_report.get("failed", 0) or 0) + int(test_report.get("errors", 0) or 0)
    if failed == 0:
        return {"repair_proposed": False, "reason": "all tests passed"}

    failed_names: list[str] = test_report.get("failed_tests", []) or []

    target_path = _repair_target_from_failures(failed_names, workspace)
    if target_path is None:
        return {
            "repair_proposed": False,
            "reason": (
                "could not determine a single repair target from failing tests: "
                f"{failed_names[:5]}"
            ),
        }

    try:
        from core.repair_proposal import RepairProposalGenerator

        llm = getattr(agent, "llm", None)
        if llm is None:
            return {"repair_proposed": False, "reason": "no llm on agent"}

        gen = RepairProposalGenerator(llm=llm, workspace_root=workspace)
        report = gen.generate(target_path=target_path)

        if report.status == "proposed" and report.proposal is not None:
            prop = report.proposal
            # RepairProposal (core/self_repair.py) carries `path` / `reason` /
            # `proposed_content` — NOT target_file/description/patch_preview.
            description = (prop.reason or report.diagnosis or "").strip() or "(no description)"
            summary_lines = [
                f"{failed} test(s) failed: {', '.join(failed_names[:5])}",
                f"Proposed fix → {prop.path}: {description}",
                f"Confidence: {report.confidence:.0%}",
            ]
            inbox.add(
                operation="repair_proposal",
                summary="\n".join(summary_lines),
                risk="reversible",
                reasons=(
                    f"{failed} failing test(s)",
                    f"evidence: {', '.join(report.evidence[:3])}",
                ),
                # Одна неустранённая поломка — одна просьба. Без ключа
                # `ApprovalInbox.add` не дедуплицирует вовсе, а починка ждёт
                # человека, значит каждый следующий тик видел ТУ ЖЕ поломку и
                # клал в ящик ещё одну такую же заявку (аудит автономности
                # 2026-09-17). Ящик — не только вход человека, но и ворота:
                # полоса самоприменения отказывается работать, пока в нём есть
                # ожидающие. Размножение заявок глушило настоящие просьбы и
                # само себя блокировало.
                #
                # Ключ описывает ПОЛОМКУ (цель починки + имена упавших тестов
                # + устойчивый след причины), а не предложенное лечение:
                # перефразированный диагноз — та же беда, другой упавший тест
                # или другое исключение — уже другая.
                dedup_key=_repair_dedup_key(
                    target_path, failed_names,
                    _cause_fingerprint(
                        f"{report.diagnosis or ''} {' '.join(report.evidence or ())}"
                    ),
                ),
                payload={
                    "failed_count": failed,
                    "failed_tests": failed_names,
                    "target_file": prop.path,
                    "proposed_content_preview": (
                        prop.proposed_content[:500] if prop.proposed_content else ""
                    ),
                    "confidence": report.confidence,
                    "diagnosis": report.diagnosis,
                },
            )
            return {"repair_proposed": True, "target": prop.path}
    except Exception as exc:  # noqa: BLE001
        return {"repair_proposed": False, "reason": f"exception: {exc}"}
    else:
        return {"repair_proposed": False, "reason": f"generator status: {report.status}"}


# ── autonomous self-build producer wiring (TD-026) ────────────────────────────


def _self_build_cooldown_hours() -> float:
    """Resolve the cooldown window in hours (env override, else default).

    Read at call time (not import time) so the daemon and tests can tune it via
    ``AGENT_SELF_BUILD_COOLDOWN_HOURS`` without reloading the module.
    """
    raw = os.environ.get("AGENT_SELF_BUILD_COOLDOWN_HOURS")
    if raw is None or not str(raw).strip():
        return SELF_BUILD_COOLDOWN_HOURS_DEFAULT
    try:
        hours = float(raw)
    except (TypeError, ValueError):
        return SELF_BUILD_COOLDOWN_HOURS_DEFAULT
    return hours if hours >= 0 else SELF_BUILD_COOLDOWN_HOURS_DEFAULT


def _read_producer_state(workspace: Path) -> dict:
    path = workspace / SELF_BUILD_STATE_PATH
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_producer_state(workspace: Path, last_proposed_at: str) -> None:
    path = workspace / SELF_BUILD_STATE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {"last_proposed_at": last_proposed_at}
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)  # atomic swap


def _cooldown_remaining_seconds(
    state: dict, *, cooldown_hours: float, now: datetime | None = None
) -> float:
    """Seconds still to wait before another proposal is allowed (0 = ready)."""
    if cooldown_hours <= 0:
        return 0.0
    last = state.get("last_proposed_at")
    if not last:
        return 0.0
    try:
        when = datetime.fromisoformat(str(last))
    except (ValueError, TypeError):
        return 0.0  # unparseable timestamp never blocks a fresh proposal
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    elapsed = (now - when).total_seconds()
    remaining = cooldown_hours * 3600.0 - elapsed
    return remaining if remaining > 0 else 0.0


def _format_remaining(seconds: float) -> str:
    """Human-readable cooldown remaining, e.g. ``2h 5m`` / ``7m`` / ``<1m``."""
    total = int(seconds) if seconds and seconds > 0 else 0
    hours, rem = divmod(total, 3600)
    minutes, _ = divmod(rem, 60)
    if hours and minutes:
        return f"{hours}h {minutes}m"
    if hours:
        return f"{hours}h"
    if minutes:
        return f"{minutes}m"
    return "<1m"


def _self_build_status_lines(
    heartbeat: dict | None,
    state: dict | None,
    *,
    cooldown_hours: float,
    pending_self_apply: int,
    approved_self_apply: int,
    now: datetime | None = None,
) -> list[str]:
    """Render the read-only 'Self-build' block for the operator ``--status`` view.

    Pure and fully defensive (TD-027): never raises on a missing/corrupt
    heartbeat, missing/corrupt producer state, or an unparseable timestamp — it
    degrades to a clear line instead. The last producer status is taken from the
    heartbeat and is labelled explicitly as *historical* (the outcome of the last
    completed tick), NOT a live gate. The ONLY value computed live here is the
    remaining cooldown, derived from the producer state file.
    """
    hb = heartbeat if isinstance(heartbeat, dict) else {}
    st = state if isinstance(state, dict) else {}
    lines: list[str] = []

    last_status = hb.get("self_build_status") or "none"
    if last_status == "none":
        lines.append("Self-build: last self-build status: never ran")
    else:
        lines.append(
            f"Self-build: last self-build status: {last_status} "
            "(historical, from last tick — not a live gate)"
        )

    last_proposed = st.get("last_proposed_at")
    if not st:
        health = "missing"
    elif not last_proposed:
        health = "no-timestamp"
    else:
        try:
            datetime.fromisoformat(str(last_proposed))
            health = "ok"
        except (ValueError, TypeError):
            health = "bad-timestamp"
    lines.append(f"  last_proposed_at: {last_proposed or 'none'} (state file: {health})")

    remaining = _cooldown_remaining_seconds(st, cooldown_hours=cooldown_hours, now=now)
    if remaining <= 0:
        lines.append("  cooldown: ready (live)")
    else:
        lines.append(f"  cooldown: {_format_remaining(remaining)} remaining (live)")

    lines.append(f"  last approval_id: {hb.get('self_build_approval_id') or 'none'}")
    lines.append(f"  next_human_action: {hb.get('self_build_next_human_action') or 'none'}")
    lines.append(
        f"  self_apply_lane.run: {pending_self_apply} pending, "
        f"{approved_self_apply} approved ready for :self-apply-run"
    )
    return lines


def _provider_health_line(workspace: Path) -> str:
    """One line naming any provider the router is currently skipping.

    Found by auditing MIR-132's own closure against the field's named
    circuit-breaker failures (2026-08-22): every source says a breaker without
    visibility is untunable and its trips must be observable — and ours was
    invisible. A demotion that nobody can see is indistinguishable from a
    provider that simply stopped being chosen, which is exactly the
    unexplained-quiet the week-long run must not produce.
    """
    try:
        from core.model_usage import ModelUsageLedger, ModelUsageLimits

        ledger = ModelUsageLedger(
            path=workspace / DATA_DIR / "model_usage.jsonl",
            limits=ModelUsageLimits(),
        )
        seen: list[str] = []
        for row in ledger._recent_rows_for_any(limit=200):
            provider = str(row.get("provider") or "").strip().lower()
            if provider and provider not in seen:
                seen.append(provider)
        if not seen:
            return "no calls recorded"
        parts = []
        for provider in seen:
            reason = ledger.provider_unhealthy(provider)
            if reason:
                parts.append(f"{provider} SKIPPED ({reason})")
                continue
            # "ok" must not mean "not in cooldown right now". The first draft
            # of this line reported «anthropic ok» for a provider with 391
            # credit refusals and zero successes — stale failures fall out of
            # the cooldown, and the operator would have read a dead key as
            # healthy. The last recorded outcome is what he actually needs.
            rows = ledger._recent_rows_for(provider, limit=1)
            last = str((rows[-1] if rows else {}).get("status") or "?")
            if last == "success":
                parts.append(f"{provider} ok")
            else:
                err = str((rows[-1] if rows else {}).get("error") or "")[:60]
                parts.append(f"{provider} last={last} ({err})" if err
                             else f"{provider} last={last}")
        return "; ".join(parts)
    except Exception as exc:  # noqa: BLE001 — operator status must never crash
        return f"unavailable ({type(exc).__name__})"


def _self_build_status_block(
    workspace: Path, heartbeat: dict | None, pending_items: list, inbox: ApprovalInbox
) -> list[str]:
    """Gather producer state + inbox counts and format them; never raises.

    Reuses the already-computed ``pending_items`` list so we do not trigger a
    second ``inbox.pending()`` (which enforces TTL on read). Any unexpected error
    degrades to a single 'status unavailable' line so ``--status`` cannot crash.
    """
    try:
        from core.self_apply_bridge import SELF_APPLY_OPERATION

        state = _read_producer_state(workspace)
        cooldown_hours = _self_build_cooldown_hours()
        pending_sa = sum(
            1 for i in pending_items if getattr(i, "operation", "") == SELF_APPLY_OPERATION
        )
        approved_sa = sum(
            1
            for i in inbox.list(status="approved")
            if getattr(i, "operation", "") == SELF_APPLY_OPERATION
        )
        lines = _self_build_status_lines(
            heartbeat,
            state,
            cooldown_hours=cooldown_hours,
            pending_self_apply=pending_sa,
            approved_self_apply=approved_sa,
        )
        # TD-028: compact subagent-ledger summary (read-only; never runs anything).
        try:
            from core.subagent_registry import SubagentRegistry

            lines.append(f"  subagents: {SubagentRegistry.load(workspace).summary_line()}")
        except Exception:  # noqa: BLE001
            lines.append("  subagents: unavailable")
        lines.append(f"  providers: {_provider_health_line(workspace)}")
    except Exception as exc:  # noqa: BLE001 — operator status must never crash
        return [f"Self-build: status unavailable ({type(exc).__name__})"]
    else:
        return lines


def _maybe_produce_self_build(
    workspace: Path,
    inbox: ApprovalInbox,
    *,
    dry_run: bool = True,
    now_iso: str | None = None,
    cooldown_hours: float | None = None,
    build_agent_fn: Callable[[Path], Any] | None = None,
    producer_fn: Callable[..., Any] | None = None,
    vcs: Any = None,
    kill_switch: Any = None,
    budget_snapshot: Any = None,
) -> dict:
    """Run the autonomous self-build producer at most once per tick (TD-026).

    The producer (TD-025) owns the kill-switch/budget/approval/dirty-tree gates;
    this wrapper adds ONLY a persistent cooldown gate that runs *before* the
    agent is built, so ``cooldown_wait`` never constructs an agent. It creates at
    most one approval inbox item, never applies/commits/pushes and never runs the
    self-apply lane. Any exception is swallowed into ``status="error"`` so a tick
    can never crash here. Heavy collaborators are injectable for deterministic
    tests (no real LLM/provider/network/git).
    """
    now_iso = now_iso or _now_iso()
    try:
        now_dt = datetime.fromisoformat(now_iso)
        if now_dt.tzinfo is None:
            now_dt = now_dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        now_dt = datetime.now(timezone.utc)

    hours = cooldown_hours if cooldown_hours is not None else _self_build_cooldown_hours()

    # ── cooldown gate (the ONLY gate this layer owns) — before build_agent ──
    remaining = _cooldown_remaining_seconds(
        _read_producer_state(workspace), cooldown_hours=hours, now=now_dt
    )
    if remaining > 0:
        return {
            "self_build_status": "cooldown_wait",
            "cooldown_remaining_s": int(remaining),
            "next_human_action": "",
        }

    try:
        # Lazy imports: keep module import cheap and match the tick's dotenv order.
        from core.budget_kill_switch import BudgetKillSwitch, default_path
        from core.budget_ledger import BudgetLedger
        from core.safe_vcs import SafeVCS
        from core.self_build_memory import record_self_build_episode
        from core.self_build_producer import produce_self_apply_proposal

        produce = producer_fn or produce_self_apply_proposal

        if budget_snapshot is None:
            ledger = BudgetLedger.from_env(
                path=workspace / BUDGET_LEDGER_PATH,
                config_path=workspace / BUDGET_CONFIG_PATH,
            )
            budget_snapshot = ledger.snapshot()
        if kill_switch is None:
            kill_switch = BudgetKillSwitch(path=default_path(workspace)).status(
                budget_snapshot
            )
        if vcs is None:
            vcs = SafeVCS(workspace=workspace)

        # `app.bootstrap`, like the other two build sites in this file. This used
        # to read `__import__("main", fromlist=["build_agent"])`, and the Phase-7
        # extraction that reduced `main.py` to 47 lines moved `build_agent` out
        # from under it — so every real tick died here with
        # `AttributeError: module 'main' has no attribute 'build_agent'`.
        #
        # It survived the extraction because the call named its target in a
        # *string*: no import to update, nothing for a linter or for
        # `scripts/architecture_invariants.py` to resolve. A plain import cannot
        # rot the same way — it breaks loudly at import time instead.
        if build_agent_fn is not None:
            builder = build_agent_fn
        else:
            # Imported here, not at module scope: `app.bootstrap` pulls in the
            # whole agent graph, and a caller that supplied its own builder
            # should not pay for loading it.
            from app.bootstrap import build_agent as _build_agent

            def builder(workspace):
                return _build_agent(
                    workspace, approval_provider=None,
                    **unattended_memory_profile(workspace),
                )

        agent = builder(workspace)
        llm = agent.model_router.for_role("synthesizer")

        # TD-028: record role performance into the subagent ledger (best-effort).
        # A registry load/write failure must never break the tick, so this is
        # fully guarded; the producer additionally guards its own recording.
        registry = None
        try:
            from core.subagent_registry import SubagentRegistry
            registry = SubagentRegistry.load(workspace)
        except Exception:  # noqa: BLE001
            registry = None

        report = produce(
            workspace=workspace,
            inbox=inbox,
            llm=llm,
            vcs=vcs,
            budget_snapshot=budget_snapshot,
            kill_switch=kill_switch,
            now_iso=now_iso,
            registry=registry,
        )
        result = report.to_dict() if hasattr(report, "to_dict") else dict(report)
        status = result.get("status", "error")

        # Cooldown is spent ONLY when a proposal is actually created; a wait /
        # veto / no_patch / error must let the next tick try again immediately.
        if status == "proposed":
            _write_producer_state(workspace, now_iso)

        # Journal the outcome so a refusal becomes a retrievable lesson.
        # Every OTHER self-build touch-point already does this — the autonomous
        # runtime and the `:self-build-*` / `:self-apply-run` operator commands —
        # but the tick dropped `result` on the floor. The cost was silent and
        # exact: `recently_vetoed_self_build_targets()` reads episodes tagged
        # `self-build` + `critic_veto`, and nothing on this path ever wrote one,
        # so a veto raised by the daemon taught it nothing and the next tick
        # retried the same target forever. Best-effort by contract: it swallows
        # every failure and returns a bool, so journaling cannot break a tick.
        record_self_build_episode(agent, kind="self-build-produce", result=result)

        return {
            "self_build_status": status,
            "approval_id": result.get("approval_id"),
            "target_path": result.get("target_path"),
            "next_human_action": result.get("next_human_action", ""),
            # `ProducerReport.reason` explains every non-exception outcome
            # (`dirty_tree_wait`, `veto`, `no_patch`, …). Dropping it here left
            # the caller with a bare status word for exactly the cases that are
            # NOT crashes — the common ones.
            "reason": result.get("reason", ""),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "self_build_status": "error",
            "error": f"{type(exc).__name__}: {exc}",
            "next_human_action": "",
        }


def _self_build_status_line(sb_status: str, sb: dict) -> str:
    """The operator-facing line for a self-build outcome. Pure, so it is testable.

    Printing the bare status word threw away everything that made the outcome
    actionable. Three fields carry that, and each covers a different case:

    * ``error``            — the swallowed exception (the wrapper turns every
      exception into ``status="error"`` so a tick cannot crash, which is right,
      but left the operator reading one word while the cause went to a
      different event in ``logs/daemon_tick.jsonl``);
    * ``reason``           — every *ordinary* refusal: dirty tree, veto,
      no_patch. These are the common outcomes, and they are not crashes;
    * ``next_human_action`` — what to do about it.
    """
    detail = sb.get("error") or sb.get("reason") or ""
    advice = sb.get("next_human_action") or ""
    line = f"[agent_tick] Self-build producer: {sb_status}"
    if detail:
        line += f" - {detail}"
    line += "."
    if advice:
        line += f" {advice}"
    return line


def _ensure_env_loaded(workspace: Path) -> None:
    """Load ``<workspace>/.env`` if present. Ambient exports keep priority
    (``load_dotenv`` never overrides), a missing file is silent, and calling
    it twice is a no-op — so any entry point may load before it needs a pin."""
    from dotenv import load_dotenv

    load_dotenv(Path(workspace) / ".env")
    # Дом реестра молчавших объявляет точка входа (Д5, 2026-09-03).
    ensure_roster_home(Path(workspace))


# ── main tick ─────────────────────────────────────────────────────────────────

def _charter_goal_router(workspace: Path) -> Any:
    """Роутер, которым агент выбирает цель от хартии.

    Отдельной функцией, а не строкой внутри `main`, потому что этот путь уже
    один раз потерял бухгалтерию и терял её молча.
    """
    from app.bootstrap import (
        DEFAULT_BUDGET_LEDGER_PATH,
        DEFAULT_MODEL_USAGE_PATH,
    )
    from core.budget_ledger import BudgetLedger
    from core.model_router import ModelRouter
    from core.model_usage import ModelUsageLedger

    # Две правки одного места, вторая — потому что первой хватило наполовину.
    # 2026-08-19: `from_env()` шёл БЕЗ леджера, и вызовы Sol на выбор цели были
    # деньгами, невидимыми в учёте; тогда подключили леджер расхода.
    # 2026-08-24: замер показал 15 вызовов, видимых в расходе и невидимых для
    # ПОТОЛКА — резерв и запись стоимости стоят под `budget_ledger is not None`.
    # Поэтому здесь строится ровно та же связка, что в `app/bootstrap.py`:
    # `from_env` за сессионными лимитами и бюджет за суточным и недельным.
    budget_ledger = BudgetLedger.from_env(
        path=workspace / DEFAULT_BUDGET_LEDGER_PATH,
        config_path=workspace / "config" / "budget_limits.json",
    )
    return ModelRouter.from_env(
        usage_ledger=ModelUsageLedger.from_env(
            path=workspace / DEFAULT_MODEL_USAGE_PATH,
            budget_ledger=budget_ledger,
        ),
    )


def _free_stranded_rows(
    task_store: Any, *, lock: Any, workspace: Path, dry_run: bool = False
) -> None:
    """Startup-only, under the lock: return rows nothing else can free.

    Two resting states, one contract — see docs/CODE_NOTES.md, "Rows nothing
    frees" (MIR-039 / MIR-040 for the orphan half).

    ``dry_run`` carries the tick's own promise one step further than the first
    remediation pass did. The burn-in review (2026-09-17) found this call site
    passing no flag, one line above the sweep that had just been taught the
    flag. The line is drawn at reversibility, by the owner's word: a dry pass
    still re-queues an orphan that has attempts left, and no longer buries one
    that does not — a terminal ``failed`` is a verdict nothing takes back.
    """
    from core.task_lifecycle import reactivate_resumable_work, recover_orphaned_tasks

    def _summarise(tasks: list) -> list[dict]:
        return [{"id": t.id, "status": t.status,
                 "attempts": t.attempts, "error": t.last_error} for t in tasks]

    for event, error_event, action, extra in (
        ("tasks_recovered", "task_recovery_error", recover_orphaned_tasks,
         {"finalise_exhausted": not dry_run}),
        ("paused_work_reactivated", "task_reactivation_error",
         reactivate_resumable_work, {}),
    ):
        try:
            freed = action(task_store, lock=lock, **extra)
        except Exception as exc:  # noqa: BLE001 — one pass failing must not
            _log_tick(workspace, {  # stop the other, nor the tick
                "event": error_event, "error": f"{type(exc).__name__}: {exc}",
            })
            continue
        if freed:
            _log_tick(workspace, {"event": event, "count": len(freed),
                                  "dry_run": dry_run,
                                  "tasks": _summarise(freed)})


def _sweep_episodic_duplicates(workspace: Path, *, dry_run: bool = False) -> int:
    """Collapse byte-identical episodes; return how many rows were dropped.

    Exactly ONE of MIR-131's thirteen CLI-only maintenance actions crosses to
    the unattended path, and the line is drawn on judgement: duplicate collapse
    is mechanical (keeps the newest of an identical group, loses nothing),
    while staleness pruning decides which memories are WORTH keeping — the
    resolver-seat hazard MIR-128 records. Widening this sweep is a decision,
    not a refactor. Full account: docs/CODE_NOTES.md, "The sweep the tick owns".

    ``dry_run`` propagates the tick's own promise. The burn-in audit
    (2026-09-17) found this call site passing no flag at all, so a run that
    announced itself as dry still deleted rows — an irreversible change inside
    a pass whose whole contract is that it changes nothing. The count is still
    measured and journalled in dry-run, because "what WOULD have been removed"
    is exactly what the shadow mode elsewhere in this tick reports.
    """
    try:
        from core.episodic_hygiene import collapse_duplicate_episodes
        from core.smart_memory import EpisodicMemoryStore

        store = EpisodicMemoryStore(path=workspace / DATA_DIR / "episodic_memory.jsonl")
        dropped = collapse_duplicate_episodes(store, dry_run=dry_run)
        if dropped:
            _log_tick(workspace, {
                "event": "episodic_duplicates_collapsed",
                "count": len(dropped),
                "dry_run": dry_run,
                "applied": not dry_run,
            })
        return len(dropped)
    except Exception as exc:  # noqa: BLE001 — hygiene must not cost the tick its work
        _log_tick(workspace, {"event": "episodic_sweep_error",
                              "error": f"{type(exc).__name__}: {exc}"})
        return 0


class BudgetConfigMissing(RuntimeError):
    """Живой тик запущен там, где денежного потолка нет вовсе."""


def _require_budget_config(workspace: Path) -> None:
    """Отказать безнадзорному тику, если файла лимитов нет.

    H-33 в docs/audit/HISTORICAL_FAILURE_LEDGER.md. Замер 2026-08-24: весь
    денежный потолок агента держится на ОДНОМ файле — лимитов в окружении нет
    ни одного, — и при его отсутствии окна остаются с нулевыми пределами:
    `reserve` пропускает 500 вызовов подряд, молча. Тот же файл с ПОРЧЕНЫМ
    содержимым бросает. То есть из двух ошибок настройки тихо проходила ровно
    та, что СНИМАЕТ ограничение.

    Проверка стоит здесь, а не в `budget_ledger`: путь к конфигу называют все
    вызывающие по умолчанию, поэтому «назван» там не значит «оператор его
    завёл» — попытка различить это на слое библиотеки покраснила 168 тестов и
    правильно сделала. Ожидание «потолок настроен» принадлежит живому входу.

    Переменная `AGENT_BUDGET_CONFIG_PATH` уважается: она и есть законный
    способ держать лимиты в другом месте.

    ГДЕ ОНА СТОИТ, и это третья попытка. Слой библиотеки отпал: путь к конфигу
    называют все вызывающие, и различение «назван/существует» покраснило
    168 тестов. `run_tick` отпал: его зовут напрямую 17 тестов на временных
    папках. Условие «есть ключ» отпало тоже — ключ живёт в оболочке
    разработчика, и тесты его наследуют.

    Настоящая граница — ВХОД ПРОЦЕССА. Плановый безнадзорный тик всегда
    приходит через `main()`; тесты зовут `run_tick` внутри себя. Проверка
    поэтому стоит в `main()`, и разделяет она ровно то, что разделяет
    реальность, а не то, что удобно назвать.
    """
    named = os.environ.get("AGENT_BUDGET_CONFIG_PATH", "").strip()
    path = Path(named) if named else workspace / BUDGET_CONFIG_PATH
    if path.exists():
        return
    raise BudgetConfigMissing(
        f"budget limits file not found at {path}. An unattended tick refuses "
        f"to run without a spending cap: with no file and no AGENT_BUDGET_* "
        f"limits, every window's limit is zero, which reads as NO limit rather "
        f"than as no spending. Restore the file or point "
        f"AGENT_BUDGET_CONFIG_PATH at it."
    )


def _take_daily_snapshot(workspace: Path) -> None:
    """Снять суточный снимок состояния, если сегодняшнего ещё нет.

    H-51 в docs/audit/HISTORICAL_FAILURE_LEDGER.md, решение оператора
    2026-08-25. Замер того дня: 19 живых хранилищ из 24 не имели НИ ОДНОЙ
    копии — среди них журнал бюджета и ящик одобрений, — а `data/` исключён из
    git, то есть версионный контроль запасным путём не является.

    Зовётся КАЖДЫЙ тик, а работает раз в сутки: идемпотентность живёт в самом
    снимке, а не в расписании, потому что расписание — ещё одно место, где
    можно ошибиться.

    Никогда не роняет тик. Снимок — страховка; страховка, способная остановить
    работу, хуже её отсутствия.
    """
    try:
        from scripts.snapshot_state import take_snapshot

        target, copied, pruned = take_snapshot(workspace)
    except Exception as exc:  # noqa: BLE001 — страховка не вправе ронять тик
        _log_tick(workspace, {"event": "state_snapshot_failed",
                              "error": f"{type(exc).__name__}: {exc}"[:200]})
        return
    if target is not None:
        _log_tick(workspace, {"event": "state_snapshot",
                              "path": str(target), "files": copied,
                              "pruned": pruned})


def run_tick(workspace: Path, *, dry_run: bool = True) -> int:
    """Execute one daemon tick. Returns exit code (0 = ok, 1 = hard error)."""
    _ensure_env_loaded(workspace)
    _take_daily_snapshot(workspace)

    # Full-suite budget; the basis lives with DEFAULT_TIMEOUT_SECONDS in
    # tools/run_tests.py. RunTestsTool reads this when given no explicit value.
    os.environ.setdefault("AGENT_TEST_TIMEOUT_SECONDS", "900")

    # Lazy import after dotenv so env vars are available
    from app.bootstrap import build_agent
    from app.single_instance import AlreadyRunningError, SingleInstanceLock
    from core.approval_inbox import ApprovalInbox
    from core.autonomous_runtime import AutonomousRuntime, _config_from_task
    from core.incident import IncidentLog
    from core.scheduler import SchedulerStore
    from core.task_lifecycle import (
        apply_run_exception,
        apply_run_outcome,
        task_heartbeat,
    )
    from core.task_queue import TaskAlreadyClaimed, TaskQueueStore

    tick_start = _now_iso()
    summary: dict = {
        "tick_start": tick_start,
        "workspace": str(workspace),
        "dry_run": dry_run,
        "schedules_due": 0,
        "tasks_enqueued": 0,
        "tasks_processed": 0,
        "tests_result": None,
        "tests_health": "none",
        # Honest per-task test verdict, kept SEPARATE from run_status. Values:
        # "none" | "done" | "failed" | "inconclusive" | "skipped". A timed-out /
        # exit_code-less run is "inconclusive", never silently "done".
        "result_status": "none",
        "repair_proposed": False,
        "self_build_status": "none",
        "inbox_pending_after": 0,
        "error": None,
    }

    # Dry-run visibility (observability only — never changes behaviour). Read
    # the PREVIOUS heartbeat before overwriting it so the streak is accurate.
    # Extraction lives here (impure I/O); the helper itself stays pure.
    _prev_hb = _read_heartbeat(workspace)
    try:
        _prev_streak = max(0, int((_prev_hb or {}).get("dry_run_streak", 0) or 0))
    except (TypeError, ValueError):
        _prev_streak = 0
    # At tick_start no work has happened yet, so carry the streak forward
    # (did_work=False). The final tick_complete recomputes it once we know
    # whether this tick actually ran a dry-run pass. An idle no-op or an early
    # crash therefore never inflates the dry-run-stall signal.
    visibility = _dry_run_visibility(
        dry_run=dry_run,
        previous_streak=_prev_streak,
        processed_effects=0,  # no effect is ever applied in this layer
        did_work=False,
    )
    summary.update(visibility)

    # Heartbeat #1: record that a tick STARTED before doing any work. Even if
    # the tick later crashes, the liveness file proves the daemon is running.
    _write_heartbeat(
        workspace,
        {"event": "tick_start", "tick_start": tick_start, "dry_run": dry_run,
         **visibility},
    )

    # Bound before the try so the error path can always release it.
    _consumer_lock: SingleInstanceLock | None = None
    try:
        # ── 0. Budget kill-switch gate (TD-022) ───────────────────────────────
        # Safety-by-default: if the persistent DAY budget is exhausted, skip all
        # LLM-heavy work this tick. Checked before the scheduler/agent are even
        # built so no planner/synthesizer/provider call can happen.
        kill_switch = _check_budget_kill_switch(workspace)
        if kill_switch.active:
            ks_payload = kill_switch.to_dict()
            summary["error"] = None
            summary["budget_kill_switch"] = ks_payload
            summary["result_status"] = "budget_kill_switch"
            summary["tick_end"] = _now_iso()
            _log_tick(workspace, {"event": "budget_kill_switch", **ks_payload})
            _write_heartbeat(
                workspace,
                {
                    "event": "budget_kill_switch",
                    "tick_start": tick_start,
                    "dry_run": dry_run,
                    **ks_payload,
                    **visibility,
                },
            )
            print(
                "[agent_tick] budget kill-switch active "
                f"({kill_switch.counter} {kill_switch.used}/{kill_switch.limit}); "
                "skipping LLM-heavy work",
                file=sys.stderr,
            )
            return 0

        # ── 1. Scheduler tick — enqueue due schedules ─────────────────────────
        sched_store = SchedulerStore(workspace / SCHEDULES_PATH)
        task_store  = TaskQueueStore(workspace / TASK_QUEUE_PATH)
        tick_report = sched_store.tick(task_queue=task_store)
        summary["schedules_due"]   = tick_report.due_count
        summary["tasks_enqueued"]  = tick_report.enqueued_count
        _log_tick(workspace, {"event": "scheduler_tick", **tick_report.to_dict()})

        # ── 2. Claim and run pending tasks ────────────────────────────────────
        # Draining the queue happens under the single-instance lock. Two things
        # depend on it: no second consumer may claim the same task, and startup
        # recovery may only reclaim an abandoned row when nothing else can be
        # holding one in flight. If another consumer holds the lock, this tick
        # skips the drain and does the rest of its work — that is the correct
        # outcome, not an error.
        _consumer_lock = SingleInstanceLock(workspace / DAEMON_LOCK_PATH)
        try:
            _consumer_lock.acquire()
        except AlreadyRunningError as exc:
            _consumer_lock = None
            summary["task_drain"] = "skipped_locked"
            _log_tick(workspace, {"event": "task_drain_skipped",
                                  "reason": "another consumer holds the lock",
                                  "details": getattr(exc, "details", {})})

        pending_tasks = []
        if _consumer_lock is not None:
            _free_stranded_rows(task_store, lock=_consumer_lock,
                                workspace=workspace, dry_run=dry_run)
            # Same slot, same reason: the unattended path generates the most
            # repeats and was the only path that could not clean them up —
            # 43 identical episodes accumulated here on 2026-08-16 (MIR-131).
            _sweep_episodic_duplicates(workspace, dry_run=dry_run)
            # `pending()` rather than `list(status="pending")`: it honours
            # `run_after`, so a task re-queued behind the retry backoff is not
            # immediately re-run by this consumer.
            pending_tasks = task_store.pending()

        if not pending_tasks:
            if _consumer_lock is not None:
                _log_idle_tick(workspace)
            # Still check approval inbox below
        else:
            agent = build_agent(
                workspace, approval_provider=None,
                **unattended_memory_profile(workspace),
            )
            inbox = ApprovalInbox(path=workspace / APPROVAL_INBOX_PATH)
            incident_log = IncidentLog(path=workspace / INCIDENT_LOG_PATH)
            runtime = AutonomousRuntime(
                agent,
                workspace=workspace,
                approval_inbox=inbox,
                incident_log=incident_log,
                receipt_path="daemon",
            )

            for task in pending_tasks:
                try:
                    task_store.mark_running(task.id)
                except TaskAlreadyClaimed:
                    # Until 2026-08-04 this caught `Exception` and the comment
                    # claimed it meant "already claimed" — but `mark_running`
                    # never refused a second claim, so the guard was decorative
                    # and two consumers did run the same task.
                    _log_tick(workspace, {"event": "task_claim_lost",
                                          "task_id": task.id})
                    continue
                except KeyError:
                    _log_tick(workspace, {"event": "task_vanished",
                                          "task_id": task.id})
                    continue

                config = _config_from_task(task)
                # Always honour dry_run flag from CLI / env
                import dataclasses
                if dry_run and not config.dry_run:
                    config = dataclasses.replace(config, dry_run=True)

                try:
                    # Heartbeat while the run happens, so a killed process is
                    # distinguishable from a slow one and startup recovery can
                    # act without risking a double run (MIR-040).
                    with task_heartbeat(task_store, task.id):
                        run_report = runtime.run(config)
                except Exception as exc:   # noqa: BLE001
                    # Previously this escaped to the outer handler, which wrote
                    # an error heartbeat and returned WITHOUT touching the task —
                    # leaving it `running` forever with nothing in the live
                    # system able to recover it (MIR-039 + MIR-040).
                    failed, decision = apply_run_exception(
                        task_store, task.id, exc
                    )
                    summary["tasks_processed"] += 1
                    summary["result_status"] = "failed"
                    _log_tick(workspace, {
                        "event": "task_failed",
                        "task_id": task.id,
                        "status": failed.status,
                        "error_type": type(exc).__name__,
                        **decision.to_log_payload(),
                    })
                    continue

                summary["tasks_processed"] += 1

                # Per-task honest verdict (kept distinct from run_status below).
                task_result_status = "none"

                # Record test outcomes
                for task_report in run_report.tasks:
                    if task_report.status == "clarify":
                        summary["clarification"] = task_report.details.get("clarification")
                        summary["clarification_stop_reason"] = task_report.details.get(
                            "stop_reason", "replan_exhausted"
                        )
                        task_result_status = "clarify"
                        summary["result_status"] = "clarify"
                        _log_tick(
                            workspace,
                            {
                                "event": "clarification_required",
                                "task_id": task.id,
                                "stop_reason": summary["clarification_stop_reason"],
                                "clarification": summary["clarification"],
                            },
                        )
                    if task_report.task.kind == "tests":
                        summary["tests_result"] = {
                            "status": task_report.status,
                            "summary": task_report.summary,
                            **task_report.details,
                        }
                        summary["tests_health"] = _classify_test_health(
                            summary["tests_result"]
                        )
                        # The runtime already classified done/failed/inconclusive;
                        # propagate it verbatim as the result_status of this task.
                        task_result_status = task_report.status
                        summary["result_status"] = task_report.status
                        if task_report.status == "failed":
                            repair_result = _maybe_propose_repair(
                                workspace, task_report.details, inbox, agent
                            )
                            summary["repair_proposed"] = repair_result.get(
                                "repair_proposed", False
                            )
                            _log_tick(workspace, {"event": "repair_attempt", **repair_result})
                        elif summary["tests_health"] == "inconclusive":
                            # Do NOT propose a repair (no specific failing test),
                            # but never let this pass silently as healthy.
                            _log_tick(
                                workspace,
                                {
                                    "event": "tests_inconclusive",
                                    "reason": task_report.summary,
                                    "details": task_report.details,
                                },
                            )

                # One shared mapping with the CLI/campaign consumer: completed →
                # done, blocked-on-approval → blocked, stopped → failed with the
                # stop reason. The unconditional `mark_done` this replaces
                # recorded a budget-stopped or approval-blocked run as a success
                # (MIR-039).
                updated, decision = apply_run_outcome(
                    task_store,
                    task.id,
                    status=run_report.status,
                    stop_reason=run_report.stop_reason,
                    report=run_report.to_dict(),
                )
                # run_status = did the run finish (completed); result_status =
                # what the work actually established (done/failed/inconclusive);
                # task_status = where the queue row ended up. Three axes, kept
                # apart on purpose. mode/processed_effects make it explicit that
                # a dry-run task applied nothing.
                _log_tick(workspace, {"event": "task_done", "task_id": task.id,
                                      "run_status": run_report.status,
                                      "task_status": updated.status,
                                      "lifecycle_reason": decision.reason,
                                      "result_status": task_result_status,
                                      "mode": summary["mode"],
                                      "effects": summary["effects"],
                                      "processed_effects": summary["processed_effects"]})

        # The queue is drained; nothing below claims tasks, so hand the
        # single-instance lock back before the slower producer work.
        if _consumer_lock is not None:
            _consumer_lock.release()
            _consumer_lock = None

        # ── 3. Autonomous self-build producer (TD-026) ────────────────────────
        # At most one proposal per tick, gated by a persistent cooldown that runs
        # BEFORE any agent is built. Producer owns kill-switch/budget/approval/
        # dirty-tree gates; this only adds cooldown. It NEVER applies/commits/
        # pushes and NEVER runs the self-apply lane — it only proposes into the
        # human-gated approval inbox (safe in dry-run too).
        producer_inbox = ApprovalInbox(path=workspace / APPROVAL_INBOX_PATH)
        self_build = _maybe_produce_self_build(
            workspace, producer_inbox, dry_run=dry_run
        )
        summary["self_build_status"] = self_build.get("self_build_status", "none")
        summary["self_build"] = self_build
        _log_tick(workspace, {"event": "self_build_produce", **self_build})

        # ── 4. Tally inbox ────────────────────────────────────────────────────
        inbox_check = ApprovalInbox(path=workspace / APPROVAL_INBOX_PATH)
        summary["inbox_pending_after"] = len(inbox_check.pending())

    except Exception as exc:  # noqa: BLE001
        # Release before reporting: a crashed tick must not leave the queue
        # locked for the next one. (The OS would release it when this process
        # exits, but run_tick is also called in-process.)
        if _consumer_lock is not None:
            try:
                _consumer_lock.release()
            except Exception:  # noqa: BLE001, S110 — the OS frees this lock at exit either way
                pass
            _consumer_lock = None
        summary["error"] = f"{type(exc).__name__}: {exc}"
        _log_tick(workspace, {"event": "tick_error", "error": summary["error"],
                              "traceback": traceback.format_exc()})
        _write_heartbeat(
            workspace,
            {
                "event": "tick_error",
                "tick_start": tick_start,
                "dry_run": dry_run,
                "error": summary["error"],
                **visibility,
            },
        )
        print(f"[agent_tick] ERROR: {exc}", file=sys.stderr)
        return 1

    summary["tick_end"] = _now_iso()
    # Recompute the streak now that we know whether this tick did real work.
    # mode/effects/processed_effects are unchanged by did_work; only the streak
    # differs: a working dry-run pass increments, an idle no-op carries forward.
    visibility = _dry_run_visibility(
        dry_run=dry_run,
        previous_streak=_prev_streak,
        processed_effects=0,
        did_work=summary["tasks_processed"] > 0,
    )
    summary.update(visibility)

    # ── Memory maintenance ───────────────────────────────────────────────────
    # Runs here because this is the path with no human on it: interactively an
    # operator can type `:hygiene`, unattended nobody will, and memory grows
    # until someone notices (MIR-045).
    #
    # Placed AFTER the work summary so a maintenance problem can never change
    # the tick's verdict, and gated by AGENT_AUTO_HYGIENE:
    #   shadow (default) — count and report, remove nothing
    #   on               — actually remove
    #   off              — skip entirely
    # Shadow is the default deliberately: these thresholds have only ever been
    # exercised against synthetic data, and the pass deletes.
    #
    # `on` is the operator's consent to DELETE, not a repeal of `dry_run`. The
    # burn-in audit (2026-09-17) found the mode variable deciding alone, so an
    # env var set once could make every "dry" tick destructive. Two permissions
    # must now agree: the tick must be live AND the mode must say `on`.
    _hygiene_mode = os.environ.get("AGENT_AUTO_HYGIENE", "shadow").strip().lower()
    if _hygiene_mode in {"shadow", "on"}:
        try:
            _hyg_agent = build_agent(
                workspace, approval_provider=None,
                **unattended_memory_profile(workspace),
            )
            _hyg_shadow = dry_run or _hygiene_mode == "shadow"
            _hyg = _hyg_agent.run_maintenance_pass(dry_run=_hyg_shadow)
            _log_tick(workspace, {"event": "maintenance_pass",
                                  "mode": _hygiene_mode,
                                  "tick_dry_run": dry_run, **_hyg})
        except Exception as exc:  # noqa: BLE001
            _log_tick(workspace, {"event": "maintenance_error",
                                  "error": type(exc).__name__})

    try:
        from core.rule_approved_apply import drain_rule_approved_proposals

        summary["rule_approved"] = drain_rule_approved_proposals(
            workspace, dry_run=dry_run,
            log=lambda e, p: _log_tick(workspace, {"event": e, **p}),
            # Один источник истины о стоках: тот же профиль, которым собирается
            # безнадзорный агент. Урок — долговременная запись и идёт теми же
            # воротами (ревизия PR #333).
            durable_writes=unattended_memory_profile(workspace)["durable_writes"],
        )
    except Exception as exc:  # noqa: BLE001 — замыкание петли не вправе ронять тик
        _log_tick(workspace, {"event": "rule_approval_error",
                              "error": type(exc).__name__})

    _log_tick(workspace, {"event": "tick_complete", **summary})

    # Heartbeat #2: record successful completion with the honest health verdict.
    _write_heartbeat(
        workspace,
        {
            "event": "tick_complete",
            "tick_start": tick_start,
            "tick_end": summary["tick_end"],
            "dry_run": dry_run,
            "tests_health": summary["tests_health"],
            "result_status": summary["result_status"],
            "tasks_processed": summary["tasks_processed"],
            "inbox_pending_after": summary["inbox_pending_after"],
            "self_build_status": summary["self_build_status"],
            "self_build_next_human_action": (
                summary.get("self_build", {}).get("next_human_action", "")
            ),
            "self_build_approval_id": (
                summary.get("self_build", {}).get("approval_id")
            ),
            **visibility,
        },
    )

    # Human-readable summary to stderr (captured by Task Scheduler logs)
    pending = summary["inbox_pending_after"]
    tests   = summary["tests_result"]
    health  = summary["tests_health"]
    # Honest, machine-greppable visibility line: never implies effects ran.
    print(
        f"[agent_tick] mode={summary['mode']} effects={summary['effects']} "
        f"processed_effects={summary['processed_effects']} "
        f"dry_run_streak={summary['dry_run_streak']}",
        file=sys.stderr,
    )
    if tests:
        print(
            f"[agent_tick] tests_health={health} "
            f"result_status={summary['result_status']} "
            f"({tests.get('summary','')})",
            file=sys.stderr,
        )
        if health == "inconclusive":
            print(
                "[agent_tick] WARNING: test run was inconclusive "
                "(timeout / no exit code / nothing collected) — "
                "NOT treating this tick as healthy.",
                file=sys.stderr,
            )
    if summary.get("clarification"):
        clar = summary["clarification"]
        print(
            "[agent_tick] clarification required "
            f"({summary.get('clarification_stop_reason', 'replan_exhausted')}):",
            file=sys.stderr,
        )
        for question in clar.get("questions") or []:
            print(f"  - {question}", file=sys.stderr)
        forbidden = clar.get("forbidden_actions") or []
        if forbidden:
            print(
                f"  forbidden while unclear: {', '.join(forbidden)}",
                file=sys.stderr,
            )
    if summary["repair_proposed"]:
        print("[agent_tick] Repair proposal added to inbox.", file=sys.stderr)
    sb = summary.get("self_build", {})
    sb_status = summary["self_build_status"]
    if sb_status == "proposed":
        print(
            f"[agent_tick] Self-build proposal added to inbox "
            f"({sb.get('approval_id')}). {sb.get('next_human_action', '')}",
            file=sys.stderr,
        )
    elif sb_status not in ("none", "cooldown_wait"):
        print(_self_build_status_line(sb_status, sb), file=sys.stderr)
    if pending:
        print(f"[agent_tick] {pending} item(s) waiting in approval inbox.", file=sys.stderr)
    else:
        print("[agent_tick] Inbox clear.", file=sys.stderr)

    return 0


# ── paced campaign daemon mode ────────────────────────────────────────────────

def run_paced_campaign(
    workspace: Path,
    *,
    dry_run: bool = True,
    goal: str = "project health",
    success_check: str = "",
    max_cycles: int = 24,
    cycle_pause_seconds: int = 0,
    max_wall_clock_seconds: int = 0,
    max_llm_calls: int = 100,
    max_cost_units: int = 0,
    max_unproductive_streak: int = 3,
    heartbeat_fn: Callable[[Path, dict], None] | None = None,
    charter_goals: bool = False,
    run_campaign_fn: Callable[..., Any] | None = None,
    build_agent_fn: Callable[[Path], Any] | None = None,
) -> int:
    """Run ONE long, PACED autonomous campaign as a single daemon process.

    Unlike :func:`run_tick` (a single bounded pass driven externally by Task
    Scheduler), this drives the multi-cycle campaign loop itself over real
    wall-clock time: it sleeps ``cycle_pause_seconds`` between cycles and stops
    at ``max_wall_clock_seconds``. Crucially it emits a heartbeat PER CYCLE so
    the operator's ``--status`` view shows liveness throughout a multi-hour
    unattended run instead of going dark between the start and the end.

    Dry-run by default; effects still flow only through the existing approval
    gate inside the campaign. The heavy collaborators (heartbeat writer, the
    campaign loop, agent builder) are injectable so the wiring is testable with
    deterministic fakes. Returns an exit code (0 = ok, 1 = hard error).
    """
    from core.campaign import (
        CampaignConfig,
        CampaignLedger,
    )
    from core.campaign import (
        run_campaign as _real_run_campaign,
    )

    write_heartbeat = heartbeat_fn or _write_heartbeat
    run_campaign = run_campaign_fn or _real_run_campaign

    def _on_cycle(snapshot: dict) -> None:
        # Liveness during a long paced run. A heartbeat-write failure (e.g. a
        # transient disk error) must NEVER kill a multi-hour campaign, so the
        # per-cycle write is best-effort at this daemon layer (the core seam
        # in run_campaign stays pure).
        payload = {
            "event": "campaign_cycle",
            "dry_run": dry_run,
            "mode": "dry_run" if dry_run else "live",
            "effects": "disabled" if dry_run else "enabled",
            **snapshot,
        }
        try:
            write_heartbeat(workspace, payload)
        except (OSError, TypeError, ValueError):
            pass

    try:
        config = CampaignConfig(
            goal=goal,
            success_check=success_check,
            dry_run=dry_run,
            max_cycles=max_cycles,
            max_llm_calls=max_llm_calls,
            max_cost_units=max_cost_units,
            cycle_pause_seconds=cycle_pause_seconds,
            max_wall_clock_seconds=max_wall_clock_seconds,
            max_unproductive_streak=max_unproductive_streak,
        )
    except ValueError as exc:
        print(f"[agent_tick] campaign config error: {exc}", file=sys.stderr)
        return 1

    # Heartbeat #1: prove the campaign process started before any cycle runs.
    write_heartbeat(workspace, {
        "event": "campaign_start",
        "dry_run": dry_run,
        "mode": "dry_run" if dry_run else "live",
        "effects": "disabled" if dry_run else "enabled",
        "processed_effects": 0,
        "goal": config.goal,
        "max_cycles": config.max_cycles,
        "cycle_pause_seconds": config.cycle_pause_seconds,
        "max_wall_clock_seconds": config.max_wall_clock_seconds,
    })

    # Build the agent + inbox + ledger lazily (mirrors run_tick).
    if build_agent_fn is not None:
        agent = build_agent_fn(workspace)
    else:
        from dotenv import load_dotenv
        load_dotenv(workspace / ".env")
        ensure_roster_home(workspace)  # дом реестра молчавших (Д5)
        from app.bootstrap import build_agent
        agent = build_agent(
            workspace, approval_provider=None,
            **unattended_memory_profile(workspace),
        )

    from core.approval_inbox import ApprovalInbox
    inbox = ApprovalInbox(path=workspace / APPROVAL_INBOX_PATH)
    ledger = CampaignLedger(path=workspace / DATA_DIR / "campaign_ledger.jsonl")

    print(
        f"[agent_tick] paced campaign goal={config.goal!r} dry_run={dry_run} "
        f"max_cycles={config.max_cycles} pause={config.cycle_pause_seconds}s "
        f"ceiling={config.max_wall_clock_seconds}s",
        file=sys.stderr,
    )

    # Право сменить исчерпанную цель ВНУТРИ прогона (слово оператора
    # 2026-09-01). Ворота те же, что на старте: цель выбирает сам агент по
    # хартии, и хартия так же вправе отказать — отказ означает, что прогон
    # честно заканчивается, а не обходит правило.
    #
    # Замер, из-за которого это понадобилось: узкая инженерная цель
    # исчерпывается за ОДИН цикл (предложение произведено и ушло ждать
    # человека), после чего кампания умирала за четыре минуты, повторяя одно и
    # то же; с широкой целью тот же агент дал 21 полезный цикл из 21.
    def _pick_next_goal() -> Any:
        """Вернуть ОТЧЁТ о выбранной цели, а не одну её строку.

        Аудит автономности 2026-09-17, находка 4: хартия требует от каждой
        цели критерий успеха («цель без проверки — желание»), отчёт его несёт,
        а здесь наружу уезжала одна строка `pick.goal`. Критерий умирал в
        месте выбора, и исполнителю было НЕЧЕМ судить собственную работу.

        Кампания принимает и строку, и отчёт; пустая строка по-прежнему
        означает отказ.
        """
        try:
            from core.charter_goal import propose_charter_goal
            router = _charter_goal_router(workspace)
            pick = propose_charter_goal(router.for_role("planner"), workspace)
        except Exception as exc:  # noqa: BLE001 — смена цели не имеет права
            # уронить прогон: не вышло — останавливаемся прежним путём.
            print(f"[CHARTER] next goal failed: {type(exc).__name__}: {exc}")
            return ""
        if pick.status != "proposed":
            print(f"[CHARTER] no next goal: {pick.reason}")
            return ""
        print(f"[CHARTER] next goal: {pick.goal}")
        print(f"[CHARTER] anchored to: {pick.charter_quote!r}")
        print(f"[CHARTER] success check: {pick.success_check}")
        return pick

    def _call_run_campaign(**extra):
        try:
            return run_campaign(
                config, agent=agent, workspace=workspace,
                approval_inbox=inbox, ledger=ledger, on_cycle=_on_cycle,
                **extra,
            )
        except TypeError:
            if not extra:
                raise
            # Старая сигнатура без next_goal — прогон продолжается без права
            # смены цели, а не умирает из-за нового необязательного параметра.
            return run_campaign(
                config, agent=agent, workspace=workspace,
                approval_inbox=inbox, ledger=ledger, on_cycle=_on_cycle,
            )

    try:
        result = _call_run_campaign(
            **({"next_goal": _pick_next_goal} if charter_goals else {}),
        )
    except Exception as exc:  # noqa: BLE001 — the failure lands in the heartbeat
        write_heartbeat(workspace, {
            "event": "campaign_error",
            "dry_run": dry_run,
            "error": f"{type(exc).__name__}: {exc}",
        })
        print(
            f"[agent_tick] campaign failed: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 1

    # Final heartbeat + honest tick-log summary.
    write_heartbeat(workspace, {
        "event": "campaign_complete",
        "dry_run": dry_run,
        "mode": "dry_run" if dry_run else "live",
        "effects": "disabled" if dry_run else "enabled",
        "processed_effects": 0,
        "status": result.status,
        "stop_reason": result.stop_reason,
        "cycles_run": result.cycles_run,
        **result.totals,
    })
    _log_tick(workspace, {
        "event": "campaign_complete",
        "status": result.status,
        "stop_reason": result.stop_reason,
        "cycles_run": result.cycles_run,
        "totals": result.totals,
    })
    drain_and_log(
        workspace, dry_run=dry_run,
        log_tick=lambda p: _log_tick(workspace, p),
        durable_writes=unattended_memory_profile(workspace)["durable_writes"],
    )
    print(result.user_summary(), file=sys.stderr)
    return 0


# ── entry point ───────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Autonomous agent daemon tick.")
    parser.add_argument(
        "--workspace",
        default=str(WORKSPACE_DEFAULT),
        help="Path to workspace root (default: directory of this script).",
    )
    parser.add_argument(
        "--allow-effects",
        action="store_true",
        help="Disable dry-run; allow the runtime to write files (use with care).",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Only print pending inbox items and exit; do not run a tick.",
    )
    parser.add_argument(
        "--standing-grant", nargs="+", metavar="N",
        help="Open a standing grant for unattended runs, then exit: "
             "<runs per day> [hours, default 48].",
    )
    parser.add_argument(
        "--campaign",
        action="store_true",
        help="Run a long PACED campaign in this process (heartbeat per cycle) "
             "instead of one bounded tick. Use with the pacing flags below.",
    )
    parser.add_argument(
        "--goal",
        default="project health",
        help="Campaign goal (only used with --campaign).",
    )
    parser.add_argument(
        "--charter",
        action="store_true",
        help="Let the agent pick its own campaign goal from the charter "
             "(knowledge/doctrine/future/CORPORATE_MODEL.md) instead of --goal. "
             "A declined pick exits with the named gate, not a fallback goal.",
    )
    parser.add_argument(
        "--max-cycles",
        type=int,
        default=24,
        help="Max campaign cycles (only used with --campaign).",
    )
    parser.add_argument(
        "--cycle-pause-seconds",
        type=int,
        default=0,
        help="Real wall-clock pause between cycles (only used with --campaign).",
    )
    parser.add_argument(
        "--max-wall-clock-seconds",
        type=int,
        default=0,
        help="Hard real-time ceiling for the campaign (only used with --campaign).",
    )
    parser.add_argument(
        "--max-llm-calls",
        type=int,
        default=100,
        help="Campaign llm-call budget, 0 = unlimited (only used with --campaign).",
    )
    parser.add_argument(
        "--max-cost-units",
        type=int,
        default=0,
        help="Campaign cost-unit budget, 0 = unlimited (only used with --campaign).",
    )
    parser.add_argument(
        "--max-unproductive-streak",
        type=int,
        default=3,
        help="Stop the campaign after this many consecutive executed cycles "
             "with no useful state change (loop_suspected); 0 = off "
             "(only used with --campaign).",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    ws   = Path(args.workspace).resolve()

    if args.status:
        sys.exit(_print_status(ws))

    if args.standing_grant:
        # Право выдаёт РАЗБОР АРГУМЕНТОВ, а не код агента: вердикт подписан
        # человеком, набравшим флаг (см. core/standing_grant.py).
        from core.standing_grant import open_grant_from_command_line
        sys.exit(open_grant_from_command_line(ws, *args.standing_grant))

    _require_budget_config(ws)

    dry = not args.allow_effects
    # Respect env var override too
    if os.environ.get("AGENT_TICK_DRY_RUN", "1").strip().lower() in {"0", "false", "no"}:
        dry = False

    goal = args.goal
    # Критерий успеха цели. Пустая строка = «не назван»: цель из командной
    # строки приходит без него, и это честное состояние (core/success_check.py).
    success_check = ""
    if args.campaign and args.charter:
        # Цель выбирает агент — от хартии; отказ выходит с названными воротами,
        # а не подменяется целью по умолчанию (см. core/charter_goal.py).
        # .env грузится ЗДЕСЬ: `load_dotenv` живёт внутри run_tick и пейсера
        # кампании, то есть ПОЗЖЕ этого блока — и все плановые тики выбирали
        # цель на модели по умолчанию вместо закреплённой (вскрытие 19:31).
        _ensure_env_loaded(ws)
        from core.charter_goal import propose_charter_goal

        _charter_router = _charter_goal_router(ws)
        # Несколько попыток выбрать цель на СТАРТЕ. Замер 2026-09-01T23:27:
        # прогон умирал на первой же отвергнутой цели, потому что попытка была
        # ровно одна — притом внутри прогона право сменить исчерпанную цель уже
        # есть. Стартовать оказалось труднее, чем продолжать.
        #
        # Каждая неудачная попытка ОСТАВЛЯЕТ СЛЕД: запись стены и повод для
        # причинной лестницы. Тишины здесь быть не должно — иначе выбор снова
        # станет невидимым для него самого.
        pick = None
        for _attempt in range(_GOAL_PICK_ATTEMPTS):
            pick = propose_charter_goal(_charter_router.for_role("planner"), ws)
            if pick.status == "proposed":
                break
            print(f"[CHARTER] no goal (attempt {_attempt + 1}"
                  f"/{_GOAL_PICK_ATTEMPTS}): {pick.reason}")
            from core.self_stop_record import (
                reason_kind,
                record_self_stop,
                record_stop_observation,
            )
            _wall = reason_kind(pick.reason)
            _stop = record_self_stop(
                kind="goal_selection_failure",
                source="data/charter_decisions.jsonl",
                reason=_wall,
                ts=datetime.now(timezone.utc).isoformat(),
                outcome=pick.status,
                workspace=ws,
            )
            record_stop_observation(
                ws,
                kind="goal_selection_failure",
                reason=_wall,
                signature=str(_stop.get("signature") or ""),
                source="data/charter_decisions.jsonl",
            )
        if pick is None or pick.status != "proposed":
            print("[CHARTER] no goal after "
                  f"{_GOAL_PICK_ATTEMPTS} attempts; stopping honestly")
            sys.exit(3)
        print(f"[CHARTER] goal: {pick.goal}")
        print(f"[CHARTER] anchored to: {pick.charter_quote!r}")
        print(f"[CHARTER] success check: {pick.success_check}")
        goal = pick.goal
        success_check = pick.success_check

    if args.campaign:
        sys.exit(run_paced_campaign(
            ws,
            dry_run=dry,
            goal=goal,
            success_check=success_check,
            max_cycles=args.max_cycles,
            cycle_pause_seconds=args.cycle_pause_seconds,
            max_wall_clock_seconds=args.max_wall_clock_seconds,
            max_llm_calls=args.max_llm_calls,
            max_cost_units=args.max_cost_units,
            max_unproductive_streak=args.max_unproductive_streak,
            charter_goals=bool(args.charter),
        ))

    sys.exit(run_tick(ws, dry_run=dry))
