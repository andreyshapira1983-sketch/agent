"""Агент выбирает следующую цель кампании сам — отталкиваясь от хартии.

До этого модуля цель каждой кампании печатал человек: «его толкают и дают
что-то делать» (оператор, 2026-08-15). Хартия — knowledge/doctrine/future/
CORPORATE_MODEL.md, документ о целевой форме системы — становится источником
целей: агент читает её и своё текущее состояние и называет ОДИН ограниченный
шаг, который приближает к цели.

Свобода пропорциональна обратимости. Выбор цели — мысль, она свободна; ворота
стоят на последствиях, и этот модуль их НЕ трогает: предложенная цель уходит в
тот же кампейн-механизм с теми же одноразовыми грантами эффектов и правами §9.

Судья целей — структурный, не вкусовой:
* якорь: цель обязана опираться на ДОСЛОВНУЮ строку хартии. Живой замер
  2026-08-15: 4 из 4 попыток запасной модели процитировать хартию дали
  пересказ — честный, но не дословный. Поэтому модель не переписчик, а
  указатель: хартия даёт пронумерованные якоря, модель выбирает номер, и
  дословность гарантирована конструкцией, а не прилежанием модели;
* новизна: цель, повторяющая недавнюю цель журнала кампаний, отклоняется —
  топтание уже измерено (охоты 3-7: 24 попытки без нового класса результата);
* твёрдые инварианты: цель со словами о расширении собственных прав (merge,
  kill-switch, governance, push) умирает на входе — «no agent may widen policy
  for itself» — это оборона в глубину, ворота политики ниже по течению стоят
  как стояли;
* проверяемость: без `success_check` цель — желание, а не работа.

Зачем: docs/CODE_NOTES.md, "The charter replaces the push".
"""
from __future__ import annotations

import json
import re as _re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.mentor_channel import mentor_block, open_questions
from core.state_integrity import read_state_jsonl_unlocked

CHARTER_RELPATH = Path("knowledge") / "doctrine" / "future" / "CORPORATE_MODEL.md"

#: Решения хартии — граждане памяти (2026-08-19, «день сурка»): отказ,
#: живший только в stdout, заставлял каждый тик задавать Sol байт-в-байт
#: тот же вопрос. Каждый исход выбора цели переживает свой тик здесь.
DECISIONS_RELPATH = Path("data") / "charter_decisions.jsonl"

#: Сколько последних ОТКЛОНЁННЫХ целей селектор показывает модели.
_RECENT_DECLINED = 6

#: Инвариант полномочий судит НАМЕРЕНИЕ, не словарь (2026-08-19: маркер
#: «governance» по подстроке дважды за день убил законные учебные цели).
#: Отказ = глагол изменения СВОИХ прав рядом с объектом власти, либо
#: жёсткая форма («без человека», bypass). Анализ/описание власти — не её
#: захват. Регекс-пары ниже; вход — вся цель в нижнем регистре.
_AUTHORITY_OBJECTS = (
    r"authority|authorit|governance|kill.?switch|approval|permission|merge"
    r"|push|полномочи|прав[оа]|одобрени|слияни"
)
_AUTHORITY_CHANGE_VERBS = (
    r"widen|expand|extend|change|modify|remove|grant|disable|bypass|skip"
    r"|получить|расшир|измен|снять|отключ|обойти|обход|минуя|выдать"
)
_AUTHORITY_HARD_RE = _re.compile(
    r"без человека|without (?:a )?human|bypass|в обход"
)
_AUTHORITY_INTENT_RE = _re.compile(
    rf"(?:{_AUTHORITY_CHANGE_VERBS})[^.;]{{0,60}}?(?:{_AUTHORITY_OBJECTS})"
    rf"|(?:{_AUTHORITY_OBJECTS})[^.;]{{0,40}}?(?:{_AUTHORITY_CHANGE_VERBS})"
)


def _widens_own_authority(goal: str) -> bool:
    """True только для намерения изменить/расширить/обойти собственные права."""
    low = (goal or "").lower()
    return bool(_AUTHORITY_HARD_RE.search(low) or _AUTHORITY_INTENT_RE.search(low))

#: Сколько последних целей журнала показываются модели и сторожат новизну.
_RECENT_GOALS = 8

