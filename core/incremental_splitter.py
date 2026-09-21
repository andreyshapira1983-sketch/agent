"""Incremental splitter for oversized Python modules (junior-plan item #5).

The one-shot LLM splitter rewrites the whole target file in a single model
reply, which is bounded by the model's output-token ceiling -- a 4500-line
module like ``core/loop.py`` can never fit. This module removes that ceiling
by splitting *deterministically*:

1. **function mode** -- moves a dependency-closed group of top-level
functions (and the module constants only they use) into a new sibling
module; the target re-exports every moved name so all existing import paths
keep working (the hard rule learned from the verifier-split rollbacks). 2.
**mixin mode** -- when the file is dominated by one huge class (the
``AgentLoop`` case), moves self-contained methods into a mixin class in a
new sibling module and adds the mixin to the class bases. Attribute access
through ``self`` keeps working unchanged.

* moved code may reference ONLY builtins, imported names, and other moved
names -- never a name that stays behind (that would create a circular
import); * methods using ``super()``, ``global``/``nonlocal``, or name-
mangled ``__private`` attributes are never moved (their semantics are tied
to the defining class/module); * both resulting files must parse; the target
must actually shrink; every moved top-level name must remain importable from
the target.
"""
from __future__ import annotations

import ast
import builtins as _builtins_mod
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_BUILTIN_NAMES = frozenset(dir(_builtins_mod)) | {"__file__", "__name__", "__doc__"}

# Per-step budget: one extraction should stay reviewable, not become a second
# big-bang. Callers may lower it; raising it far defeats the "incremental" idea.
DEFAULT_MAX_MOVE_LINES = 400

# A class is "dominant" when it owns at least this share of the module's lines;
# then mixin mode is the only extraction that can meaningfully shrink the file.
_DOMINANT_CLASS_RATIO = 0.5


# ── data model ───────────────────────────────────────────────────────────────


@dataclass
class SplitStep:
    """One planned incremental extraction (not yet applied)."""

    mode: str  # "functions" | "mixin"
    target: str  # rel path of the module being shrunk
    new_module: str  # rel path of the new sibling module
    moved_names: list[str]  # top-level names / method names moved
    lines_moved: int
    target_content: str  # full post-image of the shrunk target
    new_content: str  # full post-image of the new module
    notes: list[str] = field(default_factory=list)


@dataclass
class SplitPlan:
    """Outcome of planning one incremental step."""

    status: str  # "planned" | "no_split"
    reason: str
    step: SplitStep | None = None


# ── shared AST helpers ───────────────────────────────────────────────────────


def _source_slice(lines: list[str], node: ast.stmt) -> str:
    """Verbatim source of a top-level node, including its decorators."""
    start = node.lineno
    for deco in getattr(node, "decorator_list", []) or []:
        start = min(start, deco.lineno)
    end = node.end_lineno or node.lineno
    return "\n".join(lines[start - 1 : end])


def _bound_names(node: ast.AST) -> set[str]:
    """Every name BOUND anywhere inside ``node`` (params, assignments, etc.)."""
    bound: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name) and isinstance(sub.ctx, (ast.Store, ast.Del)):
            bound.add(sub.id)
        elif isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(sub.name)
            if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                a = sub.args
                for arg in (
                    list(a.posonlyargs) + list(a.args) + list(a.kwonlyargs)
                ):
                    bound.add(arg.arg)
                if a.vararg:
                    bound.add(a.vararg.arg)
                if a.kwarg:
                    bound.add(a.kwarg.arg)
        elif isinstance(sub, (ast.Import, ast.ImportFrom)):
            for alias in sub.names:
                if alias.name != "*":
                    bound.add((alias.asname or alias.name).split(".")[0])
        elif (isinstance(sub, ast.ExceptHandler) and sub.name) or (isinstance(sub, ast.MatchAs) and sub.name):
            bound.add(sub.name)
    return bound


def _free_refs(node: ast.AST) -> set[str]:
    """Names LOADED inside ``node`` that are not bound within it."""
    loads = {
        sub.id
        for sub in ast.walk(node)
        if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load)
    }
    return loads - _bound_names(node)


