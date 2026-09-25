"""Мерило полезности: пригодилась ли работа цикла ПОТОМ (слово оператора 2026-09-25).

Замер того же дня по журналу кампаний 19–24.09: «сделано» засчитывалось за
любой текст ответа (935 «сделанных» циклов, 422 из них начинались с оговорки
«у меня есть данные только о…»), судья цели проверял лишь «файл появился» и
не видел 80 % целей. Польза нигде не мерилась. По литературе («Measuring
Self-Improvement», MAGELLAN) польза — это последующее использование, а не
наличие продукта.

Работа цикла засчитывается ПОЛЕЗНОЙ, если за `window_days` после цикла
случилось хоть одно из признаков. Четыре утверждены оператором, три добавлены
по его же слову того же дня («одобрение человека — один из источников, а не
единственный»; всё остальное — машинная проверка):

1. `approved` — человек одобрил заявку, поданную циклом (решения песочницы,
   постоянного разрешения и «без подписи» не в счёт);
2. `read_later` — файл, записанный или названный циклом, прочитан позже;
3. `memory_used` — запись долгой памяти, созданная в цикле, позже выдана в ход;
4. `code_survived` — правка кода агента из цикла стоит в основной ветке и не
   откачена;
5. `goal_verified` — судья цели (core/campaign_verdict.py) признал её критерий
   выполненным, И Клод подтвердил это вне хода (core/judge_queue.py; слово
   оператора 25.09: пока не судил Клод, решение предварительное, в зачёт нет);
6. `tests_passed` — цикл что-то создал и прогнал тесты, и они зелёные.

Заказы площадки считаются отдельно: принятая покупателем работа —
`market_accepted` в сводке (core/market_ledger.py).

Записи через `journal_append` продуктом не считаются: это служебные журналы,
система читает их постоянно, и «прочитано позже» ничего бы не значило.

ИЗВНЕ петли из них только `approved` (и принятые покупателем заказы): остальное
судит сам агент или его собственный судья. Долгий цикл, судящий себя, принимает
застой за прогресс (arXiv 2607.25152: нужен оценщик вне петли с независимым
доступом к миру), поэтому сводка считает подтверждённое извне отдельно —
`out_of_band`, — и расхождение двух чисел видно сразу.

Иначе — «сделано, пока без пользы»; моложе окна — «рано судить». Модуль только
считает: выбор целей он не двигает (сначала убедиться, что мерило не врёт).

Работа цикла — всё, что случилось между предыдущей строкой журнала кампании и
его собственной: кампания идёт строго по очереди, номера следа в строке нет.
"""
from __future__ import annotations

import json
import re
import subprocess
from bisect import bisect_right
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from core.judge_queue import rulings

WINDOW_DAYS = 3
SIGNALS = ("approved", "read_later", "memory_used", "code_survived", "goal_verified", "tests_passed")
#: Признаки, которые ставит не сам агент и не его судья (вне петли).
OUT_OF_BAND = frozenset({"approved", "goal_verified"})
_READ_TOOLS = frozenset({"file_read", "find_in_files", "diff_file", "convert_file"})
_PATH_RE = re.compile(r"(?<![\w/.])((?:data|proposals|knowledge|docs|core|tools|tests|cli|scripts|"
                      r"converted|math_study|knowledge_library)/[\w./-]+\.\w+)")
_AIN_RE = re.compile(r"\bain_[0-9a-f]{8,}\b")
_NOT_HUMAN = ("sandbox:", "standing_grant:", "unattributed", "rule:", "agent")
_AGENT_AUTHORS = frozenset({"Self-Apply Lane"})