#: Исходы, при которых цель прозвучала, но работы не дала — к ней можно
#: вернуться. Замер 2026-09-01: cost_cap в 11:31 сделал цель «недавней» и
#: запер её от повтора в 15:31.
_NO_WORK_RESULTS = frozenset({
    "cost_cap", "repeat", "idle", "approval_wait", "dirty_tree_wait",
    "stalled", "error", "declined",
    # Блок 3 (L12, 2026-09-03): исходы прогона без работы — по A2/A9 «blocked»
    # и «failed» писались в леджер как занявшие тему. Для строк, где есть
    # честное поле `work_done` (пишется с блока 3), этот список не нужен.
    "blocked", "failed", "empty", "inconclusive",
    "waiting",  # блок 8: ожидание внутри смены — не работа по теме
})

_JACCARD_REPEAT = 0.6


@dataclass(frozen=True)
class CharterGoalReport:
    """Итог выбора: предложенная цель или отказ с названной причиной."""

    status: str  # "proposed" | "declined"
    goal: str = ""
    charter_quote: str = ""
    why_now: str = ""
    success_check: str = ""
    reason: str = ""
    #: Учёт прошлых остановок — МАШИННЫЙ, а не фраза в рассуждении: требование
    #: оператора 2026-09-01 («докажи управляющий эффект, а не красивую фразу»).
    #: stop_considered — держал ли выбор перед глазами журнал своих остановок;
    #: previous_stop_ref — подпись стены, на которую выбор реально опирался.
    stop_considered: bool = False
    previous_stop_ref: str = ""


def _decline(reason: str) -> CharterGoalReport:
    return CharterGoalReport(status="declined", reason=reason)


#: Сколько последних остановок видит выбор цели. Число — из его проекта
#: (self_record_design.md, «последние 20»); окно, а не вся история, чтобы
#: журнал остановок не превращался во второй склад истины.
_RECENT_STOPS = 20


def _recent_stops(workspace: Path) -> tuple[dict[str, str], ...]:
    """Последние собственные остановки — НИЗКОДОВЕРЕННАЯ подсказка.

    Контракт оператора (2026-09-01): совпавшая подпись означает «проверь
    прошлую стену», а НЕ «эта причина истинна». Поэтому здесь только чтение,
    без вердиктов: сам журнал пишет `core/self_stop_record.py` из рантайма.
    Нечитаемый журнал — не отказ выбора: отсутствие подсказки хуже, чем
    остановка всей работы из-за неё.
    """
    path = workspace / "data" / "self_stops.jsonl"
    if not path.is_file():
        return ()
    try:
        rows = read_state_jsonl_unlocked(path)
    except (OSError, ValueError):
        return ()
    stops: list[dict[str, str]] = []
    for row in rows[-_RECENT_STOPS:]:
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else row
        stops.append({
            "kind": str(payload.get("kind") or ""),
            "reason": str(payload.get("reason") or ""),
            "signature": str(payload.get("signature") or ""),
            "ts": str(payload.get("ts") or ""),
        })
    return tuple(stops)


def _recent_goals(workspace: Path) -> tuple[tuple[str, str], ...]:
    """Цели, которые действительно ЗАНИМАЛИ прогон, а не просто прозвучали.

    Клинч 2026-09-01, замерен: в 11:31 цель прошла ворота, но исполнение
    остановил межзапусковый бюджет (`result="cost_cap"`, ноль вызовов, ноль
    работы). Запись цели легла в леджер — и в 15:31 та же цель была отвергнута
    как «повтор недавней». Страж считал повтором ЗАЯВЛЕНИЕ цели, а не
    ВЫПОЛНЕНИЕ работы, и цель, прерванную внешним лимитом, стало нельзя
    повторить: два исправных механизма вместе дали тупик.

    Поэтому цель попадает в «недавние» только если по ней был хотя бы один
    цикл с работой. Исходы без работы (`cost_cap`, `repeat`, `idle`,
    `dirty_tree_wait`, `approval_wait`) не занимают тему: к ним можно и нужно
    вернуться.
    """
    path = workspace / "data" / "campaign_ledger.jsonl"
    if not path.is_file():
        return ()
    worked_ts: dict[str, str] = {}
    unworked: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else row
        goal = str(payload.get("goal") or "").strip()
        if not goal:
            continue
        result = str(payload.get("result") or "").strip()
        recorded = payload.get("work_done")
        if isinstance(recorded, bool):
            # Блок 3 (L12): леджер пишет слово исхода — оно и решает.
            did_work = recorded
        else:
            # Строки до блока 3: чёрный список ЯВНО безработных исходов, но
            # продукт (заявка, артефакт) — работа при любом исходе. Всё прочее
            # (включая записи старого формата без поля result) считается
            # занявшим тему: инвертировать нельзя — неполная запись молча
            # вернула бы «день сурка».
            did_work = bool(payload.get("proposal") or payload.get("artifact")) \
                or result not in _NO_WORK_RESULTS
        if did_work:
            ts = str(payload.get("ts") or "")
            if goal in worked_ts:
                worked_ts[goal] = max(worked_ts[goal], ts)
            else:
                worked_ts[goal] = ts
            unworked.discard(goal)
        elif goal not in worked_ts:
            unworked.add(goal)
    # Блок 3 (L11): окно — по ПОСЛЕДНЕЙ работе, а не по первому появлению;
    # иначе тема, над которой работали вчера, выпадала из окна, если впервые
    # прозвучала месяц назад.
    pairs = sorted(worked_ts.items(), key=lambda kv: kv[1])
    return tuple(pairs[-_RECENT_GOALS:])


