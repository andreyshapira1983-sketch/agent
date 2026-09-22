"""Внутренние драйвы — физиология вокруг модели, считаемая из журналов.

Суточный прогон 2026-09-19 показал: будильник у агента был (кампания будит его
каждые 30 с), но просыпался он всякий раз в том же состоянии — внутри ничего
не копилось, и выбор цели шёл к ближайшему: к собственным детекторам. Человек
встаёт не из-за строки «цель», а потому что в нём постоянно меняются
эндогенные величины — голод, давление сна, скука, незавершённость (оператор,
2026-09-19).

Этот модуль — ТОЛЬКО измерение. Каждый драйв считается из того, что уже лежит
на диске: журнала кампании, эпизодов, ящика одобрений, лестницы наблюдений.
Модель ничего не выдумывает; значение — число 0..1 с объяснением, откуда оно.
Драйв растёт со временем без нужного события и гасится этим событием:
`1 - exp(-часов / tau)`. Поведение агента здесь не меняется.

    python -m core.drives            # текущие драйвы этой рабочей папки
"""
from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

#: Области знания и признак эпизода в ней — ПОЛНЫЙ путь папки: «cs/txt»
#: сидит внутри «physics/txt», и подстрока засчитывала физику информатике.
DOMAINS: dict[str, tuple[str, ...]] = {
    "math": ("math_study/",),
    "physics": ("knowledge_library/physics/",),
    "cs": ("knowledge_library/cs/",),
}
_WEB_TOOLS = frozenset({"web_search", "web_fetch", "rss_fetch"})

#: Время (часы), за которое драйв без события поднимается до ~0.63.
TAU_HOURS = {"idle_time": 0.5, "competence": 3.0, "world": 6.0, "self": 6.0}
_WORK_RESULTS = frozenset({"completed"})
#: Поломка — действие было, а результата нет. Простой («нечего делать») и пропуск
#: повтора — не поломка: это `idle_time`, иначе один факт считался бы дважды.
_BROKEN_RESULTS = frozenset({"failed", "empty"})
_WORD = re.compile(r"[a-zа-яё0-9_]{3,}", re.IGNORECASE)