def _uses_forbidden_scope(node: ast.AST) -> bool:
    """True for constructs whose meaning changes when code moves elsewhere."""
    for sub in ast.walk(node):
        if isinstance(sub, (ast.Global, ast.Nonlocal)):
            return True
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name) and sub.func.id == "super":
            return True
        if (
            isinstance(sub, ast.Attribute)
            and sub.attr.startswith("__")
            and not sub.attr.endswith("__")
        ):
            return True  # name mangling is bound to the defining class
    return False


def _import_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if alias.name != "*":
                    names.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, (ast.If, ast.Try)):
            for sub in ast.walk(node):
                if isinstance(sub, (ast.Import, ast.ImportFrom)):
                    for alias in sub.names:
                        if alias.name != "*":
                            names.add((alias.asname or alias.name).split(".")[0])
    return names


def _needed_import_stmts(
    tree: ast.Module, lines: list[str], refs: set[str]
) -> list[str]:
    """Original top-level import statements (verbatim) that bind any of ``refs``."""
    out: list[str] = []
    for node in tree.body:
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        bound = {
            (alias.asname or alias.name).split(".")[0]
            for alias in node.names
            if alias.name != "*"
        }
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            continue  # handled separately, must stay first
        if bound & refs:
            out.append(_source_slice(lines, node))
    return out


def _has_future_annotations(tree: ast.Module) -> bool:
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            return True
    return False


def _module_name(rel: str) -> str:
    return rel.replace("\\", "/").removesuffix(".py").replace("/", ".")


def _pick_new_module_path(
    workspace: Path, target: str, suffix: str, *, numbered: bool = True,
) -> str | None:
    """Имя нового модуля. Без нумерации — None, если такое имя уже занято.

    Нумерация (`_methods2`) остаётся за режимом примесей: там номер идёт вместе
    с уникальным именем базового класса, и порядок наслоения закреплён тестом.
    В режиме функций счётчик называл модуль вместо смысла: 20.09 `episode_tools`
    ушла в НОВЫЙ `core/smart_memory_helpers2.py`, когда `smart_memory_helpers.py`
    уже держал её прямую родню. Отменено коммитом 24fb2d4.
    """
    base = target.replace("\\", "/").removesuffix(".py")
    candidate = f"{base}_{suffix}.py"
    if not numbered:
        return None if (workspace / candidate).exists() else candidate
    n = 2
    while (workspace / candidate).exists():
        candidate = f"{base}_{suffix}{n}.py"
        n += 1
    return candidate


#: Слова имён, которые не говорят о предмете группы; по ним модуль не назвать.
_GENERIC_NAME_TOKENS = frozenset({
    "helper", "helpers", "util", "utils", "impl", "internal", "private",
    "value", "values", "text", "name", "names", "data", "item", "items",
    "result", "reason", "block", "check", "make", "build", "from", "with",
})


def _meaningful_suffix(names: list[str]) -> str:
    """Имя группы по её предмету: самое частое содержательное слово имён.

    Слово должно встретиться хотя бы дважды и хотя бы в пятой части имён
    (замер 2026-09-21 на доказанных группах живого кода: «claim» — 3 имени из
    14 в core/knowledge_pipeline.py). Нет такого слова — «helpers», как было;
    но нумерации в режиме функций больше нет (см. _pick_new_module_path).
    """
    counts: dict[str, int] = {}
    for name in names:
        words = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name).strip("_").lower().split("_")
        for tok in {w for w in words if len(w) >= 3 and w not in _GENERIC_NAME_TOKENS}:
            counts[tok] = counts.get(tok, 0) + 1
    if not counts:
        return "helpers"
    token, hits = max(sorted(counts.items()), key=lambda kv: kv[1])
    return token if hits >= 2 and hits * 5 >= len(names) else "helpers"


class _NoHome(Exception):
    """План раскола есть, а честного имени для нового модуля нет."""


def _name_of(node: ast.stmt) -> str:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return node.name
    target = node.targets[0] if isinstance(node, ast.Assign) else None
    return target.id if isinstance(target, ast.Name) else ""


