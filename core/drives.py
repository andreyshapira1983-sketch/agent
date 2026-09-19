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
TAU_HOURS = {"idle_time": 0.5, "competence": 3.0, "world": 6.0}
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

    pending = [r for r in _rows(root / "data" / "approval_inbox.jsonl") if r.get("status") == "pending"]
    drives["unfinished_obligations"] = {"value": 1.0 - math.exp(-len(pending) / 5),
                                        "why": f"незавершённых заявок в ящике: {len(pending)}"}

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
