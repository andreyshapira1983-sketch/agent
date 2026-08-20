"""Шаблон там, где должен стоять адрес или содержимое."""
from __future__ import annotations

import re

#: Одиночная угловая заготовка целиком: `<to be synthesized …>`, `<insert path>`.
SINGLE_TAG_RE = re.compile(r"^<[^<>\n]+>$")

#: Слова, выдающие незаполненный шаблон даже без пробелов внутри.
PLACEHOLDER_HINTS: tuple[str, ...] = (
    "to be", "tbd", "todo", "placeholder", "synthes",
    "fill in", "fill-in", "fill_me", "fillme",
    "your text", "goes here", "insert ",
)

#: Формы, встречающиеся В ПУТИ. Отдельно от `PLACEHOLDER_HINTS`, потому что путь
#: — не проза: здесь ловятся скобочные подстановки шаблонизаторов и
#: общепринятые «примерные» имена, а не слова английского языка.
_PATH_TEMPLATE_RE = re.compile(
    r"<[^<>/\\]*>"          # <identified_file>, <insert path>
    r"|\{\{[^}]*\}\}"        # {{module}}
    r"|\$\{[^}]*\}"          # ${MODULE}
    r"|(?<![A-Za-z])XXX(?![A-Za-z])"   # test_XXX.py — подчёркивание не граница слова
    , re.IGNORECASE,
)

#: Куски имени, которыми человек помечает «сюда подставить». Проверяются по
#: сегментам пути и по основе имени, а не по подстроке где попало: подстрочный
#: поиск «path/to» поймал бы легитимный `docs/path/tools.md`.
_PATH_PLACEHOLDER_SEGMENTS: frozenset[str] = frozenset({
    "to", "your", "yourfile", "yourpath", "example", "somefile", "filename",
})
_PATH_PLACEHOLDER_MARKS: tuple[str, ...] = (
    "your_file", "your-file", "your_path", "your-path", "file_path_here",
    "path_here", "fill_me", "fillme", "replace_me", "replaceme",
    "todo_", "_todo", "tbd", "placeholder",
)


#: Маркер незавершённой работы В САМОМ НАЧАЛЕ содержимого. Проверяется только у
#: ОДНОСТРОЧНОГО содержимого: файл, который целиком состоит из одной строки
#: «TODO: …», — заготовка по построению, а `# TODO: …` первой строкой обычного
#: файла — законный комментарий, и его трогать нельзя.
_WHOLE_FILE_TODO_RE = re.compile(r"^(?:TODO|FIXME|XXX|HACK)\b\s*[:\-—]", re.IGNORECASE)


def looks_like_unfilled_content(content: str) -> bool:
    """True, когда всё содержимое — одна незаполненная заготовка."""
    stripped = (content or "").strip()
    if "\n" not in stripped and _WHOLE_FILE_TODO_RE.match(stripped):
        return True
    if not SINGLE_TAG_RE.match(stripped):
        return False
    inner = stripped[1:-1]
    if any(ch.isspace() for ch in inner):
        return True
    return any(hint in stripped.casefold() for hint in PLACEHOLDER_HINTS)


def looks_like_unfilled_path(path: str) -> bool:
    """True, когда путь — заготовка, а не адрес.

    Пустая строка сюда не относится: у неё свой отказ, и два диагноза на одну
    неисправность мешают читателю понять, который сработал.
    """
    raw = (path or "").strip()
    if not raw:
        return False
    if _PATH_TEMPLATE_RE.search(raw):
        return True
    lowered = raw.casefold()
    if any(mark in lowered for mark in _PATH_PLACEHOLDER_MARKS):
        return True
    # Посегментно: «path/to/your/file.py» выдаёт себя сегментом `to` рядом с
    # `your`, а `docs/path/tools.md` — нет, потому что таких сегментов у него
    # нет ни одного.
    segments = [s for s in re.split(r"[\\/]+", lowered) if s]
    stems = {s.split(".")[0] for s in segments}
    return len(stems & _PATH_PLACEHOLDER_SEGMENTS) >= 2
