"""Задача от драйва: что сейчас важно — решает внутреннее состояние, что делать — модель.

Шаг 2 эндогенной активации (оператор, 2026-09-19). Шаг 1 (`core/drives.py`)
меряет драйвы из журналов. Здесь из них рождается ОДНА задача:

* `idle_time` — будильник, не содержание: он говорит «пора», но не «что»;
* содержание берётся у самого сильного из остальных драйвов, умноженного на
  вес ПРИВЫКАНИЯ. Первый живой замер: сильнее всех были `uncertainty` (его
  необъяснённые детекторы) и `unfinished_obligations` (правки, навсегда
  заблокированные уроками отката) — драйвы, которые действием не гасятся.
  «Бери самый сильный» вернул бы его в ту же петлю. Поэтому: если после
  задачи драйв не упал, его вес уменьшается вдвое; со временем вес
  восстанавливается. Так уступают место драйвы, которые работа удовлетворяет;
* задачу предлагает модель — по потребности, описи того, что у агента есть,
  и его последним задачам с исходами; ОДНУ, короткую, проверяемую. Перед
  выдачей её проверяют детектор неоднозначности контракта и страж повтора.

    python -m core.drive_goal            # выбрать драйв и предложить задачу
    python -m core.drive_goal --dry      # только выбор драйва, без модели
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.drives import DOMAINS, _rows, _similarity, compute_drives

STATE_RELPATH = Path("data") / "drive_state.json"
DECISIONS_RELPATH = Path("data") / "drive_decisions.jsonl"
WAKE_THRESHOLD = 0.5
MIN_CONTENT = 0.2
_NOT_CONTENT = frozenset({"idle_time", "economic_opportunity"})
_REPEAT_SIMILARITY = 0.6


@dataclass
class DriveGoal:
    """Отчёт выбора: кампания принимает объект с `goal` и `success_check`."""

    status: str  # "proposed" | "declined"
    goal: str = ""
    success_check: str = ""
    drive: str = ""
    reason: str = ""


def _load_state(root: Path) -> dict[str, Any]:
    try:
        state = json.loads((root / STATE_RELPATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    state.setdefault("weights", {})
    return state


def _save_state(root: Path, state: dict[str, Any]) -> None:
    path = root / STATE_RELPATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def choose_drive(drives: dict[str, dict[str, Any]], state: dict[str, Any],
                 now: datetime) -> tuple[str | None, dict[str, Any]]:
    """Драйв для содержания задачи с учётом привыкания; state обновляется на месте."""
    weights: dict[str, float] = state["weights"]
    last = state.get("last")
    if last and last.get("drive") in drives:
        dropped = float(last.get("value", 0)) - drives[last["drive"]]["value"]
        w = weights.get(last["drive"], 1.0)
        weights[last["drive"]] = 1.0 if dropped >= 0.1 else max(0.1, w * 0.5)
        state["last"] = None
    try:
        hours = (now - datetime.fromisoformat(state.get("updated") or now.isoformat())).total_seconds() / 3600
    except ValueError:
        hours = 0.0
    for name in list(weights):
        weights[name] = min(1.0, weights[name] + 0.1 * max(0.0, hours))
    state["updated"] = now.isoformat()
    scored = {n: info["value"] * weights.get(n, 1.0) for n, info in drives.items()
              if n not in _NOT_CONTENT and info["value"] >= MIN_CONTENT}
    if not scored:
        return None, state
    return max(scored, key=scored.get), state


def _library_sample(root: Path, drive: str, limit: int = 25) -> list[str]:
    domain = drive.removeprefix("competence_")
    lines: list[str] = []
    for mark in DOMAINS.get(domain, ()):
        base = root / mark
        if base.is_dir():
            lines += sorted(str(p.relative_to(root)).replace("\\", "/")
                            for p in base.rglob("*.txt"))[:limit]
    return lines


def _recent_tasks(root: Path, limit: int = 10) -> list[str]:
    """Последние цели с лучшим исходом по каждой."""
    best: dict[str, str] = {}
    order: list[str] = []
    rank = {"completed": 3, "failed": 1, "empty": 1}
    for r in _rows(root / "data" / "campaign_ledger.jsonl"):
        g = str(r.get("goal") or "")
        if not g:
            continue
        if g not in best:
            order.append(g)
            best[g] = ""
        res = str(r.get("result") or "")
        if rank.get(res, 0) > rank.get(best[g], 0):
            best[g] = res
    return [f"- [{best[g] or 'ничего не сделано'}] {g[:220]}" for g in order[-limit:]]


_DOMAIN_RU = {"math": "математике", "physics": "физике", "cs": "программированию и информатике"}


def need_text(drive: str, info: dict[str, Any], root: Path) -> str:
    """Потребность человеческими словами и её конкретный материал — без имён переменных.

    Первый живой проход шага 2: модели показали «uncertainty = 0.70», и она
    предложила «найди в логах, когда драйв uncertainty снижался» — снова задачу
    про собственный механизм. Имя переменной — это провод, а не потребность.
    """
    if drive.startswith("competence_") and drive != "competence_world":
        area = _DOMAIN_RU.get(drive.removeprefix("competence_"), drive)
        return (f"Ты давно не решал задач по {area} ({info['why'].split(': ', 1)[-1]}). "
                f"Хочется разобраться в чём-то новом из своих книг по {area} и проверить это расчётом или цитатой.")
    if drive == "competence_world":
        return ("Ты давно ничего не узнавал из интернета. Хочется найти в сети то, чего нет в твоих книгах, "
                "по первоисточнику, и проверить.")
    if drive == "uncertainty":
        try:
            from core.causal_climb_action import unexplained_observations

            items = [str(o.observed_mismatch)[:200] for o in unexplained_observations(root)][:5]
        except Exception:  # noqa: BLE001 — без лестницы — без списка
            items = []
        return ("Есть вещи, которые ты наблюдал и не можешь объяснить:\n" + "\n".join(f"- {i}" for i in items)
                + "\nХочется разобраться в ОДНОЙ из них до проверяемого вывода или честного «данных нет».")
    if drive == "unfinished_obligations":
        items = [str(r.get("summary") or r.get("operation"))[:200]
                 for r in _rows(root / "data" / "approval_inbox.jsonl") if r.get("status") == "pending"][:5]
        return ("Незавершённые дела ждут:\n" + "\n".join(f"- {i}" for i in items)
                + "\nХочется закрыть или честно разобрать одно из них.")
    if drive == "maintenance_need":
        broken = [f"{r.get('action')}: {str(r.get('reason') or '')[:150]}"
                  for r in _rows(root / "data" / "campaign_ledger.jsonl")[-20:] if r.get("result") in ("failed", "empty")]
        return ("Что-то у тебя ломается:\n" + "\n".join(f"- {b}" for b in broken[-5:])
                + "\nХочется найти причину одной поломки.")
    if drive == "novelty_need":
        return ("Последние занятия однообразны. Хочется заняться чем-то совсем другим, чем последние задачи.")
    return info.get("why", drive)


def _ask(llm: Any, drive: str, info: dict[str, Any], root: Path, feedback: str = "") -> dict[str, Any] | None:
    from core.charter_goal import _unattended_tools, _workspace_inventory

    system = (
        "Ты — автономный агент. Сейчас у тебя есть потребность — ниже она описана. Предложи "
        "себе ОДНУ короткую задачу, которая её удовлетворит, — задачу о самом предмете, а не "
        "о твоём устройстве: конкретную, выполнимую за один заход и "
        "проверяемую (точный файл или источник, что найти или посчитать, как проверить — "
        "цитата со страницей, расчёт в python_probe, первоисточник в интернете). Не повторяй "
        "недавние задачи. Задачу формулируй по-русски, как просьбу найти, прочитать, "
        "посчитать, проверить, объяснить. Верни только JSON: "
        '{"goal": "<текст задачи>", "success_check": "<как понять, что выполнено>"}'
    )
    parts = ["Что тебе сейчас нужно:\n" + need_text(drive, info, root)]
    sample = _library_sample(root, drive)
    if sample:
        parts.append("Книги этой области у тебя:\n" + "\n".join(sample))
    parts.append("Что лежит в рабочей папке:\n" + "\n".join(_workspace_inventory(root)))
    parts.append("Инструменты: " + ", ".join(_unattended_tools(root)))
    recent = _recent_tasks(root)
    if recent:
        parts.append("Твои последние задачи и их исход:\n" + "\n".join(recent))
    if feedback:
        parts.append("Прошлое предложение отклонено: " + feedback)
    raw = llm.complete(system=system, user="\n\n".join(parts), max_tokens=700, temperature=0.4)
    m = re.search(r"\{.*\}", str(raw or ""), re.DOTALL)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return None
    return data if isinstance(data, dict) and str(data.get("goal") or "").strip() else None


def _problem(goal: str, root: Path) -> str:
    """Почему задачу нельзя выдать, или пусто."""
    from core.completion_contract import derive_completion_contract

    contract = derive_completion_contract(goal)
    if contract.ambiguities:
        return ("исполнитель переспросит человека: " + "; ".join(contract.ambiguities)
                + " — сформулируй задачу как «найди / прочитай / посчитай / проверь / объясни»")
    recent = [line.split("] ", 1)[-1] for line in _recent_tasks(root, 20)]
    if any(_similarity([goal, old]) >= _REPEAT_SIMILARITY for old in recent):
        return "задача повторяет недавнюю — возьми другой материал или другой вопрос"
    return ""


def propose_drive_goal(llm: Any, workspace: Path | str, now: datetime | None = None,
                       attempts: int = 3) -> DriveGoal:
    root = Path(workspace)
    now = now or datetime.now(timezone.utc)
    drives = compute_drives(root, now)
    state = _load_state(root)
    drive, state = choose_drive(drives, state, now)
    report = DriveGoal(status="declined", reason="ни один драйв не выше порога содержания")
    if drive is not None:
        feedback = ""
        for _ in range(max(1, attempts)):
            try:
                data = _ask(llm, drive, drives[drive], root, feedback)
            except Exception as exc:  # noqa: BLE001 — отказ модели = отказ выбора, не падение
                report = DriveGoal("declined", "", "", drive, f"вызов модели не удался: {type(exc).__name__}")
                break
            if data is None:
                feedback = "ответ не разобран как JSON с полем goal"
                continue
            goal = " ".join(str(data["goal"]).split())
            feedback = _problem(goal, root)
            if not feedback:
                report = DriveGoal("proposed", goal, str(data.get("success_check") or "").strip(), drive)
                state["last"] = {"drive": drive, "value": drives[drive]["value"], "ts": now.isoformat()}
                break
            report = DriveGoal("declined", goal, "", drive, feedback)
    _save_state(root, state)
    with (root / DECISIONS_RELPATH).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": now.isoformat(), "status": report.status, "drive": report.drive,
                             "goal": report.goal, "success_check": report.success_check,
                             "reason": report.reason, "weights": state["weights"],
                             "drives": {n: round(i["value"], 3) for n, i in drives.items()}},
                            ensure_ascii=False) + "\n")
    return report


if __name__ == "__main__":  # pragma: no cover — ручной осмотр
    import sys

    ws = Path(".")
    if "--dry" in sys.argv:
        d = compute_drives(ws)
        print(choose_drive(d, _load_state(ws), datetime.now(timezone.utc))[0])
    else:
        sys.path.insert(0, str(ws.resolve()))
        from agent_tick import _charter_goal_router, _ensure_env_loaded

        _ensure_env_loaded(ws)
        print(propose_drive_goal(_charter_goal_router(ws).for_role("planner"), ws))
