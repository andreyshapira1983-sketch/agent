"""Что самоправка агента не трогает никогда: один список и одно сличение путей.

Список — объединение четырёх прежних (полоса самоприменения, путь починки по
дефектам, производитель самосборки, принимающий burn-in): строже любого из них.
"""
from __future__ import annotations

from core.control_files import CONTROL_RELPATHS

__all__ = ["PROTECTED_FILES", "PROTECTED_TREES", "is_protected", "path_key"]

#: Файлы по точному пути от корня репозитория.
PROTECTED_FILES: frozenset[str] = frozenset({
    # Главные правила, песочницы, сторож подмены инструкций и планки, которыми меряют агента.
    "core/root_principles.py", "core/governance.py", "core/injection_guard.py",
    "tools/python_probe.py", "tools/convert_file.py",
    "tests/test_capability_baseline.py", "tests/capability_tasks.py",
    "scripts/capability_baseline.py", "scripts/check_function_length_baseline.py",
    "scripts/check_ceo_file_baseline.py", "tests/test_ruff_config.py",
    # Тормоза и ворота одобрения: расход, список тормозов, политика, ящик одобрений.
    "core/budget_kill_switch.py", "core/budget_governor.py", "core/budget_ledger.py",
    "core/usd_spend.py", "core/control_files.py", "core/policy.py", "core/approval.py",
    "core/approval_inbox.py", "core/actuation_gateway.py", "core/rule_approved_apply.py",
    # Пути самоправки, их судья, их руки в git и этот список.
    "core/self_apply_lane.py", "core/self_apply_bridge.py", "core/patch_route.py",
    "core/self_repair_utils.py", "core/self_build_producer.py", "core/safe_vcs.py",
    "core/protected_paths.py",
    # Органы, без которых агент не стартует, и его доктрина. `core/loop.py`,
    # `core/model_usage.py` и `docs/COGNITIVE_CORE.md` закрыты только производителю
    # самосборки (CRITICAL_DENY): полосе по диагнозу они открыты нарочно.
    "main.py", "core/autonomous_runtime.py",
    "AGENT_DOCTRINE.md", "docs/AGENT_DOCTRINE.md",
    # Принимающий burn-in, его песочница, запуск и память опыта.
    "core/burn_in_supervisor.py", "core/burn_in_sandbox.py",
    "scripts/burn_in_supervisor.py", "scripts/autonomous_repair.py",
    "scripts/install_daemon.ps1",
    "state/burn_in_head.json", "state/burn_in_offers.jsonl",
    "state/burn_in_adoptions.jsonl", "config/burn_in_sandbox.json",
    # Что git считает мусором: правка открыла бы `git add -A` дорогу к памяти опыта.
    ".gitignore",
    # Файлы-тормоза оператора.
    *CONTROL_RELPATHS,
})

#: Деревья целиком: сверка по префиксу.
PROTECTED_TREES: tuple[str, ...] = ("config/", ".github/", ".git/", "secrets/", ".venv/")


def path_key(path: object) -> str | None:
    """Путь от корня в одном виде для сличения; None — пустой, абсолютный или выходит из корня."""
    raw = str(path).strip().strip('"').replace("\\", "/")
    # Двоеточие — диск или поток NTFS (`file.py::$DATA`): оба не путь внутри корня.
    if not raw or raw.startswith("/") or ":" in raw:
        return None
    parts: list[str] = []
    for seg in raw.split("/"):
        if seg == "..":
            if not parts:
                return None
            parts.pop()
            continue
        # Windows отбрасывает хвостовые точки и пробелы имени: `x.py.` — тот же файл.
        seg = seg.rstrip(" .")
        if seg:
            parts.append(seg)
    # Регистр складывается всегда: лишний отказ дешевле правки за забором.
    return "/".join(parts).casefold() or None


_FILE_KEYS: frozenset[str] = frozenset(k for k in map(path_key, PROTECTED_FILES) if k)
_TREE_KEYS: tuple[str, ...] = tuple(t.casefold() for t in PROTECTED_TREES)


def is_protected(path: object) -> bool:
    """Запретен ли путь для самоправки; путь, выходящий из корня, запретен тоже."""
    if not str(path).strip():
        return False
    key = path_key(path)
    if key is None:
        return True
    return key in _FILE_KEYS or (key + "/").startswith(_TREE_KEYS)
