"""Memory authority map: which decision each data/*.jsonl store is able to change.

Run from the repository root: python scripts/memory_authority_map.py [--json].
It prints one line per store (authority, writers, readers); --json prints the full map.
"""
from __future__ import annotations

import argparse
import ast
import json
import pathlib
import re
import sys

_ROOTS = ("core", "app", "cli", "api", "tools", "scripts")
_EXTRA_FILES = ("agent_tick.py", "main.py")

#: Four levels that must not be mixed, in increasing order of power.
INERT = "inert"           # the record exists, no live reader
REASONING = "reasoning"   # content reaches a prompt: changes the answer, not a branch
GATE = "gate"             # a code branch reads the value and changes execution
ACTION = "action"         # the value permits an effect: spending, applying, acting

#: Calls that mean an effect: an irreversible or external action.
_EFFECT_CALL = re.compile(
    r"(approve|reserve|charge|spend|commit|apply_|execute|dispatch|send_|"
    r"write_file|launch|grant|activate)",
    re.IGNORECASE,
)

#: Signs that the value goes into a model prompt rather than a branch.
_PROMPT_SINK = re.compile(
    r"(prompt|system=|messages|\.complete\(|render_|_context\(|instructions)",
    re.IGNORECASE,
)


_TREE_CACHE: dict[pathlib.Path, ast.Module] = {}


def _tree(path: pathlib.Path) -> ast.Module | None:
    """Parse each file once per run; the map walks it again for every store."""
    if path not in _TREE_CACHE:
        try:
            _TREE_CACHE[path] = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            _TREE_CACHE[path] = None
    return _TREE_CACHE[path]


def _iter_py() -> list[pathlib.Path]:
    out: list[pathlib.Path] = []
    for root in _ROOTS:
        d = pathlib.Path(root)
        if d.is_dir():
            out.extend(sorted(d.rglob("*.py")))
    for name in _EXTRA_FILES:
        p = pathlib.Path(name)
        if p.is_file():
            out.append(p)
    return out


_FUNC_INDEX: list[tuple[pathlib.Path, str, str, str]] = []


def _func_index(files: list[pathlib.Path]) -> list[tuple[pathlib.Path, str, str, str]]:
    """(file, name, body source, return-values source) per function, built once per run."""
    if _FUNC_INDEX:
        return _FUNC_INDEX
    for path in files:
        tree = _tree(path)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            body = ast.unparse(node)
            rets = " ".join(
                ast.unparse(n.value)
                for n in ast.walk(node)
                if isinstance(n, ast.Return) and n.value
            )
            _FUNC_INDEX.append((path, node.name, body, rets))
    return _FUNC_INDEX


def path_aliases(store: str, files: list[pathlib.Path]) -> set[str]:
    """Names the code uses for a store besides its file name: module constants, resolvers."""
    names: set[str] = {store}
    for path in files:
        tree = _tree(path)
        if tree is None:
            continue
        # Module level only: local names such as `path` or `store` would match anything.
        for node in tree.body:
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
                if store not in ast.unparse(node.value):
                    continue
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for t in targets:
                    if not isinstance(t, ast.Name):
                        continue
                    # Only constants and private module names address a store.
                    if t.id.isupper() or t.id.startswith("_"):
                        names.add(t.id)
            # A parameter default naming the store makes each call of the function an access.
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for default in node.args.defaults + node.args.kw_defaults:
                    if default is not None and store in ast.unparse(default):
                        names.add(node.name)

    # The address spreads through resolvers (`return ws / CONST`); their callers access it.
    idx = _func_index(files)
    for _ in range(3):
        grown = False
        for _path, fname, _body, rets in idx:
            if fname in names or not rets:
                continue
            if any(re.search(rf"\b{re.escape(a)}\b", rets) for a in names):
                names.add(fname)
                grown = True
        if not grown:
            break
    return names


def owner_classes(store: str, aliases: set[str], files: list[pathlib.Path]) -> set[str]:
    """Classes constructed with the store's address; their methods reach it as self.path."""
    owners: set[str] = set()
    for path in files:
        tree = _tree(path)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else (
                func.attr if isinstance(func, ast.Attribute) else None
            )
            if not name or not name[:1].isupper():
                continue
            args_src = ast.unparse(node)
            if any(re.search(rf"\b{re.escape(a)}\b", args_src) for a in aliases):
                owners.add(name)
    return owners