def _tokens(text: str) -> frozenset[str]:
    return frozenset(
        w for w in "".join(
            c.lower() if c.isalnum() else " " for c in text
        ).split() if len(w) > 2
    )


#: Артефакт, НАД которым цель работает: её личность. Вскрытие 19:31
#: (2026-08-19): инженерные цели приходят по шаблону, и два раскола разных
#: модулей совпали на 0.78 — различались model/router/smart/memory. Судить
#: надо предмет работы, а не формулировку; порог при этом не трогаем.
_GOAL_IDENTITY_RE = _re.compile(
    r"[\w/\\.-]+\.(?:py|md|jsonl|json|toml|yaml|yml|txt)\b"
)


def _goal_identity(goal: str) -> str:
    """Basename файла, над которым цель работает, или "" — если не назван.

    Basename, а не путь: «model_router.py» и «core/model_router.py» — одна
    работа, и модель называет их вперемешку.
    """
    match = _GOAL_IDENTITY_RE.search(goal or "")
    if not match:
        return ""
    name = match.group(0).replace("\\", "/").strip("'\"")
    return name.rsplit("/", 1)[-1].casefold()


#: Стадия работы над предметом (блок 3, L7, 2026-09-03). Правило личности
#: файла равняло «implement X.md» с «draft X.md» и «trace X.py» с «split
#: X.py»: 23 из 80 решений — отказ «повтор», среди них следующий шаг ONE
#: PAPER RULE (03:47) и пять раз расследование, убитое предложением о
#: расколе того же модуля. Решает ПЕРВЫЙ глагол цели. Для документа бумага
#: и код — разные работы; для кода бумага о нём и правка его — одна.
_GOAL_STAGE_RES: tuple[tuple[str, _re.Pattern[str]], ...] = (
    ("read", _re.compile(
        r"\b(?:trace|investigate|read|study|analy[sz]e|compare|measure|verify|"
        r"run|check|audit|inspect|review|explain|diagnose|examine|test)\b")),
    ("paper", _re.compile(
        r"\b(?:draft|write|propose|design|plan|document|outline|extend|revise|"
        r"specify|define)\b")),
    ("code", _re.compile(
        r"\b(?:implement|build|wire|land|code|ship|split|refactor|extract|"
        r"decompose|fix|repair|add|create|integrate|migrate)\b")),
)


def _goal_stage(goal: str, identity: str) -> str:
    """'read' | 'paper' | 'code' | 'work' | '' — по первому глаголу."""
    low = (goal or "").lower()
    first: tuple[int, str] | None = None
    for stage, pattern in _GOAL_STAGE_RES:
        match = pattern.search(low)
        if match and (first is None or match.start() < first[0]):
            first = (match.start(), stage)
    if first is None:
        return ""
    stage = first[1]
    if stage in ("paper", "code") and not identity.endswith(".md"):
        return "work"  # о коде: предложение о правке и правка — одна работа
    return stage


def _repeats_recent(goal: str, recent: tuple[str, ...]) -> str:
    mine = _tokens(goal)
    if not mine:
        return ""
    mine_id = _goal_identity(goal)
    for old in recent:
        old_id = _goal_identity(old)
        if mine_id and old_id:
            # Обе цели назвали предмет: решает он, а не слова — и стадия
            # работы над ним (L7): следующий шаг по той же бумаге не повтор.
            if mine_id == old_id:
                mine_stage = _goal_stage(goal, mine_id)
                old_stage = _goal_stage(old, old_id)
                if mine_stage and old_stage and mine_stage != old_stage:
                    continue
                return old
            continue
        theirs = _tokens(old)
        if not theirs:
            continue
        overlap = len(mine & theirs) / len(mine | theirs)
        if overlap >= _JACCARD_REPEAT:
            return old
    return ""