def _cohesive_group(
    group: list[ast.stmt], names: set[str] | None, max_lines: int,
) -> list[ast.stmt]:
    """ОДНА связная группа из переносимого, а не всё переносимое сразу.

    `_movable_function_group` возвращает МАКСИМАЛЬНЫЙ набор всего, что можно
    унести, не задев оставшееся, — объединение независимых мелочей. 21.09 план
    для core/step_sanitizer.py вёз в один файл подставные URL, приведение int,
    окно строк, метку shell и четыре санитайзера разных инструментов: общего у
    них было только то, что они влезли в 338 строк. Здесь выбирается одна
    компонента связности (по взаимным ссылкам); с доказательством — та, что
    содержит доказанные имена, с замыканием по ссылкам, иначе — самая крупная.
    """
    by_name = {_name_of(n): n for n in group}
    adjacent: dict[str, set[str]] = {k: set() for k in by_name}
    for name, node in by_name.items():
        for ref in _free_refs(node) & by_name.keys():
            adjacent[name].add(ref)
            adjacent[ref].add(name)

    def component(seed: set[str], *, follow_users: bool) -> set[str]:
        seen, stack = set(), list(seed)
        while stack:
            cur = stack.pop()
            if cur in seen or cur not in by_name:
                continue
            seen.add(cur)
            nxt = adjacent.get(cur, set()) if follow_users else _free_refs(by_name[cur]) & by_name.keys()
            stack.extend(nxt - seen)
        return seen

    if names:
        chosen = component(set(names) & by_name.keys(), follow_users=False)
    else:
        # Член больше бюджета одного шага сам не переедет — и не должен
        # связывать группы. 21.09 в step_sanitizer всё склеивал `sanitize_step`
        # (752 строки при бюджете 400): бюджет его отрезал, и уезжали несвязанные
        # кучки, которые держались вместе только через того, кто остаётся дома.
        hubs = {n for n, node in by_name.items()
                if (node.end_lineno or node.lineno) - node.lineno + 1 > max_lines}
        for hub in hubs:
            for peer in adjacent.pop(hub, set()):
                adjacent.get(peer, set()).discard(hub)
        comps, left = [], set(by_name) - hubs
        while left:
            comp = component({min(left)}, follow_users=True) - hubs
            comps.append(comp or {min(left)})
            left -= comps[-1]
        best = max(comps, key=lambda c: (sum(
            (by_name[n].end_lineno or by_name[n].lineno) - by_name[n].lineno + 1
            for n in c), sorted(c))) if comps else set()
        chosen = component(best, follow_users=False) if best else set()
    return [n for n in group if _name_of(n) in chosen]


_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _names_used_outside_imports(tree: ast.Module, src: str) -> set[str]:
    """Every name the module body refers to, imports themselves excluded.

    Quoted annotations and ``__all__`` entries are strings, so string
    constants are scanned by word too — ruff counts those as uses, and a
    pruned import that a string still names would turn one finding into
    another."""
    used: set[str] = set()
    # A docstring or a bare statement-level string is prose, not a use.
    prose = {
        id(n.value) for n in ast.walk(tree)
        if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)
    }
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.Name):
                used.add(sub.id)
            elif (
                isinstance(sub, ast.Constant) and isinstance(sub.value, str)
                and id(sub) not in prose
            ):
                used.update(_WORD_RE.findall(sub.value))
    return used


def prune_orphaned_imports(src: str) -> str:
    """Drop top-level imports whose every bound name the module no longer
    uses, and narrow those partly used — deterministic, AST-driven.

    Measured 2026-09-04 (lane, ain_8e92…, third rollback of one split): the
    slice carried the moved functions' imports into the new module and left
    them in the target too — 12 unused imports, and the lint-debt guard
    (`tests/test_ruff_config.py`, a repo-wide count) rolled the apply back.
    ``__future__`` imports and lines carrying ``noqa`` (re-exports) stay.
    Unparseable input is returned unchanged: this is a tidy-up, never a
    gate."""
    try:
        tree = ast.parse(src)
    except (SyntaxError, ValueError):
        return src
    used = _names_used_outside_imports(tree, src)
    lines = src.split("\n")
    drop: set[int] = set()
    replace: dict[int, str] = {}
    for node in tree.body:
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            continue
        first, last = node.lineno, node.end_lineno or node.lineno
        if any("noqa" in lines[i - 1] for i in range(first, last + 1)):
            continue
        kept = [
            alias for alias in node.names
            if alias.name == "*" or (alias.asname or alias.name).partition(".")[0] in used
        ]
        if len(kept) == len(node.names):
            continue
        drop.update(range(first, last + 1))
        if kept:
            node.names = kept
            replace[first] = ast.unparse(node)
    if not drop:
        return src
    out: list[str] = []
    for i, line in enumerate(lines, start=1):
        if i in replace:
            out.append(replace[i])
        elif i in drop:
            continue
        else:
            out.append(line)
    return "\n".join(out)


