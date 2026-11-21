"""Report fields added to a structure but never read, and functions never mentioned at all."""
from __future__ import annotations

import ast
import re
import sys
from collections import defaultdict
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_DIRS = ("core", "tools", "cli", "app", "api")
_ENTRY = ("agent_tick.py", "main.py")
_READ_CALLS = {"get", "pop", "setdefault"}
#: Our fields are snake_case; camelCase, header and env-var names are read outside the process.
_OUR_FIELD = re.compile(r"^[a-z][a-z0-9_]*$")


#: Searched for mentions, not definitions: maintenance scripts call code for real too.
_CALLER_DIRS = ("scripts", "tests")


def _sources(*, tests: bool) -> list[Path]:
    dirs = _CALLER_DIRS if tests else _DIRS
    out = [p for d in dirs for p in (_ROOT / d).rglob("*.py")
           if "__pycache__" not in str(p)]
    if not tests:
        out += [_ROOT / name for name in _ENTRY]
    return [p for p in out if p.is_file()]


def _trees(paths: list[Path]):
    for path in paths:
        try:
            yield path, ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue


def _collect(paths: list[Path]) -> tuple[dict, dict]:
    """(added keys -> sites, read names -> sites)."""
    added: dict[str, set[str]] = defaultdict(set)
    read: dict[str, set[str]] = defaultdict(set)
    for path, tree in _trees(paths):
        rel = path.relative_to(_ROOT).as_posix()
        for node in ast.walk(tree):
            site = f"{rel}:{getattr(node, 'lineno', 0)}"
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if (isinstance(target, ast.Subscript)
                            and isinstance(target.slice, ast.Constant)
                            and isinstance(target.slice.value, str)):
                        added[target.slice.value].add(site)
            elif (isinstance(node, ast.Subscript)
                  and isinstance(node.ctx, ast.Load)
                  and isinstance(node.slice, ast.Constant)
                  and isinstance(node.slice.value, str)):
                read[node.slice.value].add(site)
            elif isinstance(node, ast.Call):
                func = node.func
                if (isinstance(func, ast.Attribute) and func.attr in _READ_CALLS
                        and node.args and isinstance(node.args[0], ast.Constant)
                        and isinstance(node.args[0].value, str)):
                    read[node.args[0].value].add(site)
            elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
                # Written by key, read by attribute: d["k"] = ... and obj.k are one field.
                read[node.attr].add(site)
            elif isinstance(node, ast.Compare):
                # Comparing with the name is a read too: drive == "novelty_need".
                for part in (node.left, *node.comparators):
                    if isinstance(part, ast.Constant) and isinstance(part.value, str):
                        read[part.value].add(site)
            elif isinstance(node, (ast.Set, ast.List, ast.Tuple)):
                for elt in node.elts:
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                        read[elt.value].add(site)
    return added, read


def _mentions(paths: list[Path]) -> set[str]:
    """Everything named at all: names, attributes, string constants."""
    seen: set[str] = set()
    for _path, tree in _trees(paths):
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                seen.add(node.id)
            elif isinstance(node, ast.Attribute):
                seen.add(node.attr)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                seen.add(node.value)
    return seen


def main() -> int:
    production = _sources(tests=False)
    tests = _sources(tests=True)
    added, read = _collect(production)
    _added_t, read_t = _collect(tests)

    dead = {k: v for k, v in added.items()
            if k not in read and _OUR_FIELD.match(k)}
    only_tests = {k for k in dead if k in read_t}  # read only by tests
    print(f"ПОЛЯ, дописанные в структуру: {len(added)}")
    print(f"  не читает никто в работе: {len(dead)}"
          f" (из них читают только тесты: {len(only_tests)})")
    for key, sites in sorted(dead.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        mark = " [только тест]" if key in only_tests else ""
        print(f"  {len(sites):2d}x  {key:<32}{min(sites)}{mark}")

    defined: dict[str, str] = {}
    for path, tree in _trees(production):
        rel = path.relative_to(_ROOT).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            # A decorated function is called by its decorator (e.g. pydantic validators).
            if node.decorator_list:
                continue
            defined[node.name] = f"{rel}:{node.lineno}"
    named = _mentions(production) | _mentions(tests)
    orphan = {n: s for n, s in defined.items()
              if n not in named and not n.startswith("__")}
    print(f"\nФУНКЦИИ, определённые и ни разу не упомянутые: {len(orphan)}"
          f" из {len(defined)}")
    for name, site in sorted(orphan.items(), key=lambda kv: kv[1]):
        print(f"      {name:<42}{site}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