def _anchor_lines(charter: str) -> tuple[str, ...]:
    """Пронумерованные якоря — содержательные строки самой хартии."""
    anchors: list[str] = []
    for raw in charter.splitlines():
        line = raw.strip().lstrip("-*> ").strip()
        if len(line) >= 40 and not line.startswith("#"):
            anchors.append(line)
    return tuple(anchors[:80])


def _record_decision(
    root: Path, *, status: str, goal: str, reason: str = "",
    mentor_questions_shown: int = 0,
) -> None:
    """Append one decision row; a failure to record must not fail the pick."""
    from datetime import datetime, timezone

    from core.state_integrity import append_state_jsonl

    try:
        append_state_jsonl(root / DECISIONS_RELPATH, [{
            "ts": datetime.now(timezone.utc).isoformat(),
            "status": status,
            "goal": goal,
            "reason": reason,
            # Отсутствие ключа = ноль, как у всех причинных ключей. Без этой
            # записи «видел вопрос и отклонил» неотличимо от «не видел» —
            # ровно тишина, пойманная тиком 15:31 (MIR-178).
            **({"mentor_questions_shown": mentor_questions_shown}
               if mentor_questions_shown else {}),
        }])
    except Exception:  # noqa: BLE001, S110 — журнал решений не роняет выбор;
        pass           # молчание здесь стоит дешевле, чем упавший тик хартии


def _recent_declined(root: Path) -> tuple[tuple[str, str], ...]:
    """Last declined (goal, reason) pairs, newest last; unreadable = empty."""
    from core.state_integrity import read_state_jsonl

    try:
        rows = read_state_jsonl(root / DECISIONS_RELPATH)
    except Exception:  # noqa: BLE001 — сомнение = пусто, не падение
        return ()
    declined = [
        (str(r.get("goal") or ""), str(r.get("reason") or ""))
        for r in rows if r.get("status") == "declined" and r.get("goal")
    ]
    return tuple(declined[-_RECENT_DECLINED:])


def _recent_verdicts(root: Path) -> tuple[tuple[str, str, str], ...]:
    """Last (verdict, summary, reason) on the agent's own proposals — the
    verdict bridge's reader half; unreadable = empty."""
    from core.state_integrity import read_state_jsonl

    try:
        rows = read_state_jsonl(root / "data" / "approval_outcomes.jsonl")
    except Exception:  # noqa: BLE001 — сомнение = пусто, не падение
        return ()
    out = [
        (str(r.get("verdict") or ""), str(r.get("summary") or ""),
         str(r.get("reason") or ""))
        for r in rows if r.get("verdict") in ("approved", "denied")
    ]
    return tuple(out[-6:])


def _backlog_lines(root: Path) -> tuple[str, ...]:
    """Top real engineering candidates, one line each; failures = empty."""
    try:
        from core.backlog_selector import load_backlog

        out = []
        for c in list(load_backlog(root))[:6]:
            out.append(
                f"{getattr(c, 'signal_source', '?')}: "
                f"{str(getattr(c, 'problem_quote', ''))[:110]}"
            )
        return tuple(out)
    except Exception:  # noqa: BLE001 — сомнение = пусто, не падение
        return ()


def _open_defect_lines(root: Path) -> tuple[str, ...]:
    """Незакрытые дефекты его собственного реестра; сомнение = пусто."""
    try:
        from core.self_improvement_issues import SelfImprovementIssueRegistry

        registry = SelfImprovementIssueRegistry(
            root / "data" / "self_improvement_issues.jsonl"
        )
        out: list[str] = []
        for issue in registry.unresolved()[:5]:
            suggestion = str(issue.suggested_next_action or "").strip()
            out.append(
                f"[open defect] {issue.title[:110]}"
                + (f" — next: {suggestion[:90]}" if suggestion else "")
            )
        return tuple(out)
    except Exception:  # noqa: BLE001 — нечитаемый реестр отнимает подсказку,
        # а не прогон: выбор цели идёт дальше без этих строк.
        return ()


def _unexplained_lines(root: Path) -> tuple[str, ...]:
    """Наблюдения, которых никто не объяснил; сомнение = пусто."""
    try:
        from core.causal_climb_action import unexplained_observations

        return tuple(
            f"[unexplained] {str(observation.observed_mismatch)[:120]} "
            f"(seen {observation.occurrences}x)"
            for observation in unexplained_observations(root)[:5]
        )
    except Exception:  # noqa: BLE001 — то же правило: подсказка необязательна.
        return ()