def _lint_fix_imports(content: str, rel: str, workspace: Path) -> str:
    """``ruff check --fix`` for the two import rules (I001 sort/format, F401
    unused), fed through stdin under the workspace's own ruff config — the
    exact judge the lint-debt guard applies later. Any failure (no ruff, a
    non-zero exit other than «fixed», empty output) returns the text as it
    came: a tidy-up, never a gate."""
    import shutil
    import subprocess  # nosec B404 — fixed argv, our own workspace

    if shutil.which("ruff") is None:
        return content
    try:
        result = subprocess.run(  # noqa: S603 — literal argv, no shell; nosec B603 B607
            ["ruff", "check", "--fix", "--quiet", "--select", "I001,F401",  # noqa: S607
             "--stdin-filename", rel, "-"],
            input=content, capture_output=True, text=True, encoding="utf-8",
            cwd=str(workspace), check=False, timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return content
    fixed = result.stdout
    if result.returncode not in (0, 1) or not fixed.strip():
        return content
    try:
        ast.parse(fixed)
    except (SyntaxError, ValueError):
        return content
    return fixed


def _sorted_import_slot(tree: ast.Module, module: str) -> int:
    """1-based line AFTER which ``from <module> import …`` sits in the
    isort order ruff's I001 expects among the first-party ``from`` imports:
    before the first such import whose module name sorts later, else after
    the last import (`_last_import_end`). Measured 2026-09-04: the re-export
    appended after the block was «un-sorted» — one more finding per split."""
    head = module.partition(".")[0]
    top_imports = [
        n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))
    ]
    for node in top_imports:
        if (
            isinstance(node, ast.ImportFrom) and node.module
            and node.module.split(".")[0] == head and node.module > module
        ):
            return node.lineno - 1
    return _last_import_end(tree)


def _last_import_end(tree: ast.Module) -> int:
    """1-based line after which a re-export/import can safely be inserted.

    Prefers the end of the last top-level import; falls back to the module
    docstring; 0 means "insert before the first line".
    """
    last = 0
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            last = max(last, node.end_lineno or node.lineno)
    if last == 0 and tree.body:
        first = tree.body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)  # module docstring
        ):
            last = first.end_lineno or first.lineno
    return last


# ── function mode ────────────────────────────────────────────────────────────


def _movable_function_group(
    tree: ast.Module, dominant_class: ast.ClassDef | None
) -> list[ast.stmt]:
    """Maximal set of top-level defs movable without referencing left-behind names.

    Iteratively removes any candidate whose free references include a
    module-level name that is neither imported nor part of the candidate set.
    The fixed point is safe by construction: moved code only needs builtins,
    imports (replicated in the new module), and other moved names.
    """
    imports = _import_names(tree)
    # Names anyone in the module MUTATES via ``global`` must stay put: moving
    # the constant would silently split it from its writers.
    global_written: set[str] = set()
    for sub in ast.walk(tree):
        if isinstance(sub, ast.Global):
            global_written.update(sub.names)
    candidates: dict[str, ast.stmt] = {}
    for node in tree.body:
        if node is dominant_class:
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not _uses_forbidden_scope(node):
                candidates[node.name] = node
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            tgt = node.targets[0]
            if (
                isinstance(tgt, ast.Name)
                and tgt.id not in global_written
                and not _uses_forbidden_scope(node)
            ):
                candidates[tgt.id] = node

    changed = True
    while changed and candidates:
        changed = False
        for name in list(candidates):
            refs = _free_refs(candidates[name])
            external = refs - _BUILTIN_NAMES - imports - set(candidates)
            if external:
                # references a name that stays behind (module-level or unknown)
                del candidates[name]
                changed = True
    return sorted(candidates.values(), key=lambda n: n.lineno)


