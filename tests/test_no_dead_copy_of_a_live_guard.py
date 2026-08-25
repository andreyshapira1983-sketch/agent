"""У живого стража не должно быть мёртвых копий с другими настройками.

СВЕРКА С ПОЛЕМ, форма F-7 «ворота» (docs/audit/FIELD_CHECK_QUEUE.md). Провал,
названный полем: ворота стоят, но не на живом пути.

ЗАМЕР 2026-08-25. Сплошной разбор нашёл 53 функции-стража; без единого вызова —
три. Две из них оказались ложным следом, и это надо было проверить, а не
списать: `_require_auth` подключён через `Depends(_require_auth)`, то есть
ССЫЛКОЙ, а не вызовом, и мой детектор считал только вызовы.

Настоящая находка — другая, и она тоньше «ворот не на пути». В `web_fetch`
живой путь проверяет URL дважды: до запроса и ПОВТОРНО после перенаправления
(`self._network_policy.validate_url`). Защита работает. Но рядом лежат два
статических дубликата — `_validate_url` и `_check_host_not_local`, — которые не
зовёт никто, а политику собирают СВОЮ, с другими настройками.

ЧЕМ ЭТО ОПАСНО. Не тем, что они не работают, а тем, что следующий читатель
позовёт их, считая живой проверкой, и получит другие настройки. Это порода
«покрытие списали у соседа»: две версии одной проверки, которые расходятся
молча.

Тестов на них не было — то есть удаление ничего не ломает и ничего не скрывает.
Этот тест закрепляет ОТСУТСТВИЕ дубликатов, а не их наличие: если такая копия
заведётся снова, он покраснеет.
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