def _ts(raw: Any) -> datetime | None:
    try:
        moment = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _rows(path: Path) -> Iterable[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            yield row.get("payload") if isinstance(row.get("payload"), dict) else row


@dataclass
class CycleUse:
    ts: str
    goal: str
    llm_calls: int
    products: set[str] = field(default_factory=set)
    approvals: set[str] = field(default_factory=set)
    memory_ids: set[str] = field(default_factory=set)
    commits: set[str] = field(default_factory=set)
    signals: dict[str, bool] = field(default_factory=dict)
    verdict: str = ""  # useful | not_yet | pending


@dataclass
class _Traces:
    """Сжатый след: только то, что нужно мерилу, по времени."""

    writes: list[tuple[datetime, str]] = field(default_factory=list)
    reads: list[tuple[datetime, str]] = field(default_factory=list)
    injected: list[tuple[datetime, str]] = field(default_factory=list)
    green_tests: list[tuple[datetime, str]] = field(default_factory=list)


def _scan_traces(logs: Path) -> _Traces:
    out = _Traces()
    test_calls: set[str] = set()
    for path in sorted(logs.glob("trace_*.jsonl")):
        try:
            fh = path.open(encoding="utf-8", errors="replace")
        except OSError:
            continue
        with fh:
            for line in fh:
                wanted = ('"tool_call"' in line or '"persistent_memory_inject"' in line
                          or (test_calls and '"tool_result"' in line))
                if not wanted:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                moment, payload = _ts(row.get("ts")), row.get("payload") or {}
                if moment is None:
                    continue
                if row.get("event") == "tool_result":
                    if payload.get("tool_call_id") in test_calls and _green(payload.get("output")):
                        out.green_tests.append((moment, "run_tests"))
                    continue
                if row.get("event") == "tool_call" and payload.get("tool_name") == "run_tests":
                    test_calls.add(str(payload.get("id")))
                    continue
                if row.get("event") == "persistent_memory_inject":
                    out.injected += [(moment, str(i)) for i in payload.get("ids") or []]
                    continue
                target = str((payload.get("arguments") or {}).get("path") or "").lstrip("./")
                if not target:
                    continue
                tool = payload.get("tool_name")
                if tool == "file_write":
                    out.writes.append((moment, target))
                elif tool in _READ_TOOLS:
                    out.reads.append((moment, target))
    for items in (out.writes, out.reads, out.injected, out.green_tests):
        items.sort()
    return out


def _green(output: Any) -> bool:
    """Зелёный прогон run_tests: код выхода 0 и хоть один прошедший тест."""
    if isinstance(output, str):
        try:
            output = json.loads(output)
        except ValueError:
            return False
    return isinstance(output, dict) and output.get("exit_code") == 0 and int(output.get("passed") or 0) > 0


def _verified_goals(data: Path) -> list[tuple[datetime, str]]:
    """Цели, признанные достигнутыми, — только подтверждённые Клодом."""
    confirmed = {i for i, r in rulings(data.parent).items() if r.get("verdict") == "confirmed"}
    return sorted((m, str(r.get("goal") or "")) for r in _rows(data / "campaign_verdicts.jsonl")
                  if r.get("verdict") == "verified" and r.get("judge_item") in confirmed
                  and (m := _ts(r.get("ts"))))


def _between(items: list[tuple[datetime, str]], start: datetime, end: datetime) -> list[str]:
    keys = [m for m, _ in items]
    lo, hi = bisect_right(keys, start), bisect_right(keys, end)
    return [v for _, v in items[lo:hi]]


def _human_approvals(data: Path) -> tuple[dict[str, datetime], dict[str, datetime]]:
    """(создана: id → время) и (одобрена человеком: id → время)."""
    created, approved = {}, {}
    for row in list(_rows(data / "approval_inbox.jsonl")) + list(_rows(data / "approval_outcomes.jsonl")):
        aid = str(row.get("id") or "")
        if not aid:
            continue
        if (moment := _ts(row.get("created_at"))) and aid not in created:
            created[aid] = moment
        status = str(row.get("status") or row.get("verdict") or "")
        who = str(row.get("decided_by") or "")
        if status in ("approved", "executed") and who and not who.startswith(_NOT_HUMAN):
            approved.setdefault(aid, _ts(row.get("updated_at") or row.get("ts")) or datetime.max.replace(
                tzinfo=timezone.utc))
    return created, approved


def _memory_created(data: Path) -> list[tuple[datetime, str]]:
    out = [(m, str(r.get("id"))) for r in _rows(data / "persistent_memory.jsonl")
           if (m := _ts(r.get("created_at"))) and r.get("id")]
    return sorted(out)


def _agent_commits(root: Path) -> tuple[list[tuple[datetime, str]], set[str]]:
    """Правки агента в основной ветке (время, sha) и откаченные sha."""
    try:
        out = subprocess.run(["git", "-C", str(root), "log", "--format=%H%x1f%an%x1f%cI%x1f%B%x1e", "main", "--"],  # noqa: S603, S607
                             capture_output=True, text=True, timeout=60, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return [], set()
    commits, reverted = [], set()
    for entry in out.split("\x1e"):
        parts = entry.strip().split("\x1f")
        if len(parts) < 4:
            continue
        sha, author, when, body = parts
        reverted |= set(re.findall(r"This reverts commit ([0-9a-f]{40})", body))
        if author in _AGENT_AUTHORS and (moment := _ts(when)):
            commits.append((moment, sha))
    return sorted(commits), reverted


def measure(root: Path | str, *, window_days: int = WINDOW_DAYS, now: datetime | None = None) -> list[CycleUse]:
    """Каждый цикл кампании с `work_done` — и пригодилась ли его работа."""
    root = Path(root)
    data, now = root / "data", now or datetime.now(timezone.utc)
    window = timedelta(days=window_days)
    traces = _scan_traces(root / "logs")
    created, approved = _human_approvals(data)
    memories = _memory_created(data)
    commits, reverted = _agent_commits(root)
    verified = _verified_goals(data)
    out: list[CycleUse] = []
    previous: datetime | None = None
    for row in _rows(data / "campaign_ledger.jsonl"):
        moment = _ts(row.get("ts"))
        if moment is None:
            continue
        start, previous = previous or moment - timedelta(hours=1), moment
        if row.get("work_done") is not True:
            continue
        cycle = CycleUse(ts=moment.isoformat(), goal=str(row.get("goal") or "")[:200],
                         llm_calls=int(row.get("llm_calls_spent") or 0))
        # Продукт — только СОЗДАННОЕ циклом: записанные файлы и названное в
        # заявке. Текст ответа (`artifact`) называет и прочитанные ИСТОЧНИКИ —
        # первый прогон на живых данных 2026-09-25 засчитал 629 «прочитано
        # позже» за повторное чтение той же книги, которую цель велела читать.
        proposal = str(row.get("proposal") or "")
        cycle.products = set(_between(traces.writes, start, moment)) | set(_PATH_RE.findall(proposal))
        # Заявки «по времени» — только если цикл сам сказал, что подал их
        # (`approvals_new=N`): заявку в то же окно кладёт и полоса демона, и
        # живой прогон приписал было её одобрение циклу «прочитай книгу».
        cycle.approvals = set(_AIN_RE.findall(proposal)) | (
            {a for a, m in created.items() if start < m <= moment} if proposal.startswith("approvals_new=") else set())
        cycle.memory_ids = set(_between(memories, start, moment))
        cycle.commits = set(_between(commits, start, moment))
        until = moment + window
        later_reads = set(_between(traces.reads, moment, until))
        later_injected = set(_between(traces.injected, moment, until))
        cycle.signals = {
            "approved": any(a in approved and approved[a] <= until for a in cycle.approvals),
            "read_later": bool(cycle.products & later_reads),
            "memory_used": bool(cycle.memory_ids & later_injected),
            "code_survived": bool(cycle.commits - reverted),
            "goal_verified": cycle.goal[:200] in {g[:200] for g in _between(verified, start, until)},
            "tests_passed": bool(cycle.products) and bool(_between(traces.green_tests, start, moment)),
        }
        if any(cycle.signals.values()):
            cycle.verdict = "useful"
        else:
            cycle.verdict = "pending" if now < until else "not_yet"
        out.append(cycle)
    return out


def market_accepted(root: Path | str) -> int:
    """Заказы площадки, принятые покупателем (core/market_ledger.py)."""
    from core.market_ledger import FILE_NAME, MarketLedger

    lines = MarketLedger(Path(root) / "data" / "market" / FILE_NAME).lines()
    return sum(1 for line in lines.values() if line.status == "accepted")


def summary(cycles: list[CycleUse], market: int | None = None) -> dict[str, Any]:
    judged = [c for c in cycles if c.verdict != "pending"]
    useful = [c for c in judged if c.verdict == "useful"]
    calls = sum(c.llm_calls for c in judged)
    return {
        "worked_cycles": len(cycles),
        "judged": len(judged),
        "useful": len(useful),
        "out_of_band": sum(1 for c in judged if any(c.signals.get(s) for s in OUT_OF_BAND)),
        "useful_share": round(len(useful) / len(judged), 3) if judged else None,
        "pending": len(cycles) - len(judged),
        "by_signal": {s: sum(1 for c in judged if c.signals.get(s)) for s in SIGNALS},
        "llm_calls_judged": calls,
        "llm_calls_on_useful_share": round(sum(c.llm_calls for c in useful) / calls, 3) if calls else None,
        "no_product_named": sum(1 for c in judged if not (c.products or c.approvals or c.memory_ids or c.commits)),
        **({"market_accepted": market} if market is not None else {}),
    }