def _trim_to_budget(
    group: list[ast.stmt], lines: list[str], max_lines: int
) -> list[ast.stmt]:
    """Drop trailing members (reverse-dependency-safe) until under budget.

    A member may only be dropped if no kept member references it; dropping from
    the "most-depended-upon-last" end keeps the moved set closed. We iterate:
    remove any member nobody else in the set references, largest first, until
    the total fits.
    """

    def total(nodes: list[ast.stmt]) -> int:
        return sum(
            (n.end_lineno or n.lineno) - n.lineno + 1 for n in nodes
        )

    def name_of(node: ast.stmt) -> str:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return node.name
        assert isinstance(node, ast.Assign)  # noqa: S101 — type narrowing, guarded above
        tgt = node.targets[0]
        assert isinstance(tgt, ast.Name)  # noqa: S101 — type narrowing, guarded above
        return tgt.id

    kept = list(group)
    while kept and total(kept) > max_lines:
        removable = []
        for node in kept:
            others = [o for o in kept if o is not node]
            still_needed = any(name_of(node) in _free_refs(o) for o in others)
            if not still_needed:
                removable.append(node)
        if not removable:
            return []  # tightly coupled block larger than budget: skip this run
        removable.sort(
            key=lambda n: (n.end_lineno or n.lineno) - n.lineno + 1, reverse=True
        )
        kept.remove(removable[0])
    return kept