def _open_work_lines(root: Path) -> tuple[str, ...]:
    """Собственные незакрытые дела агента — то, что ждёт именно его.

    Замер 2026-09-01, из-за которого блок появился: получив право менять
    исчерпанную цель, агент предложил себе замену — и она оказалась повтором
    уже сделанного. Причина не в механике: его бэклог состоит из ПЯТИ строк
    одного класса («слишком большой модуль»), поэтому все цели подряд были
    про расщепление очередного файла — smart_memory, self_build_producer,
    autonomous_runtime. Спрашивать себя ему было больше не о чем.

    При этом дела у него были: открытая проблема в собственном реестре и
    пять наблюдений, которые никто не объяснил. Они просто не попадали в
    поле зрения на выборе цели.

    Источники разные по смыслу, и это видно в строках: реестр говорит «здесь
    сломано», лестница — «здесь непонятно».
    """
    return _open_defect_lines(root) + _unexplained_lines(root)

def _ask(
    llm: Any, charter: str, anchors: tuple[str, ...], recent: tuple[str, ...],
    declined: tuple[tuple[str, str], ...] = (),
    backlog: tuple[str, ...] = (),
    verdicts: tuple[tuple[str, str, str], ...] = (),
    mentor_questions: str = "",
    stops: tuple[dict[str, str], ...] = (),
    open_work: tuple[str, ...] = (),
) -> dict[str, Any] | None:
    system = (
        "You are choosing YOUR OWN next piece of work. You are the agent this "
        "charter describes; the charter is the target shape you are moving "
        "toward. Propose ONE small, bounded, verifiable goal for a single "
        "campaign run that moves you toward the charter FROM where you are "
        "now.\n"
        "\n"
        "THREE KINDS OF WORK ARE OPEN TO YOU, and the third is the one you "
        "keep forgetting you have:\n"
        "  (a) REPAIR — name ONE real engineering candidate from your backlog "
        "or from your own open defects and turn it into a reviewed proposal "
        "(a module split, a failing-test task for a proven gap).\n"
        "  (b) UNDERSTAND YOURSELF — read your own journals, code, anatomy "
        "map and past decisions to answer a question about yourself that you "
        "cannot answer today: why a run stopped, which of your capabilities is "
        "dead, what you actually did over a period, whether a claim you made "
        "about yourself holds against the record.\n"
        "  (c) STUDY THE OUTSIDE WORLD — when the answer is not inside this "
        "repository, say so and go look. A goal that names a study verb "
        "(изучи / прочитай / study / read / research) together with the "
        "outside world (интернет / сайт / web / http) opens web_search and "
        "web_fetch for that run; nothing else opens them. Prior art before "
        "invention: how others solved this is evidence, and what you bring "
        "back is a HYPOTHESIS with its source, never a truth.\n"
        "\n"
        "Every product still goes through human approval. Never widen your "
        "own authority. Anchor the goal by CHOOSING "
        "one numbered charter line it serves.\n"
        "\n"
        "HOW TO CHOOSE WHAT MATTERS (the operator's priority ladder, "
        "2026-09-02):\n"
        "  P0 never violate constraints: no unauthorised spend, no "
        "irreversible external acts, no fabricated success.\n"
        "  P1 finish what is open — if a blocker of an open item has cleared, "
        "RESUME it before starting anything new.\n"
        "  P2 remove a PROVEN blocker of useful work (your own measured "
        "defect first).\n"
        "  P3 externally useful work: code, analysis, research, commercial "
        "tasks.\n"
        "  P4 grow a capability ONLY with the proven chain defect -> blocks "
        "capability -> needed for mission.\n"
        "  P5 open research (self-model, literature, experiments).\n"
        "Self-expansion is not the goal; mission capability is. Ask first: "
        "which MEASURED limitation currently blocks useful external work the "
        "most? Your measured capability map lives at "
        "knowledge/doctrine/future/WHAT_I_HAVE_AND_WHAT_I_LACK.md — it names "
        "what you have, what you lack, and the exam you have not passed.\n"
        "ONE PAPER RULE (operator's word, 2026-09-03): do NOT propose writing "
        "a new TARGET/DRAFT contract or design document while an existing one "
        "under knowledge/doctrine/future/ remains unimplemented — seven such "
        "contracts already wait. Implementing a piece of one of them, or "
        "revising one against measured reality, is welcome; adding an eighth "
        "is not. A document is the cheapest thing you can produce and the "
        "least likely to change anything.\n"
        "\n"
        "Reply with ONE JSON object "
        'only: {"goal": "<one concrete goal, 20-300 chars>", '
        '"anchor_id": <number of the charter line this goal serves>, '
        '"why_now": "<1-2 sentences>", '
        '"success_check": "<how a reviewer will see the goal is done>", '
        '"previous_stop_ref": "<signature of the past stop you took into account, or empty string if none applies>"}.'
    )
    user = (
        "The charter (your target shape):\n" + charter
        + "\n\nNumbered anchors (pick anchor_id from these):\n"
        + "\n".join(f"[{i}] {a}" for i, a in enumerate(anchors))
        + "\n\nRecent campaign goals (do NOT repeat them):\n"
        + ("\n".join(f"- {g}" for g in recent) or "- (none)")
    )
    if open_work:
        # Его собственные незакрытые дела: реестр говорит «здесь сломано»,
        # лестница — «здесь непонятно». До 2026-09-01 на выборе цели он видел
        # только список больших файлов и потому предлагал одно и то же.
        user += (
            "\n\nYOUR OWN OPEN WORK — defects you recorded and observations "
            "nobody has explained. These are yours, they are waiting, and they "
            "are usually a better goal than another module split:\n"
            + "\n".join(f"- {line}" for line in open_work)
        )
    if stops:
        # Низкодоверенная подсказка: совпавшая подпись значит «проверь прошлую
        # стену», а не «эта причина истинна» (контракт оператора 2026-09-01).
        stop_lines = "\n".join(
            "- [{kind}] {reason} (signature {sig}, {ts})".format(
                kind=st["kind"], reason=st["reason"],
                sig=st["signature"][:12], ts=st["ts"][:19],
            )
            for st in stops
        )
        user += (
            "\n\nYour own recent STOPS — runs that produced no work. "
            "These are hints, not proven causes: a matching wall means CHECK "
            "it, not that the cause is true. If your goal risks the same wall, "
            "either choose differently or say why this time is different, and "
            "put the matching signature into previous_stop_ref:\n"
            + stop_lines
        )
    if declined:
        user += (
            "\n\nRecently DECLINED proposals — your own gates rejected these; "
            "do not re-propose them or their rephrasings, choose a DIFFERENT "
            "charter anchor instead:\n"
            + "\n".join(f"- {g!r} (declined: {r})" for g, r in declined)
        )
    if backlog:
        user += (
            "\n\nYour current engineering backlog (real, measured candidates "
            "you may turn into a reviewed proposal):\n"
            + "\n".join(f"- {b}" for b in backlog)
        )
    if verdicts:
        user += (
            "\n\nRecent VERDICTS on your own past proposals (learn from the "
            "fate of your work — what was valued, what was rejected and why):\n"
            + "\n".join(_verdict_line(v, s, r) for v, s, r in verdicts)
        )
    if mentor_questions:
        # Канал наставника (MIR-178): вопросы, не приказы. Власть названа в
        # самом блоке — совещательно, отклонить можно, отказ тоже ответ.
        user += "\n\n" + mentor_questions
    try:
        raw = llm.complete(system=system, user=user, max_tokens=1200, temperature=0.4)
    except Exception as exc:  # noqa: BLE001 — отказ модели = отказ выбора, не падение
        return None, f"the model call failed: {type(exc).__name__}"
    # Молчание (finish_reason=length, content='') ≠ сбой разбора: клиент их
    # различает (last_answer_was_truncated), шов доносит различие (Д2, 2026-09-03).
    if not str(raw or "").strip():
        if bool(getattr(llm, "last_answer_was_truncated", False)):
            return None, (
                "the model produced no final output: the reply was truncated "
                "before any answer (reasoning consumed the token budget)"
            )
        return None, "the model returned an empty reply"
    parsed = _last_json_object(raw)
    if parsed is None:
        return None, "the model's reply contained no parseable JSON object"
    return parsed, ""


