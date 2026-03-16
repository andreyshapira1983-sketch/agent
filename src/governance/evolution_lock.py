"""
Блокировка для координации нескольких агентов: только один может применять патч (accept_patch_to_stable) в момент времени.
Предотвращает конфликты при create_agent_family, когда несколько агентов патчат код одновременно.

Использование: в начале accept_patch_to_stable — acquire(agent_id); в конце (success или fail) — release().
Состояние в data/evolution_lock.json. TTL: если держатель не освободил lock дольше N секунд, считаем lock свободным.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
_DATA_DIR = _ROOT / "data"
_LOCK_FILE = _DATA_DIR / "evolution_lock.json"
LOCK_TTL_SECONDS = int(os.environ.get("EVOLUTION_LOCK_TTL_SECONDS", "120"))


def _load() -> dict:
    if not _LOCK_FILE.exists():
        return {"holder": "", "since": 0}
    try:
        data = json.loads(_LOCK_FILE.read_text(encoding="utf-8"))
        return {"holder": (data.get("holder") or ""), "since": float(data.get("since") or 0)}
    except Exception:
        return {"holder": "", "since": 0}


def _save(holder: str, since: float) -> None:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    _LOCK_FILE.write_text(
        json.dumps({"holder": holder, "since": since}, ensure_ascii=False),
        encoding="utf-8",
    )


def get_holder() -> str | None:
    """Кто сейчас держит lock, или None если свободен (или истёк TTL)."""
    state = _load()
    holder = (state.get("holder") or "").strip()
    if not holder:
        return None
    since = state.get("since") or 0
    if time.time() - since > LOCK_TTL_SECONDS:
        _save("", 0)
        return None
    return holder


def acquire(agent_id: str) -> bool:
    """
    Захватить lock для применения патча. Возвращает True если захвачен, False если уже занят.
    agent_id: идентификатор агента (root или id дочернего из family).
    """
    current = get_holder()
    if current is not None:
        return False
    _save(agent_id.strip(), time.time())
    return True


def release() -> None:
    """Освободить lock (вызывать после accept_patch_to_stable в finally)."""
    _save("", 0)