def _plan_function_split(
    workspace: Path,
    target: str,
    src: str,
    tree: ast.Module,
    dominant_class: ast.ClassDef | None,
    max_lines: int,
    names: set[str] | None = None,
) -> SplitStep | None:
    lines = src.split("\n")
    group = _cohesive_group(_movable_function_group(tree, dominant_class), names, max_lines)
    group = [
        n
        for n in group
        if not (isinstance(n, ast.Assign) and len(group) == 1)
    ]  # never move a lone constant; pointless churn
    group = _trim_to_budget(group, lines, max_lines)
    funcs = [
        n for n in group if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    if not funcs:
        return None

    moved_names: list[str] = []
    for node in group:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            moved_names.append(node.name)
        elif isinstance(node, ast.Assign):
            tgt = node.targets[0]
            assert isinstance(tgt, ast.Name)  # noqa: S101 — type narrowing, guarded above
            moved_names.append(tgt.id)

    refs: set[str] = set()
    for node in group:
        refs |= _free_refs(node)
    refs -= set(moved_names)
    import_stmts = _needed_import_stmts(tree, lines, refs)

    suffix = _meaningful_suffix(moved_names)
    new_rel = _pick_new_module_path(workspace, target, suffix, numbered=False)
    if new_rel is None:
        raise _NoHome(
            f"{target.removesuffix('.py')}_{suffix}.py already exists: decide whether "
            f"{', '.join(sorted(moved_names)[:6])} belong there or elsewhere — a numbered "
            "copy of the name is not a home"
        )
    header = [
        f'"""Helpers extracted verbatim from ``{target}`` by the incremental',
        "splitter. The original module re-exports every name below, so all",
        'existing import paths keep working."""',
    ]
    parts: list[str] = ["\n".join(header)]
    if _has_future_annotations(tree):
        parts.append("from __future__ import annotations")
    if import_stmts:
        parts.append("\n".join(import_stmts))
    for node in group:
        parts.append(_source_slice(lines, node))
    new_content = "\n\n".join(parts) + "\n"

    # Shrink the target: delete moved line ranges, insert one re-export line.
    drop: set[int] = set()
    for node in group:
        start = node.lineno
        for deco in getattr(node, "decorator_list", []) or []:
            start = min(start, deco.lineno)
        drop.update(range(start, (node.end_lineno or node.lineno) + 1))
    insert_after = _sorted_import_slot(tree, _module_name(new_rel))
    reexport = (
        f"from {_module_name(new_rel)} import (  # noqa: F401 -- re-exported\n    "
        + ",\n    ".join(sorted(moved_names))
        + ",\n)"
    )
    out: list[str] = []
    if insert_after == 0:
        out.append(reexport)
    for i, line in enumerate(lines, start=1):
        if i in drop:
            continue
        out.append(line)
        if i == insert_after:
            out.append(reexport)
    target_content = _collapse_blank_runs(prune_orphaned_imports("\n".join(out)))
    if not target_content.endswith("\n"):
        target_content += "\n"

    lines_moved = sum(
        (n.end_lineno or n.lineno) - n.lineno + 1 for n in group
    )
    return SplitStep(
        mode="functions",
        target=target,
        new_module=new_rel,
        moved_names=sorted(moved_names),
        lines_moved=lines_moved,
        target_content=target_content,
        new_content=new_content,
        notes=[f"moved {len(funcs)} function(s) + {len(group) - len(funcs)} constant(s)"],
    )


# ── mixin mode ───────────────────────────────────────────────────────────────


def _dominant_class(tree: ast.Module, total_lines: int) -> ast.ClassDef | None:
    best: ast.ClassDef | None = None
    best_span = 0
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            span = (node.end_lineno or node.lineno) - node.lineno + 1
            if span > best_span:
                best, best_span = node, span
    if best is not None and best_span >= total_lines * _DOMINANT_CLASS_RATIO:
        return best
    return None


def _movable_methods(
    cls: ast.ClassDef, tree: ast.Module, max_lines: int
) -> list[ast.stmt]:
    """Self-contained methods safe to relocate into a mixin, under budget.

    A method moves only when its free names are builtins or module imports
    (which the mixin module replicates). ``self.<attr>`` access is dynamic and
    keeps working; ``super()``, mangling and module-global writes disqualify.
    """
    imports = _import_names(tree)
    picked: list[ast.stmt] = []
    used = 0
    for node in cls.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name == "__init__":
            continue  # keep construction in the primary class for readability
        if _uses_forbidden_scope(node):
            continue
        refs = _free_refs(node)
        if refs - _BUILTIN_NAMES - imports:
            continue  # touches module-level names staying behind
        start = node.lineno
        for deco in node.decorator_list:
            start = min(start, deco.lineno)
        span = (node.end_lineno or node.lineno) - start + 1
        if used + span > max_lines:
            continue
        picked.append(node)
        used += span
    return picked


def _plan_mixin_split(
    workspace: Path,
    target: str,
    src: str,
    tree: ast.Module,
    cls: ast.ClassDef,
    max_lines: int,
) -> SplitStep | None:
    lines = src.split("\n")
    methods = _movable_methods(cls, tree, max_lines)
    if not methods:
        return None

    refs: set[str] = set()
    for node in methods:
        refs |= _free_refs(node)
    import_stmts = _needed_import_stmts(tree, lines, refs)

    new_rel = _pick_new_module_path(workspace, target, "methods")
    mixin_name = f"{cls.name}ExtractedMethods"
    n = 2
    existing_names = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }
    existing_names |= _import_names(tree)
    while mixin_name in existing_names:
        mixin_name = f"{cls.name}ExtractedMethods{n}"
        n += 1

    header = [
        f'"""Methods extracted verbatim from ``{cls.name}`` in ``{target}`` by the',
        "incremental splitter. The class inherits this mixin, so behaviour and the",
        'public surface are unchanged."""',
    ]
    parts: list[str] = ["\n".join(header)]
    if _has_future_annotations(tree):
        parts.append("from __future__ import annotations")
    if import_stmts:
        parts.append("\n".join(import_stmts))
    body_chunks = [_source_slice(lines, m) for m in methods]
    mixin_src = f"class {mixin_name}:\n" + "\n\n".join(body_chunks)
    parts.append(mixin_src)
    new_content = "\n\n".join(parts) + "\n"

    # Rewrite the target: import the mixin, add it to the bases, drop the methods.
    drop: set[int] = set()
    for node in methods:
        start = node.lineno
        for deco in node.decorator_list:
            start = min(start, deco.lineno)
        drop.update(range(start, (node.end_lineno or node.lineno) + 1))

    class_header_line = cls.lineno  # line with ``class Name(...):``
    header_src = lines[class_header_line - 1]
    if cls.bases or cls.keywords:
        new_header = header_src.replace("(", f"({mixin_name}, ", 1)
    else:
        new_header = header_src.replace(
            f"class {cls.name}", f"class {cls.name}({mixin_name})", 1
        )
    insert_after = _sorted_import_slot(tree, _module_name(new_rel))
    mixin_import = f"from {_module_name(new_rel)} import {mixin_name}"

    out: list[str] = []
    if insert_after == 0:
        out.append(mixin_import)
    for i, line in enumerate(lines, start=1):
        if i in drop:
            continue
        if i == class_header_line:
            out.append(new_header)
        else:
            out.append(line)
        if i == insert_after:
            out.append(mixin_import)
    target_content = _collapse_blank_runs(prune_orphaned_imports("\n".join(out)))
    if not target_content.endswith("\n"):
        target_content += "\n"

    lines_moved = sum(
        (m.end_lineno or m.lineno) - m.lineno + 1 for m in methods
    )
    return SplitStep(
        mode="mixin",
        target=target,
        new_module=new_rel,
        moved_names=sorted(m.name for m in methods),
        lines_moved=lines_moved,
        target_content=target_content,
        new_content=new_content,
        notes=[f"moved {len(methods)} method(s) of {cls.name} into mixin {mixin_name}"],
    )