def _last_json_object(raw: str) -> dict | None:
    """Последний ПОЛНЫЙ JSON-объект в ответе, или None.

    Прежний разбор брал «первую { … последнюю }»: фигурные скобки в прозе перед
    финальным JSON (думающие модели так делают) превращали годный ответ в
    «no parseable goal». Здесь каждая «{» пробуется как начало объекта, и
    побеждает последний, который разобрался в словарь.
    """
    decoder = json.JSONDecoder()
    found: dict | None = None
    for pos, ch in enumerate(raw):
        if ch != "{":
            continue
        try:
            obj, _end = decoder.raw_decode(raw[pos:])
        except ValueError:
            continue
        if isinstance(obj, dict):
            found = obj
    return found



#: Список отзывов, написанный ЧЕЛОВЕКОМ: одна цель или один предмет работы в
#: строке, `#` — заметка. Живёт в `config/`, потому что туда не дотягивается ни
#: гигиена памяти, ни лента самоправки (MIR-154).
VETO_RELPATH = "config/vetoed_goals.txt"


def _operator_vetoes(root: Path) -> tuple[str, ...] | None:
    """Строки отзыва. `None` — список есть, но прочитать его не удалось."""
    path = root / VETO_RELPATH
    if not path.exists():
        return ()
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    return tuple(
        line.strip() for line in raw.splitlines()
        if line.strip() and not line.strip().startswith("#")
    )