def _rows(path: Path) -> list[dict[str, Any]]:
    """Строки jsonl; обёртка целостности (`payload`) снимается."""
    out: list[dict[str, Any]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else row
        if isinstance(payload, dict):
            out.append(payload)
    return out


def _ts(value: Any) -> datetime | None:
    try:
        t = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _growth(since: datetime | None, now: datetime, tau: float) -> float:
    if since is None:
        return 1.0
    hours = max(0.0, (now - since).total_seconds() / 3600)
    return 1.0 - math.exp(-hours / tau)


def _ago(since: datetime | None, now: datetime) -> str:
    if since is None:
        return "ни разу"
    minutes = int((now - since).total_seconds() // 60)
    return f"{minutes} мин назад" if minutes < 120 else f"{minutes // 60} ч назад"


def episode_domains(episode: dict[str, Any]) -> set[str]:
    """Области, в которых работал эпизод: по путям в вопросе/источниках и по веб-инструментам."""
    text = " ".join([str(episode.get("question") or ""),
                     " ".join(str(s) for s in episode.get("source_labels") or [])]).replace("\\", "/")
    found = {d for d, marks in DOMAINS.items() if any(m in text for m in marks)}
    if _WEB_TOOLS & set(episode.get("tools_used") or []):
        found.add("world")
    return found


def _similarity(goals: list[str]) -> float:
    """Средняя попарная схожесть последних целей (Жаккар по словам)."""
    sets = [set(_WORD.findall(g.lower())) for g in goals if g]
    sets = [s for s in sets if s]
    pairs = [(a, b) for i, a in enumerate(sets) for b in sets[i + 1:]]
    if not pairs:
        return 0.0
    return sum(len(a & b) / len(a | b) for a, b in pairs) / len(pairs)


#: Заявки о правах самого прогона: сдвинуть их может только человек.
_HUMAN_ONLY_OPERATIONS = frozenset({"autonomous_runtime.allow_effects", "autonomous_runtime.standing_grant"})


def open_obligations(root: Path) -> list[dict[str, Any]]:
    """Ожидающие заявки, которые агент САМ может сдвинуть.

    Живой проход шага 3 (2026-09-19): все четыре «незавершённых дела» были
    заперты — три разбиения модулей закрыты уроками отката (полоса их больше
    не возьмёт), четвёртое — просьба о правах, ждущая человека. Драйв звал к
    ним, агент упирался в approval_wait, и гасило это одно привыкание. Дело,
    которое агент не может сдвинуть, — не его незавершённое дело.
    """
    from core.self_build_rules import blocking_lesson

    out = []
    for r in _rows(root / "data" / "approval_inbox.jsonl"):
        if r.get("status") != "pending" or r.get("operation") in _HUMAN_ONLY_OPERATIONS:
            continue
        files = (r.get("payload") or {}).get("files") or []
        targets = [f.get("path") if isinstance(f, dict) else f for f in files]
        if targets and blocking_lesson(root, targets) is not None:
            continue
        out.append(r)
    return out


#: Потребность «улучшить себя» меряется двумя вещами: сколько прошло с
#: последней СВОЕЙ правки кода и есть ли материал — крупный собственный модуль,
#: который сейчас никто не ждёт в ящике и по которому нет урока отката. Замер
#: 2026-09-19/20: после включения драйвов агент читал свой код в 133 задачах из
#: 198 и не подал ни одной заявки на правку — потребности «сделать себя лучше»
#: в списке не было вовсе, а задачи от драйвов по построению только читают.
_OWN_CODE_DIRS = ("core", "tools")
_SELF_CHANGE_OPS = frozenset({"self_apply_lane.run"})


def last_self_change(root: Path) -> datetime | None:
    """Когда агент в последний раз ИЗМЕНИЛ свой код (исполненная заявка полосы)."""
    stamps = [t for r in _rows(root / "data" / "approval_inbox.jsonl")
              if r.get("operation") in _SELF_CHANGE_OPS and r.get("status") == "executed"
              for t in [_ts(r.get("updated_at") or r.get("created_at"))] if t]
    return max(stamps, default=None)


def self_improvement_targets(root: Path, limit: int = 5) -> list[tuple[str, int]]:
    """Свои модули, по которым правку можно предложить ПРЯМО СЕЙЧАС — только с
    доказательством, сильные первыми (подробности: `self_improvement_proofs`)."""
    return [(rel, lines) for rel, lines, _proof in self_improvement_proofs(root, limit)]


def _paused_targets(root: Path) -> frozenset[str]:
    """Файлы на паузе у рук: недавно отклонённые человеком и отказанные
    раскольщиком без изменений. 2026-09-21: цель кампании трижды подряд
    выбирала core/scheduler.py, а руки его не брали (target_denied_recently),
    и цикл уходил в простой — голова не видела паузы рук.
    """
    # Ящик читается сырыми строками (`_rows`), а не объектом ApprovalInbox: тот
    # при открытии «чинит» строки без обёртки целостности, а читатель цели
    # ничего переписывать не вправе (полный прогон 2026-09-21 поймал это на
    # соседнем замере last_self_change).
    from core.approval_inbox import _DENIAL_MEMORY_HOURS, _payload_targets, _within_hours
    from core.self_apply_bridge import SELF_APPLY_OPERATION
    from core.splitter_refusals import refused_unchanged

    denied: set[str] = set()
    for row in _rows(root / "data" / "approval_inbox.jsonl"):
        if (row.get("status") == "denied" and row.get("operation") == SELF_APPLY_OPERATION
                and _within_hours(row.get("updated_at"), _DENIAL_MEMORY_HOURS)):
            denied.update(_payload_targets(row.get("payload") or {}))
    return frozenset(denied) | refused_unchanged(root)


def self_improvement_proofs(root: Path, limit: int = 5) -> list[tuple[str, int, Any]]:
    """Свои модули с ДОКАЗАТЕЛЬСТВОМ, что их надо переделать, и само доказательство.

    До 2026-09-21 цель выбиралась по числу строк — самый толстый файл первым.
    Шесть файлов в пределах 180 строк друг от друга: разрежешь первый, корона
    переедет на второй, финиша нет по построению; 139 из 141 самостоятельных
    целей были «прочитать себя» или «разбить себя». Требование оператора:
    прежде чем резать — доказать; размер доказательством не является.
    Доказательства — `core/split_proof.py`; без него модуль не цель, даже самый
    большой. Порядок: вид доказательства, его вес, размер — последним.

    Отсеиваются занятые: то, что уже ждёт человека в ящике, и то, что закрыто
    уроком отката, — предлагать их значит снова упереться в approval_wait.
    Критический орган (CRITICAL_DENY) отсеивается по правилу САМОГО
    производителя заявок: живой запуск 2026-09-20 выбрал крупнейший модуль
    core/self_build_producer.py и получил «grounded target is critical» —
    материал драйва обязан совпадать с тем, что полоса вообще берёт.
    """
    from core.self_build_producer import _is_critical
    from core.self_build_rules import blocking_lesson
    from core.split_proof import index_workspace, proof_for, rank_key

    # Производитель заявок молчит, пока в ящике есть НЕРЕШЁННАЯ заявка полосы —
    # одна за раз, и решает её человек (`_has_pending_self_build_proposal`).
    # Пока так, материала нет ни по одному модулю: иначе драйв зовёт в запертую
    # дверь и цикл кончается пустым исходом (живой запуск 2026-09-20).
    if any(r.get("operation") in _SELF_CHANGE_OPS and r.get("status") == "pending"
           for r in _rows(root / "data" / "approval_inbox.jsonl")):
        return []
    index = index_workspace(root)
    paused = _paused_targets(root)
    out: list[tuple[str, int, Any]] = []
    for rel, mod in index.items():
        if not rel.startswith(tuple(f"{d}/" for d in _OWN_CODE_DIRS)):
            continue
        if _is_critical(rel) or rel in paused or blocking_lesson(root, [rel]) is not None:
            continue
        proof = proof_for(rel, index)
        if proof is not None:
            out.append((rel, mod.lines, proof))
    out.sort(key=lambda item: rank_key(item[2], item[1]))
    return out[:limit]


def _open_defect_count(root: Path) -> int:
    """Сколько дефектов реестра ждут починки (0 — реестра нет или он пуст)."""
    try:
        from core.self_improvement_issues import DEFAULT_ISSUE_PATH, SelfImprovementIssueRegistry

        return len(SelfImprovementIssueRegistry(root / DEFAULT_ISSUE_PATH).unresolved())
    except Exception:  # noqa: BLE001 — нечитаемый реестр = ноль поводов, остальное считается
        return 0


def compute_drives(workspace: Path | str, now: datetime | None = None) -> dict[str, dict[str, Any]]:
    """Все драйвы: {имя: {"value": 0..1, "why": строка}}. Только чтение диска."""
    root = Path(workspace)
    now = now or datetime.now(timezone.utc)
    ledger = _rows(root / "data" / "campaign_ledger.jsonl")
    episodes = _rows(root / "data" / "episodic_memory.jsonl")
    drives: dict[str, dict[str, Any]] = {}

    last_work = max((t for r in ledger if r.get("result") in _WORK_RESULTS
                     for t in [_ts(r.get("ts"))] if t), default=None)
    drives["idle_time"] = {"value": _growth(last_work, now, TAU_HOURS["idle_time"]),
                           "why": f"последний полезный цикл: {_ago(last_work, now)}"}

    for domain in [*DOMAINS, "world"]:
        last = max((t for e in episodes if e.get("outcome") == "success" and domain in episode_domains(e)
                    for t in [_ts(e.get("created_at"))] if t), default=None)
        tau = TAU_HOURS["world"] if domain == "world" else TAU_HOURS["competence"]
        label = "в интернете" if domain == "world" else f"по области {domain}"
        drives[f"competence_{domain}"] = {"value": _growth(last, now, tau),
                                          "why": f"последняя успешная задача {label}: {_ago(last, now)}"}

    recent_goals: list[str] = []
    for r in reversed(ledger):
        g = str(r.get("goal") or "")
        if g and g not in recent_goals:
            recent_goals.append(g)
        if len(recent_goals) >= 8:
            break
    sim = _similarity(recent_goals)
    drives["novelty_need"] = {"value": sim,
                              "why": f"схожесть последних {len(recent_goals)} целей между собой: {sim:.2f}"}

    pending = open_obligations(root)
    drives["unfinished_obligations"] = {"value": 1.0 - math.exp(-len(pending) / 5),
                                        "why": f"незавершённых дел, которые можно сдвинуть: {len(pending)}"}

    targets = self_improvement_proofs(root)
    changed = last_self_change(root)
    # Открытые дефекты реестра — такой же повод чинить себя, как дубль или
    # раскол: 2026-09-22 вес был ноль при семи открытых дефектах, потому что
    # считались только структурные доказательства, и путь самопочинки
    # (core/patch_route.py) не запускался ни разу.
    open_defects = _open_defect_count(root)
    drives["self_improvement_need"] = {
        "value": (_growth(changed, now, TAU_HOURS["self"]) if targets
                  else 1.0 - math.exp(-open_defects / 3) if open_defects else 0.0),
        "why": (f"последняя своя правка кода: {_ago(changed, now)}; "
                + (f"доказано: {targets[0][2].describe(targets[0][0])}" if targets
                   else f"открытых дефектов в реестре: {open_defects}" if open_defects
                   else "ни доказательства правки, ни открытых дефектов — "
                        "или всё занято ящиком или уроками")),
    }

    tail = ledger[-20:]
    acted = [r for r in tail if r.get("result") in _BROKEN_RESULTS | _WORK_RESULTS]
    broken = sum(1 for r in acted if r.get("result") in _BROKEN_RESULTS)
    drives["maintenance_need"] = {"value": broken / len(acted) if acted else 0.0,
                                  "why": f"из последних {len(acted)} попыток действия сломались: {broken}"}

    try:
        from core.causal_climb_action import unexplained_observations

        unexplained = len(unexplained_observations(root))
    except Exception:  # noqa: BLE001 — нет лестницы — нет и этой величины, остальное считается
        unexplained = 0
    drives["uncertainty"] = {"value": 1.0 - math.exp(-unexplained / 5),
                             "why": f"необъяснённых наблюдений: {unexplained}"}

    # Уткнулся — спроси: интернет и партнёр (слово оператора 2026-09-22:
    # «он забывает, что у него есть интернет и партнёр»). Совет в подсказке
    # механизмом не был: сам он заговорил первым 3 раза за всё время.
    from core.stuck_route import stuck_evidence

    stuck = len(stuck_evidence(root))
    drives["stuck_need"] = {
        "value": 1.0 - math.exp(-stuck / 2) if stuck else 0.0,
        "why": (f"признаков застревания: {stuck}" if stuck else "застревания не видно"),
    }

    drives["economic_opportunity"] = {"value": 0.0,
                                      "why": "источник оплачиваемой работы не подключён — честный ноль"}
    return drives


def strongest(drives: dict[str, dict[str, Any]], threshold: float = 0.5) -> tuple[str, float] | None:
    """Самый сильный драйв выше порога, или None — будить нечего."""
    name, info = max(drives.items(), key=lambda kv: kv[1]["value"])
    return (name, info["value"]) if info["value"] >= threshold else None


if __name__ == "__main__":  # pragma: no cover — ручной осмотр
    import sys

    ws = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
    d = compute_drives(ws)
    for name, info in sorted(d.items(), key=lambda kv: -kv[1]["value"]):
        bar = "#" * round(info["value"] * 20)
        print(f"{name:24s} {info['value']:.2f} {bar:20s} {info['why']}")
    print("strongest:", strongest(d))