def _collapse_blank_runs(text: str) -> str:
    """Collapse 3+ consecutive blank lines (left behind by deletions) to 2."""
    out: list[str] = []
    blanks = 0
    for line in text.split("\n"):
        if line.strip() == "":
            blanks += 1
            if blanks > 2:
                continue
        else:
            blanks = 0
        out.append(line)
    return "\n".join(out)


# ── dedup mode ───────────────────────────────────────────────────────────────


def _dedup_step(
    workspace: Path, target: str, src: str, tree: ast.Module, proof: Any,
) -> SplitStep:
    """Свести дубль: убрать копию из `target`, взять её импортом у `proof.other`.

    Правка, которую следует из доказательства «дубль», — не раскол: второй
    модуль уже держит то же тело, рождать третий файл незачем. Копия
    выбрасывается, только если тело в `other` по-прежнему то же (по AST, как
    его сравнил core/split_proof.py); имя другое — берётся с псевдонимом.
    """
    from core.split_proof import _shape

    other = str(getattr(proof, "other", "") or "")
    try:
        other_tree = ast.parse((workspace / other).read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError) as exc:
        raise _NoHome(f"dedup: the kept copy {other!r} is unreadable: {exc}") from exc
    kept = {_shape(n): n.name for n in other_tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
    wanted = set(getattr(proof, "names", ()) or ())
    drops = [n for n in tree.body
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
             and n.name in wanted and _shape(n) in kept]
    if not drops:
        raise _NoHome(f"dedup: no copy in {target} still matches {other}")
    aliases = sorted(
        kept[_shape(n)] if kept[_shape(n)] == n.name else f"{kept[_shape(n)]} as {n.name}"
        for n in drops)
    lines = src.split("\n")
    drop: set[int] = set()
    for node in drops:
        start = min([node.lineno, *(d.lineno for d in node.decorator_list)])
        drop.update(range(start, (node.end_lineno or node.lineno) + 1))
    module = _module_name(other)
    line = (f"from {module} import (  # noqa: F401 -- deduplicated, the kept copy\n    "
            + ",\n    ".join(aliases) + ",\n)")
    slot = _sorted_import_slot(tree, module)
    out = [line] if slot == 0 else []
    for i, text in enumerate(lines, start=1):
        if i in drop:
            continue
        out.append(text)
        if i == slot:
            out.append(line)
    content = _collapse_blank_runs(prune_orphaned_imports("\n".join(out)))
    return SplitStep(
        mode="dedup", target=target, new_module=other,
        moved_names=sorted(n.name for n in drops),
        lines_moved=sum((n.end_lineno or n.lineno) - n.lineno + 1 for n in drops),
        target_content=content if content.endswith("\n") else content + "\n",
        new_content="",
        notes=[f"dropped {len(drops)} duplicate(s); the kept copy lives in {other}"],
    )


# ── public API ───────────────────────────────────────────────────────────────


def plan_incremental_split(
    workspace: str | Path,
    target: str,
    *,
    max_move_lines: int = DEFAULT_MAX_MOVE_LINES,
    proof: Any | None = None,
) -> SplitPlan:
    """Plan ONE deterministic extraction step for an oversized module.

    Pure planning: reads the target, computes verbatim post-images for the
    shrunk target and the new sibling module, and validates them. Never writes
    a file; the caller routes the step through the approval inbox + self-apply
    lane, which runs tests and auto-rolls back on red.
    """
    ws = Path(workspace)
    rel = str(target or "").replace("\\", "/").strip()
    if not rel.endswith(".py"):
        return SplitPlan("no_split", f"target {rel!r} is not a Python module")
    path = ws / rel
    if not path.is_file():
        return SplitPlan("no_split", f"target {rel!r} does not exist")
    try:
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src)
    except (OSError, SyntaxError) as exc:
        return SplitPlan("no_split", f"cannot parse {rel!r}: {exc}")

    total_lines = src.count("\n") + 1
    dominant = _dominant_class(tree, total_lines)

    # Доказательство правки (core/split_proof.py) решает, ЧТО делать: дубль
    # сводится импортом, два предмета — выносится доказанная группа.
    try:
        if getattr(proof, "kind", None) == "dup":
            step = _dedup_step(ws, rel, src, tree, proof)
        else:
            names = set(getattr(proof, "names", ()) or ()) or None
            step = _plan_function_split(ws, rel, src, tree, dominant, max_move_lines, names)
    except _NoHome as exc:
        return SplitPlan("no_split", str(exc))
    if step is None and dominant is not None:
        step = _plan_mixin_split(ws, rel, src, tree, dominant, max_move_lines)
    if step is None:
        return SplitPlan(
            "no_split",
            f"no self-contained block under {max_move_lines} lines found in "
            f"{rel!r} (every candidate references names that must stay)",
        )

    # The same linter the lane's guard runs, on the same two files, BEFORE the
    # self-checks (measured 2026-09-04, fifth rollback of one split: the new
    # module carried whole import statements verbatim — six unused names —
    # and both import blocks were un-sorted; the repo-wide lint-debt count
    # rolled the apply back). Import-only rules; ruff absent = text unchanged.
    if step.new_content:  # у сведения дубля второй модуль не меняется
        step.new_content = _lint_fix_imports(
            prune_orphaned_imports(step.new_content), step.new_module, ws,
        )
    step.target_content = _lint_fix_imports(step.target_content, step.target, ws)

    # Deterministic self-checks; refuse rather than propose a broken step.
    try:
        new_tree = ast.parse(step.target_content)
        ast.parse(step.new_content)
    except SyntaxError as exc:
        return SplitPlan("no_split", f"planned step does not parse: {exc}")
    new_total = step.target_content.count("\n") + 1
    if new_total >= total_lines:
        return SplitPlan("no_split", "planned step does not shrink the target")
    if step.mode in {"functions", "dedup"}:
        kept = _bound_names(new_tree)
        missing = [n for n in step.moved_names if n not in kept]
        if missing:
            return SplitPlan(
                "no_split",
                "planned step would drop importable name(s): "
                + ", ".join(missing),
            )

    step.notes.append(f"target shrinks {total_lines} -> {new_total} lines")
    if step.mode == "dedup":
        return SplitPlan(
            "planned",
            f"dedup of {len(step.moved_names)} name(s) ({step.lines_moved} lines): "
            f"copies removed from {rel}, taken from {step.new_module}",
            step,
        )
    return SplitPlan(
        "planned",
        f"{step.mode} extraction of {len(step.moved_names)} name(s) "
        f"({step.lines_moved} lines) into {step.new_module}",
        step,
    )
