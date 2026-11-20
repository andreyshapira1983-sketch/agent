"""Map a command used as a goal subject (`:team-run`) to the module holding its handler, parsed from the dispatcher source."""
from __future__ import annotations

import re
from pathlib import Path

#: Handler search roots; convention: `:team-run` is served by `_handle_team_run`, defined in `cli/` or `app/`.
_HANDLER_DIRS: tuple[str, ...] = ("cli", "app")
_DISPATCH_REL = "cli/command_dispatch.py"

_TOKEN_RE = re.compile(r'":([a-z][a-z0-9-]*)"')
_HEAD_TEST_RE = re.compile(r"^\s*if head (?:==|in)\s")
_CALL_RE = re.compile(r"\b(_handle_\w+)\(")
_DEF_RE = re.compile(r"^def (_handle_\w+)\(", re.MULTILINE)

_CACHE: dict[str, dict[str, str]] = {}


def _handler_modules(root: Path) -> dict[str, str]:
    """`_handle_<key>` -> relative path of the module that defines it."""
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
        # A handler is called within the nearest lines of its branch; further down is the next check.
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
    """Module responsible for this command, or None."""
    name = str(token or "").strip().strip("`'\",.;()[]")
    if not name.startswith(":"):
        return None
    key = str(Path(root).resolve())
    table = _CACHE.get(key)
    if table is None:
        table = _build(Path(root))
        _CACHE[key] = table
    return table.get(name)
