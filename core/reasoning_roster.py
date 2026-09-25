"""Реестр моделей, которые тратят бюджет вывода на рассуждение («молчавших»).

Вынесено из core/llm.py: клиент LLM — тонкая обёртка над провайдерами, а это
отдельный хранимый орган со своим журналом (``data/reasoning_roster.jsonl``) и
своим правилом дома: где он лежит, решает рантайм в точке входа, а не
библиотека. Клиент только спрашивает `is_known_reasoning_model` и пополняет
`remember_reasoning_model`, когда модель промолчала, истратив бюджет.
"""
from __future__ import annotations

import os
from typing import Any

#: Home of the roster, chosen by the RUNTIME, never by this library. Unset
#: means "no roster here": a store with a default location would be written by
#: anything that ever truncates — the suite did exactly that on 2026-08-29 and
#: banked three invented models into the live journal, after which real budget
#: tests read 8192 where they had asked for 1024. Entry points set it (see
#: `main.py`); libraries and tests get silence unless they ask for a home.
#: Дом реестра молчавших внутри рабочей области. Библиотека сама хранилище
#: НЕ выбирает (решение 2026-08-29: дефолт в библиотеке дал батарее тестов
#: записать три выдуманные модели в живой журнал — и 2026-09-03 при пробе
#: дефолта это повторилось). Дом задаёт РАНТАЙМ в точке входа —
#: `ensure_roster_home` из agent_tick и REPL.
ROSTER_RELPATH = "data/reasoning_roster.jsonl"


def _roster_path() -> Any:
    """Путь к реестру молчавших из окружения; None — рантайм дом не задал."""
    from pathlib import Path

    configured = (os.getenv("AGENT_REASONING_ROSTER") or "").strip()
    return Path(configured) if configured else None


def ensure_roster_home(workspace: Any) -> Any:
    """Точка входа объявляет дом реестра, если оператор не задал свой.

    Замер 2026-09-03: AGENT_REASONING_ROSTER не задавал никто, путь был None
    в каждом живом процессе, реестр не читался и не писался, и думающая
    модель получала лестницу 1200→2400→4800 вместо пола 8192 — три молчания
    по 8 400 токенов за утро. Орган работал; подключения не было. Возвращает
    действующий путь.
    """
    from pathlib import Path

    configured = (os.getenv("AGENT_REASONING_ROSTER") or "").strip()
    if configured:
        return Path(configured)
    home = Path(workspace) / ROSTER_RELPATH
    os.environ["AGENT_REASONING_ROSTER"] = str(home)
    return home


def _roster_key(provider: str, model: str) -> str:
    return f"{(provider or '').strip().lower()}:{(model or '').strip()}"


def is_known_reasoning_model(provider: str, model: str) -> bool:
    """Has this model already proved it spends the budget on thinking?

    A missing or unreadable roster answers "not known" — ignorance must not
    become a verdict, and the caller simply pays the blind leg once more.
    """
    from core.state_integrity import read_state_jsonl

    path = _roster_path()
    if path is None or not path.exists():
        return False
    try:
        rows = read_state_jsonl(path)
    except Exception:  # noqa: BLE001 — a damaged roster must never break a call
        return False
    key = _roster_key(provider, model)
    return any(str(row.get("key")) == key for row in rows)


def remember_reasoning_model(provider: str, model: str, *, spent: int) -> None:
    """Record what silence taught us, so the next run does not buy it again."""
    from core.state_integrity import append_state_jsonl

    path = _roster_path()
    if path is None or is_known_reasoning_model(provider, model):
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        append_state_jsonl(path, [{
            "key": _roster_key(provider, model),
            "provider": (provider or "").strip().lower(),
            "model": (model or "").strip(),
            "evidence": f"truncated with empty text at {int(spent)} output tokens",
        }])
    except Exception:  # noqa: BLE001 — learning is best-effort, answering is not
        return
