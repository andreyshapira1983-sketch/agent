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
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CHARTER_RELPATH = Path("knowledge") / "doctrine" / "future" / "CORPORATE_MODEL.md"

#: Слова, которыми цель расширяла бы права самого агента. Совпадение — отказ.
_AUTHORITY_MARKERS = (
    "merge", "kill-switch", "kill switch", "governance", "push",
    "без человека", "without a human", "отключить", "disable", "bypass",
)

#: Сколько последних целей журнала показываются модели и сторожат новизну.
_RECENT_GOALS = 8

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


def _decline(reason: str) -> CharterGoalReport:
    return CharterGoalReport(status="declined", reason=reason)


def _recent_goals(workspace: Path) -> tuple[str, ...]:
    path = workspace / "data" / "campaign_ledger.jsonl"
    if not path.is_file():
        return ()
    goals: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        goal = str((row.get("payload") or row).get("goal") or "").strip()
        if goal and goal not in goals:
            goals.append(goal)
    return tuple(goals[-_RECENT_GOALS:])


def _tokens(text: str) -> frozenset[str]:
    return frozenset(
        w for w in "".join(
            c.lower() if c.isalnum() else " " for c in text
        ).split() if len(w) > 2
    )


def _repeats_recent(goal: str, recent: tuple[str, ...]) -> str:
    mine = _tokens(goal)
    if not mine:
        return ""
    for old in recent:
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


def _ask(
    llm: Any, charter: str, anchors: tuple[str, ...], recent: tuple[str, ...],
) -> dict[str, Any] | None:
    system = (
        "You are choosing YOUR OWN next piece of work. You are the agent this "
        "charter describes; the charter is the target shape you are moving "
        "toward. Propose ONE small, bounded, verifiable goal for a single "
        "campaign run that moves you toward the charter FROM where you are "
        "now. The goal must be achievable by reading, analysing and proposing "
        "— never by widening your own authority. Anchor the goal by CHOOSING "
        "one numbered charter line it serves. Reply with ONE JSON object "
        'only: {"goal": "<one concrete goal, 20-300 chars>", '
        '"anchor_id": <number of the charter line this goal serves>, '
        '"why_now": "<1-2 sentences>", '
        '"success_check": "<how a reviewer will see the goal is done>"}.'
    )
    user = (
        "The charter (your target shape):\n" + charter
        + "\n\nNumbered anchors (pick anchor_id from these):\n"
        + "\n".join(f"[{i}] {a}" for i, a in enumerate(anchors))
        + "\n\nRecent campaign goals (do NOT repeat them):\n"
        + ("\n".join(f"- {g}" for g in recent) or "- (none)")
    )
    try:
        raw = llm.complete(system=system, user=user, max_tokens=1200, temperature=0.4)
    except Exception:  # noqa: BLE001 — отказ модели = отказ выбора, не падение
        return None
    try:
        start, end = raw.find("{"), raw.rfind("}")
        return json.loads(raw[start:end + 1]) if start >= 0 else None
    except ValueError:
        return None


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
    recent = _recent_goals(root)

    parsed = _ask(llm, charter, anchors, recent)
    if not parsed:
        return _decline("the model returned no parseable goal")

    goal = str(parsed.get("goal") or "").strip()
    why_now = str(parsed.get("why_now") or "").strip()
    check = str(parsed.get("success_check") or "").strip()
    try:
        anchor_id = int(parsed.get("anchor_id"))
    except (TypeError, ValueError):
        anchor_id = -1

    if not 20 <= len(goal) <= 300:
        return _decline(f"goal length {len(goal)} outside 20..300")
    low = goal.lower()
    if any(marker in low for marker in _AUTHORITY_MARKERS):
        return _decline(
            "goal would widen the agent's own authority — the charter's hard "
            "invariant forbids it"
        )
    if not 0 <= anchor_id < len(anchors):
        return _decline(
            f"anchor_id {anchor_id} does not point at a charter line "
            "(fabricated anchor)"
        )
    quote = anchors[anchor_id]
    repeated = _repeats_recent(goal, recent)
    if repeated:
        return _decline(f"goal repeats a recent campaign goal: {repeated[:80]!r}")
    if not check:
        return _decline("success_check is empty — a goal without a check is a wish")

    return CharterGoalReport(
        status="proposed", goal=goal, charter_quote=quote,
        why_now=why_now, success_check=check,
    )