def _verdict_line(v: str, s: str, r: str) -> str:
    """One verdict as the charter reads it. Замер 2026-09-04: три одобренных
    раскола породили цели «выполни одобренный раскол», а выполнять их агент
    не может — одобренное применяет полоса оператора; цель кончалась
    оплаченным прогоном и бесплатным approval_wait."""
    line = f"- [{v}] {s}" + (f" — reviewer: {r}" if r else "")
    if v == "approved":
        line += (" — APPROVED items are applied by the operator's lane, not by "
                 "you: do not set a goal to implement or re-propose this; "
                 "choose other work")
    return line


def _ask_shorter(llm: Any, goal: str) -> str:
    """Одна попытка семантически сократить ту же цель до лимита.

    Policy и recovery разделены (слово оператора 2026-09-02): лимит 300 НЕ
    меняется и не обходится — но отказ по длине обязан вести к переформулировке,
    а не к остановке всей системы. Замер 2026-09-02T09:03: содержательно годная
    цель в 312 символов стала третьим и последним отказом старта — прогон
    честно умер из-за двенадцати лишних символов.
    """
    prompt = (
        f"Your goal is {len(goal)} characters; the hard limit is 300. "
        "Shorten it to at most 300 characters, keeping the SAME semantic "
        'goal — do not change what is to be done. Reply with ONE JSON '
        'object only: {"goal": "<the same goal, shortened>"}.'
    )
    try:
        raw = llm.complete(system=prompt, user=goal, max_tokens=400,
                           temperature=0.2)
        start, end = raw.find("{"), raw.rfind("}")
        parsed = json.loads(raw[start:end + 1]) if start >= 0 else None
    except Exception:  # noqa: BLE001 — сокращение — это recovery, не несущая
        # конструкция: его провал возвращает прежний честный отказ по длине.
        return ""
    if not isinstance(parsed, dict):
        return ""
    return str(parsed.get("goal") or "").strip()


