"""
Справочник профессий для семейки: список профессий, которые можно распределять между агентами.
Добавляешь 5, 120 или сколько угодно — семья сама может распределять роли и создавать агентов под них.
"""
from __future__ import annotations

import json
from pathlib import Path

_root = Path(__file__).resolve().parent.parent.parent
_path = _root / "data" / "professions.json"


def get_professions() -> list[str]:
    """Текущий список профессий (без дубликатов, порядок сохранён)."""
    if not _path.exists():
        return []
    try:
        data = json.loads(_path.read_text(encoding="utf-8"))
        return list(dict.fromkeys(data)) if isinstance(data, list) else []
    except Exception:
        return []


def add_professions(professions: list[str]) -> int:
    """Добавить профессии в справочник (без дубликатов). Возвращает сколько добавлено новых."""
    current = get_professions()
    seen = set(current)
    added = 0
    for p in professions:
        s = (p or "").strip()
        if s and s not in seen:
            current.append(s)
            seen.add(s)
            added += 1
    if added > 0:
        _path.parent.mkdir(parents=True, exist_ok=True)
        _path.write_text(json.dumps(current, ensure_ascii=False, indent=0), encoding="utf-8")
    return added


def get_assigned_roles(agent_id: str) -> list[str]:
    """Роли, уже занятые членами семьи (дети текущего агента). Для сравнения со справочником профессий."""
    from src.agency.supervisor import get_family_tree
    tree = get_family_tree(agent_id)
    children = tree.get("children") or []
    return [c.get("role", "") for c in children if c.get("role")]
