"""Шаблон там, где должен стоять адрес или содержимое.

Планировщик иногда выдаёт шаг ДО того, как подставил в него конкретику, и в
аргументе остаётся заготовка. Для содержимого это ловили с 2026-08-04
(`tools/file_write._looks_like_unfilled_placeholder`), для ПУТИ не ловил никто.

Три случая за один день, по одному на инструмент, класс один:

    file_write  содержимое  <updated content for core/loop.py with experience…>
    file_write  путь        your_file_path_here.txt
    file_read   путь        core/<identified_file>.py

Третий — самый дорогой. Агент получил список `core/`, где `loop_attempt.py`
лежит, и следующим шагом прочитал заготовку, после чего честно доложил, что
файла нет. Стена встала ровно между OBSERVED и EXPLAINED: расследуя сигнал, он
не смог дойти до кода, который этот сигнал рождает.

Почему проверка отдельная, а не «файл не найден»: несуществующий путь и
незаполненный шаблон — разные диагнозы. Первый бывает опечаткой или переездом
файла, второй означает, что план не был достроен, и лечится он не поиском
файла, а возвратом к планированию. Слить их значило бы отправить агента искать
`<identified_file>.py` в репозитории.

Зачем и как проверялось: docs/CODE_NOTES.md, «A path is an address, not a
template».
"""
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


def looks_like_unfilled_content(content: str) -> bool:
    """True, когда всё содержимое — одна незаполненная заготовка."""
    stripped = (content or "").strip()
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
