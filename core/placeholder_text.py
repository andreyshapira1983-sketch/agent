"""A template where an address or content should stand (unfilled placeholder detection)."""
from __future__ import annotations

import os
import re

#: A lone angle-bracket stub as a whole: `<to be synthesized ...>`, `<insert path>`.
SINGLE_TAG_RE = re.compile(r"^<[^<>\n]+>$")

#: Words that give away an unfilled template even without inner spaces.
PLACEHOLDER_HINTS: tuple[str, ...] = (
    "to be", "tbd", "todo", "placeholder", "synthes",
    "fill in", "fill-in", "fill_me", "fillme",
    "your text", "goes here", "insert ",
)

#: Forms found IN A PATH; separate from `PLACEHOLDER_HINTS` because a path is not prose: template substitutions and conventional example names, not English words.
_PATH_TEMPLATE_RE = re.compile(
    r"<[^<>/\\]*>"          # <identified_file>, <insert path>
    r"|\{\{[^}]*\}\}"        # {{module}}
    r"|\$\{[^}]*\}"          # ${MODULE}
    r"|(?<![A-Za-z])XXX(?![A-Za-z])"  # test_XXX.py: underscore is not a word boundary
    , re.IGNORECASE,
)

#: Name pieces a person uses for "fill in here"; matched by path segment and name stem, not by substring (`path/to` would hit the legitimate `docs/path/tools.md`).
_PATH_PLACEHOLDER_SEGMENTS: frozenset[str] = frozenset({
    "to", "your", "yourfile", "yourpath", "example", "somefile", "filename",
})
_PATH_PLACEHOLDER_MARKS: tuple[str, ...] = (
    "your_file", "your-file", "your_path", "your-path", "file_path_here",
    "path_here", "fill_me", "fillme", "replace_me", "replaceme",
    "todo_", "_todo", "tbd", "placeholder",
)


#: Unfinished-work marker AT THE START of SINGLE-line content only: a file that is one "TODO: ..." line is a stub, a `# TODO` first line of a normal file is a legitimate comment.
_WHOLE_FILE_TODO_RE = re.compile(r"^(?:TODO|FIXME|XXX|HACK)\b\s*[:\-—]", re.IGNORECASE)


#: The model's own stub dialect: one-line prose with a marker word, a `# placeholder` comment, a multi-line body of only bare pass / assert False / "...". Real code is told by parsing: prose does not parse, a stub parses to a bare statement, and marker-free comment documentation is legitimate.
_STUB_MARKER_WORDS: tuple[str, ...] = (
    "placeholder", "will be filled", "will be replaced", "write me",
    "to be", "tbd", "todo", "fixme", "stub", "fill in", "fill-in", "insert ",
)


#: Guard v2: the common sign is "no real body", decided by parsing, not by a word list; covers a bare marker token, `def f(): pass`, `raise NotImplementedError`, a docstring-only stub.
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
    """True when the function body is a stub; @abstractmethod is authoritative (any body is a legitimate interface declaration)."""
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
    """True when the tree has at least one executable statement; comments are invisible in the AST, definitions recurse (a file of real functions is live code)."""
    import ast
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # A stub body does not make a file live; an abstract method is not a stub and is live.
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
    """True when the tree has definitions and ALL of them are stubs."""
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
    """True when the docstring of the module or any definition carries a placeholder marker."""
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
    """Bare stub: the only statement is pass, "..." or assert False (any message)."""
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
    """True when the whole content is one unfilled stub; contract: content: str -> bool (live caller tools/file_write passes the whole string)."""
    import ast
    stripped = (content or "").strip()
    if not stripped:
        return False
    # v2 law: a bare marker token as a whole (PLACEHOLDER, TODO_LATER, "...").
    if stripped.casefold().replace(" ", "") in _STUB_TOKENS or stripped == "...":
        return True
    # Old law: a one-line TODO:/FIXME:/XXX:/HACK: is a stub.
    if "\n" not in stripped and _WHOLE_FILE_TODO_RE.match(stripped):
        return True
    # Old law: a whole `<tag>`.
    if SINGLE_TAG_RE.match(stripped):
        inner = stripped[1:-1]
        if any(ch.isspace() for ch in inner):
            return True
        return any(hint in stripped.casefold() for hint in PLACEHOLDER_HINTS)
    # v2 law: structural parse, "is there a real body"; comments are invisible in the AST so pure documentation falls to the old marker branch below.
    try:
        tree = ast.parse(content)
    except SyntaxError:
        tree = None
    if tree is not None:
        if _docstring_has_marker(tree):
            return True
        if not _has_executable_statement(tree) and _all_defs_are_stubs(tree):
            return True
        # A module whose only content is a bare stub is a stub even without comments next to it.
        if _is_bare_statement_tree(tree):
            return True
    # New law: parsing decides.
    lines = [line.strip() for line in stripped.splitlines() if line.strip()]
    if not lines:
        return False
    if len(lines) == 1 and not lines[0].startswith("#"):
        # Prose with a marker word is a stub only if it does not parse as Python (`x = fill_rate(1)` parses and is legitimate).
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
        # Only comments: marker comments are a stub, documentation is legitimate.
        return has_marker_comment
    # Code present: a stub only if ALL of it is bare stubs.
    for line in code_lines:
        try:
            tree = ast.parse(line)
        except SyntaxError:
            return False
        if not _is_bare_statement_tree(tree):
            return False
    return True


def looks_like_unfilled_path(path: str, base_dir: str | None = None) -> bool:
    """True when the path is a placeholder, not an address; an empty string has its own refusal. With ``base_dir`` (workspace root) a path that exists on disk is a real address and returns False even with template-like name parts (a real module was condemned by name substring)."""
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
    # Per segment: "path/to/your/file.py" gives itself away by a `to` segment beside `your`, `docs/path/tools.md` has none.
    segments = [s for s in re.split(r"[\\/]+", lowered) if s]
    stems = {s.split(".")[0] for s in segments}
    return len(stems & _PATH_PLACEHOLDER_SEGMENTS) >= 2
