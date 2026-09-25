"""Ссылка памяти на код сверяется с кодом в момент чтения.

Запись памяти, выученная на прежнем коде, после отката, раскола или
переименования продолжает называть файл и функцию, которых больше нет. Замер
2026-09-25 на сервере: 36 ссылок на исчезнувшие файлы — 19 в карточках
прошлых ошибок, 15 в уроках самосборки, 2 в долгой памяти. Агент им верит:
другого знания у него нет.

По «Impact Is Not Invalidation» (arXiv 2609.25130): спрашивать надо не «что
поменялось в коде», а «держится ли ЭТО утверждение», и судья — исполнение, а
не догадка; хранимое не уничтожать, раз точность важнее полноты. Здесь —
самая дешёвая проверка, которую исполнение даёт без запуска: существует ли
названный файл и есть ли в нём названное имя (`путь::имя` или `путь:имя`).
Не существует — запись доходит до агента как есть, но с пометкой, что её
адрес устарел и её надо сверить с кодом. Удалять и переписывать память этот
модуль не вправе.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

_CITE_RE = re.compile(
    r"(?<![\w/.])((?:core|tools|cli|app|api|tests|scripts)/[A-Za-z0-9_/]+\.py)"
    # Имя — от трёх знаков: подпись ошибки прячет номер строки под `:N`
    # (карточки прошлых ошибок, замер 2026-09-25), а это не имя.
    r"(?:::?([A-Za-z_][A-Za-z0-9_]{2,}))?"
)
_names_cache: dict[tuple[str, float], frozenset[str]] = {}


def _names(path: Path) -> frozenset[str] | None:
    """Имена верхнего и вложенного уровня файла: функции, классы, присваивания."""
    try:
        key = (str(path), path.stat().st_mtime)
    except OSError:
        return None
    if key not in _names_cache:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, ValueError):
            return None
        found = {n.name for n in ast.walk(tree)
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
        found |= {t.id for n in ast.walk(tree) if isinstance(n, (ast.Assign, ast.AnnAssign))
                  for t in (n.targets if isinstance(n, ast.Assign) else [n.target]) if isinstance(t, ast.Name)}
        _names_cache[key] = frozenset(found)
    return _names_cache[key]


def stale_refs(root: Path | str, text: str) -> list[str]:
    """Ссылки текста на код, которых в рабочей папке больше нет."""
    out: list[str] = []
    for m in _CITE_RE.finditer(text or ""):
        rel, name = m.group(1), m.group(2)
        path = Path(root) / rel
        if not path.is_file():
            ref = f"{rel} (файла больше нет)"
        elif name and (names := _names(path)) is not None and name not in names:
            ref = f"{rel}::{name} (имени в файле больше нет)"
        else:
            continue
        if ref not in out:
            out.append(ref)
    return out


def annotate(root: Path | str | None, text: str) -> str:
    """Текст записи — и пометка, если её ссылки на код устарели."""
    if root is None:
        return text
    stale = stale_refs(root, text)
    if not stale:
        return text
    return f"{text} [АДРЕС УСТАРЕЛ: {'; '.join(stale[:3])} — сверь с кодом, прежде чем опираться]"


def annotate_lines(root: Path | str | None, block: str) -> tuple[str, int]:
    """Пометить каждую строку блока отдельно; вернуть блок и число пометок."""
    if root is None or not block:
        return block, 0
    lines, marked = [], 0
    for line in block.split("\n"):
        new = annotate(root, line)
        marked += new is not line
        lines.append(new)
    return "\n".join(lines), marked
