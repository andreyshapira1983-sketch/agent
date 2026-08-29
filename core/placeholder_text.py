"""Шаблон там, где должен стоять адрес или содержимое."""
from __future__ import annotations

import os
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


#: --- Сторож v2 (авторство агента, 2026-08-29, вторая волна) ------------------
#: Атака невиданными формами показала: v1 знал только четыре формы из своих же
#: тестов и пропускал семь других, включая те, что РЕАЛЬНО ложились на диск
#: вместо кода (голый токен-маркер; `def f(): pass`; `raise NotImplementedError`;
#: docstring-заглушка). Общий признак, названный агентом: «нет реального тела»,
#: и решает его разбор синтаксиса, а не список слов.
_STUB_TOKENS: frozenset[str] = frozenset({
    "placeholder",
    "placeholder_replaced_by_executor_with_full_file",
    "placeholder_filled_by_synthesizer",
    "todo",
    "todo_later",
    "fixme",
    "tbd",
    "xxx",
})


def _body_is_stub(node) -> bool:
    """True, когда тело функции — заглушка (авторство агента, часть 1).

    Декоратор @abstractmethod авторитетен: у абстрактного метода законно ЛЮБОЕ
    тело (pass / ... / docstring / raise), это объявление интерфейса, а не стаб.
    """
    import ast
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return False
    for dec in node.decorator_list:
        if isinstance(dec, ast.Name) and dec.id == "abstractmethod":
            return False
        if (
            isinstance(dec, ast.Attribute)
            and isinstance(dec.value, ast.Name)
            and dec.value.id == "abc"
            and dec.attr == "abstractmethod"
        ):
            return False
    statements = list(node.body)
    if not statements:
        return False
    if (
        isinstance(statements[0], ast.Expr)
        and isinstance(statements[0].value, ast.Constant)
        and isinstance(statements[0].value.value, str)
    ):
        statements = statements[1:]
    if len(statements) != 1:
        return False
    stmt = statements[0]
    if isinstance(stmt, ast.Pass):
        return True
    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
        return stmt.value.value is Ellipsis
    if isinstance(stmt, ast.Raise):
        exc = stmt.exc
        if isinstance(exc, ast.Call) and isinstance(exc.func, ast.Name):
            return exc.func.id == "NotImplementedError"
        return isinstance(exc, ast.Name) and exc.id == "NotImplementedError"
    return False


def _has_executable_statement(tree) -> bool:
    """True, когда в дереве есть хоть один исполняемый оператор (авторство агента).

    Комментарии в AST невидимы, поэтому файл из одних комментариев даёт пустое
    тело. Определения сами по себе не исполняемы — рекурсируем в них, а не
    пропускаем: файл из настоящих функций обязан считаться живым кодом.
    """
    import ast
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # Тело-заглушка не делает файл живым: pass/.../raise NotImplementedError
            # внутри стаба — это отсутствие работы, а не работа. Абстрактный метод
            # (@abstractmethod) _body_is_stub не считает заглушкой, и он живой.
            if not _body_is_stub(node) and _has_executable_statement(node):
                return True
            continue
        if isinstance(node, ast.ClassDef):
            if _has_executable_statement(node):
                return True
            continue
        if (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            continue
        return True
    return False


def _all_defs_are_stubs(tree) -> bool:
    """True, когда в дереве есть определения и ВСЕ они — заглушки."""
    import ast
    found = False
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            found = True
            if not _body_is_stub(node):
                return False
    return found


_DOCSTRING_STUB_MARKERS: tuple[str, ...] = (
    "will be filled in later", "to be implemented", "placeholder", "stub",
)


def _docstring_has_marker(tree) -> bool:
    """True, когда docstring модуля или любого определения несёт маркер заготовки."""
    import ast
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            doc = ast.get_docstring(node)
            if doc and any(m in doc.casefold() for m in _DOCSTRING_STUB_MARKERS):
                return True
    return False


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
    if not stripped:
        return False
    # Закон v2: голый токен-маркер целиком (PLACEHOLDER, TODO_LATER, «...»).
    if stripped.casefold().replace(" ", "") in _STUB_TOKENS or stripped == "...":
        return True
    # Старый закон, дословно: однострочный TODO:/FIXME:/XXX:/HACK: — заготовка.
    if "\n" not in stripped and _WHOLE_FILE_TODO_RE.match(stripped):
        return True
    # Старый закон, дословно: «<тег>» целиком.
    if SINGLE_TAG_RE.match(stripped):
        inner = stripped[1:-1]
        if any(ch.isspace() for ch in inner):
            return True
        return any(hint in stripped.casefold() for hint in PLACEHOLDER_HINTS)
    # Закон v2: структурный разбор — «есть ли реальное тело». Комментарии в AST
    # невидимы, поэтому чистая документация сюда не попадает (её решает старая
    # ветка ниже, по маркерам). Файл из настоящих функций — живой код.
    try:
        tree = ast.parse(content)
    except SyntaxError:
        tree = None
    if tree is not None:
        if _docstring_has_marker(tree):
            return True
        if not _has_executable_statement(tree) and _all_defs_are_stubs(tree):
            return True
        # Модуль, чьё единственное содержимое — голый стаб (pass / ... / assert
        # False), заглушка и без комментариев рядом: старая ветка ниже видела
        # такие формы только в компании комментариев (атака курьера, 2026-08-29).
        if _is_bare_statement_tree(tree):
            return True
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


def looks_like_unfilled_path(path: str, base_dir: str | None = None) -> bool:
    """True, когда путь — заготовка, а не адрес.

    Пустая строка сюда не относится: у неё свой отказ, и два диагноза на одну
    неисправность мешают читателю понять, который сработал.

    Контракт факта существования (авторство агента, 2026-08-29, груз №2):
    если передан ``base_dir`` (корень рабочей области), путь, существующий на
    диске относительно него, считается настоящим адресом и возвращает False —
    даже при шаблонных признаках в имени. Признак родился из живого отказа:
    сторож осуждал реальный ``core/placeholder_text.py`` — собственный модуль —
    за подстроку в имени, и 26 раз записал вину в память агента как его
    ошибку планирования. У настоящего адреса есть факт существования; у
    заготовки — никогда. Без ``base_dir`` решают только текстовые правила.
    """
    raw = (path or "").strip()
    if not raw:
        return False
    if base_dir is not None and os.path.exists(os.path.join(base_dir, raw)):
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