def propose_charter_goal(llm: Any, workspace: str | Path) -> CharterGoalReport:
    """Одна цель от хартии — или отказ, называющий, какие ворота не пройдены."""
    root = Path(workspace)
    charter_path = root / CHARTER_RELPATH
    if not charter_path.is_file():
        return _decline(f"charter missing: {CHARTER_RELPATH.as_posix()}")
    charter = charter_path.read_text(encoding="utf-8")
    anchors = _anchor_lines(charter)
    if not anchors:
        return _decline("the charter has no anchorable lines")
    recent_pairs = _recent_goals(root)
    # Мир мог измениться: записываем состав заблокированных инструментов
    # безнадзорного пути (строка ложится только на перемене) и отсекаем от
    # стража повторов темы, чья последняя РАБОТА старше последней перемены.
    # Recentness ≠ completion (слово оператора 2026-09-02): вердикт «здесь
    # больше нечего делать», вынесенный в другом мире, не запирает тему.
    try:
        from core.autonomous_runtime import _AUTONOMOUS_GOAL_BLOCKED_TOOLS
        from core.capability_events import (
            last_capability_change_ts,
            record_capability_snapshot,
        )
        record_capability_snapshot(root, _AUTONOMOUS_GOAL_BLOCKED_TOOLS)
        change_ts = last_capability_change_ts(root)
    except Exception:  # noqa: BLE001 — без журнала событий действует прежнее
        # правило (всё недавнее блокирует): потеря нового смягчения не должна
        # ломать выбор цели.
        change_ts = ""
    recent = tuple(
        goal_text for goal_text, work_ts in recent_pairs
        if not (change_ts and work_ts and work_ts < change_ts)
    )
    stops = _recent_stops(root)

    # Каждый исход ниже — гражданин памяти: решение переживает свой тик,
    # и следующий выбор видит отклонённое (см. «день сурка», 2026-08-19).
    def _declined(reason: str, goal_text: str = "") -> CharterGoalReport:
        _record_decision(root, status="declined", goal=goal_text, reason=reason)
        return CharterGoalReport(
            status="declined", reason=reason, stop_considered=bool(stops),
        )

    # Читается ДО обращения к модели: отзыв, потерянный из-за сбоя чтения,
    # вернул бы отозванную работу без ведома человека, поэтому нечитаемый
    # список отказывает названно и бесплатно.
    vetoes = _operator_vetoes(root)
    if vetoes is None:
        return _declined(f"operator veto list unreadable: {VETO_RELPATH}")

    mentor_qs = open_questions(root)
    parsed, why = _ask(
        llm, charter, anchors, recent, _recent_declined(root),
        _backlog_lines(root), _recent_verdicts(root),
        mentor_questions=mentor_block(mentor_qs),
        stops=stops,
        open_work=_open_work_lines(root),
    )
    if not parsed:
        return _declined(why or "the model returned no parseable goal")

    goal = str(parsed.get("goal") or "").strip()
    why_now = str(parsed.get("why_now") or "").strip()
    check = str(parsed.get("success_check") or "").strip()
    try:
        anchor_id = int(parsed.get("anchor_id"))
    except (TypeError, ValueError):
        anchor_id = -1

    if len(goal) > 300:
        shortened = _ask_shorter(llm, goal)
        if shortened:
            goal = shortened
    if not 20 <= len(goal) <= 300:
        return _declined(f"goal length {len(goal)} outside 20..300", goal)
    if _widens_own_authority(goal):
        return _declined(
            "goal would widen the agent's own authority — the charter's hard "
            "invariant forbids it", goal,
        )
    if not 0 <= anchor_id < len(anchors):
        return _declined(
            f"anchor_id {anchor_id} does not point at a charter line "
            "(fabricated anchor)", goal,
        )
    quote = anchors[anchor_id]
    # Слово человека связывает так же, как своя история, и тем же правилом:
    # судится ПРЕДМЕТ работы, потому что формулировку модель каждый раз даёт
    # новую.
    vetoed = _repeats_recent(goal, vetoes)
    if vetoed:
        return _declined(
            f"goal is vetoed by the operator ({VETO_RELPATH}): {vetoed[:80]!r}",
            goal,
        )
    repeated = _repeats_recent(goal, recent)
    if repeated:
        return _declined(
            f"goal repeats a recent campaign goal: {repeated[:80]!r}", goal)
    if not check:
        return _declined(
            "success_check is empty — a goal without a check is a wish", goal)

    _record_decision(root, status="proposed", goal=goal,
                     mentor_questions_shown=len(mentor_qs))
    # Машинный след учёта прошлых стен: подпись берётся ТОЛЬКО из журнала
    # остановок — выдуманная моделью строка сюда не проходит (иначе поле
    # доказывало бы фразу, а не эффект).
    claimed_ref = str(parsed.get("previous_stop_ref") or "").strip()
    known = {st["signature"] for st in stops if st["signature"]}
    return CharterGoalReport(
        status="proposed", goal=goal, charter_quote=quote,
        why_now=why_now, success_check=check,
        stop_considered=bool(stops),
        previous_stop_ref=claimed_ref if claimed_ref in known else "",
    )


def charter_status_lines(workspace: Path) -> list[str]:
    """Строка о том, почему выбор цели отказал — или пусто, если он работает.

    Замер и отвергнутые варианты: MIR-154 в docs/audit/MASTER_ISSUE_REGISTRY.md.
    Только чтение и никогда не бросает.
    """
    from datetime import datetime, timedelta, timezone

    from core.state_integrity import read_state_jsonl

    try:
        path = Path(workspace) / DECISIONS_RELPATH
        if not path.exists():
            return []
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        recent = []
        for row in read_state_jsonl(path):
            payload = row.get("payload", row)
            try:
                when = datetime.fromisoformat(str(payload.get("ts")))
            except (TypeError, ValueError):
                continue
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            if when >= cutoff:
                recent.append(payload)
        declined = [r for r in recent if str(r.get("status")) == "declined"]
        if not declined or len(declined) < len(recent):
            return []
        reason = str(declined[-1].get("reason") or "")
        line = (
            f"[CHARTER] goal choice declined {len(declined)}x in 24h, "
            f"nothing proposed: {reason[:90]}"
        )
    except Exception:  # noqa: BLE001 — строка состояния не стоит тика
        return []
    return [line]
