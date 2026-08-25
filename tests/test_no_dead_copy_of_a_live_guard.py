"""У живого стража не должно быть мёртвых копий с другими настройками.

Замер, отвергнутые варианты и границы: F-7 в docs/audit/FIELD_CHECK_QUEUE.md.
"""
from __future__ import annotations

import ast
import pathlib

_REPO = pathlib.Path(__file__).resolve().parent.parent


def _uncalled_guards() -> list[str]:
    """Функции-стражи, у которых в живом коде нет ни вызова, ни ссылки."""
    import re

    gate = re.compile(
        r"(^_?(refuse|reject|require|forbid|deny|block|guard|validate|check)_)",
        re.IGNORECASE,
    )
    files: list[pathlib.Path] = [_REPO / "agent_tick.py", _REPO / "main.py"]
    for name in ("core", "app", "cli", "api", "tools"):
        files.extend(sorted((_REPO / name).rglob("*.py")))

    defined: dict[str, str] = {}
    referenced: set[str] = set()
    for path in files:
        if not path.exists():
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if gate.search(node.name):
                    defined[node.name] = str(path.relative_to(_REPO))
                continue
            # И ВЫЗОВ, и ССЫЛКА считаются подключением: `Depends(_require_auth)`
            # передаёт стража, не вызывая его, и первая редакция этой пробы
            # объявила его мёртвым именно поэтому.
            if isinstance(node, ast.Name):
                referenced.add(node.id)
            elif isinstance(node, ast.Attribute):
                referenced.add(node.attr)
    return sorted(name for name in defined if name not in referenced)


def test_no_guard_is_defined_without_being_reachable() -> None:
    orphans = _uncalled_guards()

    assert not orphans, (
        "страж определён и ни разу не упомянут в живом коде — либо он мёртвая "
        "копия работающей проверки, либо защита, которую забыли подключить: "
        f"{orphans}"
    )


def test_the_live_url_guard_is_still_wired() -> None:
    """Существо, а не бухгалтерия: живая проверка обязана остаться на месте.

    Без этого первый тест удовлетворялся бы удалением САМОЙ защиты вместо
    удаления её дубликата.
    """
    src = (_REPO / "tools" / "web_fetch.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "validate_url"
    ]

    assert len(calls) >= 2, (
        "web_fetch проверяет URL меньше двух раз — а проверок должно быть две: "
        "до запроса и ПОВТОРНО после перенаправления"
    )
