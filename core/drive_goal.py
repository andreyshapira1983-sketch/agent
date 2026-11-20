"""Task from a drive: internal state decides what matters, the model decides what to do (`python -m core.drive_goal [--dry]`)."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.drives import DOMAINS, _rows, _similarity, compute_drives, open_obligations

STATE_RELPATH = Path("data") / "drive_state.json"
DECISIONS_RELPATH = Path("data") / "drive_decisions.jsonl"
# Threshold is on the value WITH habituation: a habituated 0.09 must not beat a growing subject at
# 0.17.
MIN_CONTENT = 0.05
_NOT_CONTENT = frozenset({"idle_time", "economic_opportunity"})
# Share of choices where the subject area is taken at random instead of by drive strength
# (Forestier, Mollard, Oudeyer, JMLR 2022: 20% random goal space, 80% by progress).
# Steady rhythm instead of a coin: every EXPLORE_EVERY-th choice takes the area that has been in
# such a choice longest ago; reproducible from the journal.
EXPLORE_SHARE = 0.2
EXPLORE_EVERY = round(1 / EXPLORE_SHARE)
_REPEAT_SIMILARITY = 0.6


@dataclass
class DriveGoal:
    """Choice report: the campaign accepts an object with `goal` and `success_check`."""

    status: str  # "proposed" | "declined"
    goal: str = ""
    success_check: str = ""
    drive: str = ""
    reason: str = ""
    # Action by which this goal is done (empty: an agent pass on the goal).
    action: str = ""


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
    """Drive for the task content with habituation; state is updated in place.

    Every EXPLORE_EVERY-th choice is the subject area in turn (mode="random" in drives); the rest take the strongest drive.
    """
    weights: dict[str, float] = state["weights"]
    last = state.get("last")
    # A random choice says nothing about habituation to the area: its "did not fall" must not cut
    # its weight in an ordinary choice.
    if last and last.get("random"):
        state["last"] = None
        last = None
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
    subjects = sorted(n for n in drives if n.startswith("competence_"))
    state["choices"] = int(state.get("choices", 0)) + 1
    if subjects and state["choices"] % EXPLORE_EVERY == 0:
        explored: dict[str, str] = state.setdefault("explored", {})
        pick = min(subjects, key=lambda n: (explored.get(n, ""), n))
        explored[pick] = now.isoformat()
        drives[pick]["mode"] = "random"
        return pick, state
    scored = {n: info["value"] * weights.get(n, 1.0) for n, info in drives.items() if n not in _NOT_CONTENT}
    scored = {n: v for n, v in scored.items() if v >= MIN_CONTENT}
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
    """Latest goals with the best outcome for each."""
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
    """A need in human words and its concrete material, without variable names.

    A variable name is wiring, not a need.
    """
    # Area interest is progress or novelty (core/drives.py), not hours.
    if drive.startswith("competence_") and drive != "competence_world":
        area = _DOMAIN_RU.get(drive.removeprefix("competence_"), drive)
        if info.get("mode") == "explore":
            return (f"По {area} у тебя почти нет недавних попыток. Задача: попробовать что-то "
                    f"из своих книг по {area} и проверить это расчётом или цитатой.")
        if info.get("mode") == "random":
            return (f"Для разнообразия, а не по счёту — задача по {area}. Возьми из своих книг "
                    f"по {area} то, чего ещё не было, и проверь расчётом или цитатой.")
        return (f"По {area} у тебя сейчас меняется умение ({info['why']}). Задача: учиться дальше "
                f"там, где идёт прогресс: задача из своих книг по {area} чуть труднее недавних, "
                "с проверкой расчётом или цитатой.")
    if drive == "competence_world":
        if info.get("mode") == "random":
            return ("Для разнообразия, а не по счёту — интернет. Найди в сети то, чего нет в твоих "
                    "книгах, по первоисточнику, и проверь.")
        if info.get("mode") == "explore":
            return ("Из интернета ты почти ничего не узнавал в последнее время. Задача: найти в сети то, "
                    "чего нет в твоих книгах, по первоисточнику, и проверить.")
        return (f"В поиске по интернету у тебя сейчас меняется умение ({info['why']}). Задача: найти "
                "в сети то, чего нет в твоих книгах, по первоисточнику, чуть труднее недавнего, и проверить.")
    if drive == "uncertainty":
        try:
            from core.causal_climb_action import unexplained_observations

            items = [str(o.observed_mismatch)[:200] for o in unexplained_observations(root)][:5]
        except Exception:  # noqa: BLE001 — without the ladder, no list
            items = []
        return ("Есть вещи, которые ты наблюдал и не можешь объяснить:\n" + "\n".join(f"- {i}" for i in items)
                + "\nЗадача: разобрать ОДНУ из них до проверяемого вывода или честного «данных нет».")
    if drive == "unfinished_obligations":
        items = [str(r.get("summary") or r.get("operation"))[:200] for r in open_obligations(root)][:5]
        return ("Незавершённые дела ждут:\n" + "\n".join(f"- {i}" for i in items)
                + "\nЗадача: закрыть или честно разобрать одно из них.")
    if drive == "maintenance_need":
        broken = [f"{r.get('action')}: {str(r.get('reason') or '')[:150]}"
                  for r in _rows(root / "data" / "campaign_ledger.jsonl")[-20:] if r.get("result") in ("failed", "empty")]
        return ("Последние циклы без результата:\n" + "\n".join(f"- {b}" for b in broken[-5:])
                + "\nЗадача: найти причину одного из них.")
    if drive == "novelty_need":
        return ("Последние задачи однотипны. Задача: взять другую область, чем в последних задачах.")
    return info.get("why", drive)


def _ask(llm: Any, drive: str, info: dict[str, Any], root: Path, feedback: str = "") -> dict[str, Any] | None:
    from core.workspace_inventory import unattended_tools, workspace_inventory

    system = (
        "Ты — автономный агент. Сейчас у тебя есть потребность — ниже она описана. Предложи "
        "себе ОДНУ короткую задачу, которая её удовлетворит, — задачу о самом предмете, а не "
        "о твоём устройстве: конкретную, выполнимую за один заход и "
        "проверяемую (точный файл или источник, что найти или посчитать, как проверить — "
        "цитата со страницей, расчёт в python_probe, первоисточник в интернете). Не повторяй "
        "недавние задачи. Задачу формулируй по-русски, как просьбу найти, прочитать, "
        "посчитать, проверить, объяснить. В success_check файлы называй только полным путём "
        "от корня рабочей папки и не угадывай заранее номера и страницы. Верни только JSON: "
        '{"goal": "<текст задачи>", "success_check": "<как понять, что выполнено>"}'
    )
    parts = ["Что тебе сейчас нужно:\n" + need_text(drive, info, root)]
    sample = _library_sample(root, drive)
    if sample:
        parts.append("Книги этой области у тебя:\n" + "\n".join(sample))
    parts.append("Что лежит в рабочей папке:\n" + "\n".join(workspace_inventory(root)))
    parts.append("Инструменты: " + ", ".join(unattended_tools(root)))
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


# Notes of subject tasks (data/ is excluded from git, so the tree stays clean).
NOTES_DIR = "data/notes"


def _with_notes(goal: str, check: str, drive: str, now: datetime) -> tuple[str, str]:
    """A subject task ends with a note, and the check looks at it.

    _problem requires the criterion files to exist already, and the goal judge does not count a trace older than the goal start, so "read a book section" was never counted. The note is a new trace of work and accumulated knowledge.
    """
    from core.note_contract import criterion

    note = f"{NOTES_DIR}/{now.strftime('%Y%m%dT%H%M%S')}_{drive}.md"
    goal = (f"{goal} Итог запиши файлом {note}: источник (путь в рабочей папке или ссылка), "
            "дословная цитата строкой с «>», и проверка с числом (расчёт или python_probe). "
            "Пустые скобки-заглушки не годятся — конспект пишется после чтения.")
    # The criterion is set by CODE, not by the model: a model that writes the check finds a
    # loophole.
    return goal, criterion(note)


# Start of the tail that `_with_notes` appends to every goal.
_NOTE_TAIL = " Итог запиши файлом "
_QUOTED = re.compile(r"'([^']{12,})'|«([^»]{12,})»|\"([^\"]{12,})\"")


def _goal_subject(goal: str) -> str:
    return str(goal or "").split(_NOTE_TAIL, 1)[0].strip()


def _quoted(text: str) -> set[str]:
    return {" ".join(next(g for g in m.groups() if g).lower().split()) for m in _QUOTED.finditer(text)}


def repeats_recent_goal(goal: str, root: Path, limit: int = 20) -> bool:
    """Same task as a recent one: by words without the note tail or by the same quote."""
    subject = _goal_subject(goal)
    quotes = _quoted(subject)
    recent: list[str] = []
    for row in _rows(root / "data" / "campaign_ledger.jsonl"):
        old = _goal_subject(str(row.get("goal") or ""))
        if old:
            recent = [g for g in recent if g != old] + [old]
    for old in recent[-limit:]:
        if _similarity([subject, old]) >= _REPEAT_SIMILARITY or quotes & _quoted(old):
            return True
    return False


def _problem(goal: str, root: Path, success_check: str = "") -> str:
    """Why the task cannot be issued, or empty.

    The executor checks the success criterion against disk (core/success_check.py): every named file must exist. These tasks create no new files, so the same checker verifies the criterion BEFORE issuing (a bare name without a path and a placeholder are rejected).
    """
    from core.completion_contract import derive_completion_contract
    from core.success_check import observe_success_check

    contract = derive_completion_contract(goal)
    if contract.ambiguities:
        return ("исполнитель переспросит человека: " + "; ".join(contract.ambiguities)
                + " — сформулируй задачу как «найди / прочитай / посчитай / проверь / объясни»")
    if repeats_recent_goal(goal, root):
        return "задача повторяет недавнюю — возьми другой материал или другой вопрос"
    missing = observe_success_check(success_check, root)["missing"] if success_check else []
    if missing:
        return ("критерий успеха называет файлы, которых нет на диске: " + ", ".join(missing)
                + " — называй файлы полным путём от корня рабочей папки, не угадывай номера "
                  "и страницы заранее; что должно быть в ответе, опиши словами")
    return ""


def _observation_goal(root: Path) -> DriveGoal | None:
    """The unclear is handled by the standard `explain_causal_observation` lift, not prose.

    A prose analysis closes no observation; only a request with competing explanations does, and this action writes it.
    """
    try:
        from core.causal_climb_action import unexplained_observations

        naked = unexplained_observations(root)
    except Exception:  # noqa: BLE001 — without the ladder the drive is simply silent
        return None
    if not naked:
        return None
    first = naked[0]
    return DriveGoal(
        status="proposed",
        goal="Объяснить наблюдение о себе: " + str(first.observed_mismatch)[:400],
        success_check=("в data/causal_claims.jsonl появилась заявка по отпечатку "
                       f"{first.fingerprint} с двумя конкурирующими объяснениями и предсказаниями"),
        drive="uncertainty",
        action="explain_causal_observation",
    )


def _engineering_goal(root: Path) -> DriveGoal | None:
    """Own-code edits are proposed by the standard action, not prose about it.

    Drive tasks only read by construction, and the goal-first mode bypasses the menu. Here the goal names a free module and its own action `propose_engineering_task`, through the same lane and gates.
    """
    from core.drives import self_improvement_proofs

    targets = self_improvement_proofs(root)
    if not targets:
        return None
    target, _lines, proof = targets[0]
    # The goal names the EVIDENCE and the action that follows from it, not file size; success is a
    # code-edit trace, not a paper appearing.
    gone = " ".join(f"undefined:{name}@{target}" for name in proof.names[:3])
    if proof.kind == "dup":
        goal = (f"Свести повтор в модуле {target}: {proof.describe(target)}. Оставить одну "
                f"копию в {proof.other}, в {target} взять её импортом — заявкой в ящик "
                "одобрений; полоса самоправок и её ворота остаются прежними")
        check = f"{gone} — копия в {target} убрана, остаётся в {proof.other}"
    else:
        goal = (f"Раскол модуля {target} по доказательству — {proof.describe(target)}. "
                "Вынести эту группу в отдельный модуль (раскольщик называет его по общему слову "
                "имён группы, без нумерации _helpers2) — заявкой "
                "в ящик одобрений; полоса самоправок и её ворота остаются прежними")
        check = f"{gone} — группа вынесена из {target}"
    return DriveGoal(
        status="proposed", goal=goal, success_check=check,
        drive="self_improvement_need", action="propose_engineering_task",
    )


def propose_drive_goal(llm: Any, workspace: Path | str, now: datetime | None = None,
                       attempts: int = 3) -> DriveGoal:
    root = Path(workspace)
    now = now or datetime.now(timezone.utc)
    drives = compute_drives(root, now)
    state = _load_state(root)
    drive, state = choose_drive(drives, state, now)
    report = DriveGoal(status="declined", reason="ни один драйв не выше порога содержания")
    if drive == "stuck_need":
        # Stuck: ask, internet first, then a partner (core/stuck_route.py).
        from core.stuck_route import stuck_goal

        report = stuck_goal(root) or report
        if report.status == "proposed":
            state["last"] = {"drive": drive, "value": drives[drive]["value"], "ts": now.isoformat()}
    elif drive in ("uncertainty", "self_improvement_need"):
        # Self-improvement: first an open registry defect by an edit (core/patch_route.py).
        from core.patch_route import defect_goal

        made = (_observation_goal(root) if drive == "uncertainty"
                else defect_goal(root) or _engineering_goal(root))
        report = made or report
        if report.status == "proposed":
            state["last"] = {"drive": drive, "value": drives[drive]["value"], "ts": now.isoformat()}
    elif drive is not None:
        feedback = ""
        for _ in range(max(1, attempts)):
            try:
                data = _ask(llm, drive, drives[drive], root, feedback)
            except Exception as exc:  # noqa: BLE001 — a model refusal is a choice refusal, not a crash
                report = DriveGoal("declined", "", "", drive, f"вызов модели не удался: {type(exc).__name__}")
                break
            if data is None:
                feedback = "ответ не разобран как JSON с полем goal"
                continue
            goal = " ".join(str(data["goal"]).split())
            check = str(data.get("success_check") or "").strip()
            feedback = _problem(goal, root, check)
            if not feedback:
                goal, check = _with_notes(goal, check, drive, now)
                report = DriveGoal("proposed", goal, check, drive)
                state["last"] = {"drive": drive, "value": drives[drive]["value"], "ts": now.isoformat(),
                                 "random": drives[drive].get("mode") == "random"}
                break
            report = DriveGoal("declined", goal, "", drive, feedback)
    _save_state(root, state)
    with (root / DECISIONS_RELPATH).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": now.isoformat(), "status": report.status, "drive": report.drive,
                             "goal": report.goal, "success_check": report.success_check,
                             "reason": report.reason, "weights": state["weights"],
                             "mode": drives.get(report.drive, {}).get("mode", ""),
                             "drives": {n: round(i["value"], 3) for n, i in drives.items()}},
                            ensure_ascii=False) + "\n")
    return report


if __name__ == "__main__":  # pragma: no cover — manual inspection
    import sys

    # The core does not depend on the entry point (INV-1): the router is assembled here.
    from dotenv import load_dotenv

    from core.model_router import ModelRouter

    ws = Path(".")
    if "--dry" in sys.argv:
        d = compute_drives(ws)
        print(choose_drive(d, _load_state(ws), datetime.now(timezone.utc))[0])
    else:
        load_dotenv(ws / ".env")
        print(propose_drive_goal(ModelRouter.from_env().for_role("planner"), ws))
