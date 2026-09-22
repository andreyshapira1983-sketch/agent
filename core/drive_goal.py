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

from core.drives import DOMAINS, _rows, _similarity, compute_drives, open_obligations

STATE_RELPATH = Path("data") / "drive_state.json"
DECISIONS_RELPATH = Path("data") / "drive_decisions.jsonl"
WAKE_THRESHOLD = 0.5
#: Порог — на значение С УЧЁТОМ привыкания. Замер 2026-09-19 (20 мин «цель
#: первой»): порог стоял на сыром значении 0.2, предметные драйвы дорастают до
#: него ~40 мин (tau 3 ч), а `uncertainty` (0.89, растёт от собственных
#: детекторов) оставался единственным над порогом — 12 из 14 задач «разобрать
#: наблюдение о детекторах» при его весе 0.1. Приевшееся 0.09 не должно
#: выигрывать у растущего предмета 0.17.
MIN_CONTENT = 0.05
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
    #: Действие, которым эта цель делается (пусто — заход агента на цель).
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
        items = [str(r.get("summary") or r.get("operation"))[:200] for r in open_obligations(root)][:5]
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
        "посчитать, проверить, объяснить. В success_check файлы называй только полным путём "
        "от корня рабочей папки и не угадывай заранее номера и страницы. Верни только JSON: "
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


#: Конспекты предметных задач (data/ исключена из git — дерево не пачкается).
NOTES_DIR = "data/notes"


def _with_notes(goal: str, check: str, drive: str, now: datetime) -> tuple[str, str]:
    """Предметная задача заканчивается конспектом, и проверка смотрит на него.

    Кампания 2026-09-22: _problem требует, чтобы файлы критерия уже были на
    диске, — значит, критерий называл входную книгу; судья цели (0630e28)
    честно не засчитывает след старше начала цели. Вместе: «прочитай раздел
    книги» не засчитывалась НИКОГДА, даже выполненная, а прочитанное
    пропадало в журнале. Конспект — новый след работы и накопленное знание.
    """
    note = f"{NOTES_DIR}/{now.strftime('%Y%m%dT%H%M%S')}_{drive}.md"
    goal = (f"{goal} Итог — формулировку, ключевой шаг и результат проверки с источником — "
            f"запиши файлом {note}.")
    return goal, f"{check} Файл {note} создан и содержит итог задачи.".strip()


def _problem(goal: str, root: Path, success_check: str = "") -> str:
    """Почему задачу нельзя выдать, или пусто.

    Критерий успеха исполнитель сверяет с диском (core/success_check.py):
    каждый названный в нём файл обязан существовать. Живой проход шага 3:
    модель написала «цитата из Judson_AbstractAlgebra.txt (например, Theorem
    9.x)» — голое имя без пути и заглушка; теорему агент нашёл верно, а
    исход записан как «следа нет», и драйв математики наказан за сделанную
    работу. Задачи здесь — чтение и расчёт, новых файлов они не создают,
    поэтому критерий сверяется тем же проверяющим ДО выдачи.
    """
    from core.completion_contract import derive_completion_contract
    from core.success_check import observe_success_check

    contract = derive_completion_contract(goal)
    if contract.ambiguities:
        return ("исполнитель переспросит человека: " + "; ".join(contract.ambiguities)
                + " — сформулируй задачу как «найди / прочитай / посчитай / проверь / объясни»")
    recent = [line.split("] ", 1)[-1] for line in _recent_tasks(root, 20)]
    if any(_similarity([goal, old]) >= _REPEAT_SIMILARITY for old in recent):
        return "задача повторяет недавнюю — возьми другой материал или другой вопрос"
    missing = observe_success_check(success_check, root)["missing"] if success_check else []
    if missing:
        return ("критерий успеха называет файлы, которых нет на диске: " + ", ".join(missing)
                + " — называй файлы полным путём от корня рабочей папки, не угадывай номера "
                  "и страницы заранее; что должно быть в ответе, опиши словами")
    return ""


def _observation_goal(root: Path) -> DriveGoal | None:
    """Непонятное разбирает штатный подъём (`explain_causal_observation`), не проза.

    Суточный прогон 2026-09-19/20: модель сама придумывала, где искать, лезла в
    data/charter_decisions.jsonl (детекторов там нет) и 230 раз отвечала
    «данных нет». Прозаический разбор не закрывает наблюдение ничем: закрывает
    его только заявка с конкурирующими объяснениями, а её пишет это действие.
    """
    try:
        from core.causal_climb_action import unexplained_observations

        naked = unexplained_observations(root)
    except Exception:  # noqa: BLE001 — без лестницы драйв просто молчит
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
    """Правку своего кода предлагает штатное действие, а не проза о нём.

    Замер 2026-09-19/20: после включения драйвов агент читал свой код в 133
    задачах из 198 и не подал НИ ОДНОЙ заявки на правку. Путь к изменению кода
    лежал в меню, а режим «цель первой» меню обходит; задачи от драйвов по
    построению только читают. Здесь цель называет свободный модуль и своё
    действие — `propose_engineering_task`, всё та же полоса и те же ворота.
    """
    from core.drives import self_improvement_proofs

    targets = self_improvement_proofs(root)
    if not targets:
        return None
    target, _lines, proof = targets[0]
    # Цель называет ДОКАЗАТЕЛЬСТВО и действие, которое из него следует, а не
    # толщину файла; успех — след правки в коде, а не появление бумажки.
    # До 2026-09-21: «Разбить свой модуль X (N строк)… в X_helpers.py» и
    # «в data/approval_inbox.jsonl появилась новая заявка» — критерий мерил
    # бумажку и не отличал сделанное от несделанного (core/success_check.py).
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
    if drive in ("uncertainty", "self_improvement_need"):
        # Самоулучшение — сначала открытый дефект реестра правкой (core/patch_route.py).
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
            except Exception as exc:  # noqa: BLE001 — отказ модели = отказ выбора, не падение
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

    # Ядро не зависит от точки входа (INV-1): прежний осмотр импортировал
    # `agent_tick` и ломал инвариант слоёв (внесено 2026-09-19 вместе с шагом 2,
    # найдено полным прогоном 2026-09-20). Роутер собирается здесь же.
    from dotenv import load_dotenv

    from core.model_router import ModelRouter

    ws = Path(".")
    if "--dry" in sys.argv:
        d = compute_drives(ws)
        print(choose_drive(d, _load_state(ws), datetime.now(timezone.utc))[0])
    else:
        load_dotenv(ws / ".env")
        print(propose_drive_goal(ModelRouter.from_env().for_role("planner"), ws))
