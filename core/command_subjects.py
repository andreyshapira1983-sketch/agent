"""Команда как ПРЕДМЕТ цели: `:team-run` → модуль, где живёт её обработчик.

Агент пишет цели двумя словарями — путём к файлу и именем команды, — а
распознаватель предмета читал только первый. Карта не ведётся руками: она
РАЗБИРАЕТСЯ из диспетчера, который и маршрутизирует команды, потому что
рукописная разошлась бы с кодом в первый же день.

Читается исходник, а не импортируется модуль: `core` не вправе зависеть от
`cli` — это правило проверяет `tests/test_architecture_invariants.py`, и оно
поймало первую версию этого файла. Замер и границы: MIR-174.
"""
from __future__ import annotations

import re
from pathlib import Path

#: Где ищутся обработчики. Соглашение проекта: команду `:team-run` обслуживает
#: функция `_handle_team_run`, и определена она в `cli/` или в `app/`.
_HANDLER_DIRS: tuple[str, ...] = ("cli", "app")
_DISPATCH_REL = "cli/command_dispatch.py"

_TOKEN_RE = re.compile(r'":([a-z][a-z0-9-]*)"')
_HEAD_TEST_RE = re.compile(r"^\s*if head (?:==|in)\s")
_CALL_RE = re.compile(r"\b(_handle_\w+)\(")
_DEF_RE = re.compile(r"^def (_handle_\w+)\(", re.MULTILINE)

_CACHE: dict[str, dict[str, str]] = {}


def _handler_modules(root: Path) -> dict[str, str]:
    """`_handle_<key>` → относительный путь модуля, где он ОПРЕДЕЛЁН."""
    found: dict[str, str] = {}
    for directory in _HANDLER_DIRS:
        base = root / directory
        if not base.is_dir():
            continue
        for path in sorted(base.glob("*.py")):
            try:
                source = path.read_text(encoding="utf-8")
            except OSError:
                continue
            for name in _DEF_RE.findall(source):
                found.setdefault(name, f"{directory}/{path.name}")
    return found


def _build(root: Path) -> dict[str, str]:
    dispatch = root / _DISPATCH_REL
    try:
        lines = dispatch.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    modules = _handler_modules(root)
    table: dict[str, str] = {}
    for index, line in enumerate(lines):
        if not _HEAD_TEST_RE.match(line):
            continue
        tokens = _TOKEN_RE.findall(line)
        if not tokens:
            continue
        # Обработчик вызывается в пределах ближайших строк ветви; дальше
        # начинается следующая проверка, и приписывать ей эти токены нельзя.
        for follow in lines[index + 1 : index + 4]:
            call = _CALL_RE.search(follow)
            if call is None:
                continue
            module = modules.get(call.group(1))
            if module:
                for token in tokens:
                    table.setdefault(f":{token}", module)
            break
    return table


def command_module(token: str, *, root: Path | str = ".") -> str | None:
    """Модуль, отвечающий за эту команду, или None, если такой команды нет."""
    name = str(token or "").strip().strip("`'\",.;()[]")
    if not name.startswith(":"):
        return None
    key = str(Path(root).resolve())
    table = _CACHE.get(key)
    if table is None:
        table = _build(Path(root))
        _CACHE[key] = table
    return table.get(name)
