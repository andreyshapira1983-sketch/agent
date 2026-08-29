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


#: --- Родной диалект заглушек самой модели (авторство агента, 2026-08-29) -----
#: Пойман на четырёх живых промахах: прозаический однострочник со словом-маркером
#: («PLACEHOLDER — will be filled by executor»), комментарий «# placeholder …»,
#: многострочник, чьи единственные операторы — голые pass / assert False / «...».
#: Настоящий код определяется разбором синтаксиса, не решёткой: проза не
#: парсится, стаб парсится в голый оператор, а документация — комментарии без
#: маркеров — законна (решение агента, закреплено его же свидетелем).
_STUB_MARKER_WORDS: tuple[str, ...] = (
    "placeholder", "will be filled", "will be replaced", "write me",
    "to be", "tbd", "todo", "fixme", "stub", "fill in", "fill-in", "insert ",
)


def _is_bare_statement_tree(tree) -> bool:
    """Голый стаб: единственный оператор pass, «...» или assert False (с любым сообщением)."""
    import ast
    if len(tree.body) != 1:
        return False
    node = tree.body[0]
    if isinstance(node, ast.Pass):
        return True
    if (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and node.value.value is Ellipsis
    ):
        return True
    return (
        isinstance(node, ast.Assert)
        and isinstance(node.test, ast.Constant)
        and node.test.value is False
    )


def looks_like_unfilled_content(content: str) -> bool:
    """True, когда всё содержимое — одна незаполненная заготовка.

    Контракт органа: content: str -> bool (живой вызывающий — tools/file_write —
    передаёт строку целиком). Старый закон сохранён дословно; новый закон —
    авторство агента, собран курьером из его v5 и двух его словесных правок.
    """
    import ast
    stripped = (content or "").strip()
    # Старый закон, дословно: однострочный TODO:/FIXME:/XXX:/HACK: — заготовка.
    if "\n" not in stripped and _WHOLE_FILE_TODO_RE.match(stripped):
        return True
    # Старый закон, дословно: «<тег>» целиком.
    if SINGLE_TAG_RE.match(stripped):
        inner = stripped[1:-1]
        if any(ch.isspace() for ch in inner):
            return True
        return any(hint in stripped.casefold() for hint in PLACEHOLDER_HINTS)
    # Новый закон: решает разбор синтаксиса.
    lines = [line.strip() for line in stripped.splitlines() if line.strip()]
    if not lines:
        return False
    if len(lines) == 1 and not lines[0].startswith("#"):
        # Проза со словом-маркером — заглушка, ТОЛЬКО если не парсится как
        # Python: «x = fill_rate(1)» парсится и законен (граница — его решение).
        lowered = lines[0].casefold()
        if any(word in lowered for word in _STUB_MARKER_WORDS):
            try:
                ast.parse(lines[0])
            except SyntaxError:
                return True
        return False
    code_lines = [line for line in lines if not line.startswith("#")]
    comment_text = " ".join(
        line for line in lines if line.startswith("#")
    ).casefold()
    has_marker_comment = any(word in comment_text for word in _STUB_MARKER_WORDS)
    if not code_lines:
        # Одни комментарии: маркерные — заглушка, документация — законна.
        return has_marker_comment
    # Код есть: заглушка, только если ВЕСЬ он — голые стабы.
    for line in code_lines:
        try:
            tree = ast.parse(line)
        except SyntaxError:
            return False
        if not _is_bare_statement_tree(tree):
            return False
    return True


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