def _accessors_for(store: str, files: list[pathlib.Path]) -> dict[str, set[str]]:
    """Functions naming the store, split into readers and writers by the state call inside."""
    aliases = path_aliases(store, files)
    owners = owner_classes(store, aliases, files)
    found: dict[str, set[str]] = {"read": set(), "write": set()}
    for path in files:
        tree = _tree(path)
        if tree is None:
            continue
        owned_methods: set[int] = set()
        for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
            if cls.name in owners:
                owned_methods.update(
                    id(m) for m in cls.body
                    if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))
                )
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            body = ast.unparse(node)
            named = any(re.search(rf"\b{re.escape(a)}\b", body) for a in aliases)
            if not named and id(node) not in owned_methods:
                continue
            kind = None
            if re.search(r"(append_state|write_state|_dump|save|record_|store_)", body):
                kind = "write"
            if re.search(r"(read_state|load|_iter|list_|get_|find_|fetch)", body):
                kind = "read" if kind is None else "both"
            if kind in ("read", "both"):
                found["read"].add(f"{path}:{node.name}")
            if kind in ("write", "both"):
                found["write"].add(f"{path}:{node.name}")
    return found


def _authority_of(reader_ref: str) -> tuple[str, str]:
    """A reader's authority, judged by the value's sink (strongest first), never by its name."""
    path_s, _, func = reader_ref.rpartition(":")
    tree = _tree(pathlib.Path(path_s))
    if tree is None:
        return INERT, "разбор не удался"
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func:
            body = ast.unparse(node)
            if _EFFECT_CALL.search(body):
                hit = _EFFECT_CALL.search(body)
                return ACTION, f"вызывает эффект: {hit.group(0)}"
            has_branch = any(isinstance(n, (ast.If, ast.IfExp)) for n in ast.walk(node))
            if has_branch:
                return GATE, "значение попадает в ветку исполнения"
            if _PROMPT_SINK.search(body):
                return REASONING, "уходит в промпт"
            return INERT, "прочитано и возвращено без решения"
    return INERT, "функция не найдена"


_ENTRY_POINTS = ('run_tick', 'main', 'run_autonomous_cycle', 'execute_loop')


def reachable_functions(files: list[pathlib.Path]) -> set[str]:
    """Names reachable from live entry points by call graph (by name, so an over-estimate)."""
    calls: dict[str, set[str]] = {}
    for _path, fname, body, _rets in _func_index(files):
        try:
            tree = ast.parse(body)
        except SyntaxError:
            continue
        names: set[str] = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Call):
                f = n.func
                if isinstance(f, ast.Name):
                    names.add(f.id)
                elif isinstance(f, ast.Attribute):
                    names.add(f.attr)
        calls.setdefault(fname, set()).update(names)
    seen: set[str] = set()
    queue = [e for e in _ENTRY_POINTS if e in calls]
    while queue:
        cur = queue.pop()
        if cur in seen:
            continue
        seen.add(cur)
        queue.extend(calls.get(cur, ()))
    return seen


def build_map() -> list[dict[str, object]]:
    files = _iter_py()
    stores = sorted(p.name for p in pathlib.Path("data").glob("*.jsonl"))
    rows: list[dict[str, object]] = []
    for store in stores:
        acc = _accessors_for(store, files)
        readers = sorted(acc["read"])
        levels: dict[str, str] = {}
        for r in readers:
            level, why = _authority_of(r)
            levels[r] = f"{level}: {why}"
        strongest = INERT
        for order in (ACTION, GATE, REASONING):
            if any(v.startswith(order) for v in levels.values()):
                strongest = order
                break
        live = reachable_functions(files)
        rows.append({
            "store": store,
            "live_readers": sorted(r for r in readers if r.rpartition(":")[2] in live),
            "writers": sorted(acc["write"]),
            "readers": readers,
            "reader_authority": levels,
            "authority": strongest,
        })
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true", help="выдать машинно читаемо")
    args = ap.parse_args()
    rows = build_map()
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    for r in rows:
        print(f"{r['store']:38} {r['authority']:10} "
              f"писателей {len(r['writers']):2}  читателей {len(r['readers']):2}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
