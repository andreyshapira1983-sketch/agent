"""Incremental splitter for oversized Python modules (junior-plan item #5).

The one-shot LLM splitter rewrites the whole target file in a single model
reply, which is bounded by the model's output-token ceiling -- a 4500-line
module like ``core/loop.py`` can never fit. This module removes that ceiling by
splitting *deterministically*:

* no LLM at all -- code is moved **verbatim** (exact source-line slices), so
  there is nothing to hallucinate and no token budget to exceed;
* one small step per invocation -- each run extracts ONE cohesive block under a
  line budget, leaving a re-export (or a mixin base) behind, so the module
  shrinks safely across several approved steps instead of one big-bang rewrite;
* every step still flows through the normal safety lane: an approval-inbox
  item, human approval, targeted + full tests, auto-rollback on red.

Two extraction modes, chosen automatically:

1. **function mode** -- moves a dependency-closed group of top-level functions
   (and the module constants only they use) into a new sibling module; the
   target re-exports every moved name so all existing import paths keep
   working (the hard rule learned from the verifier-split rollbacks).
2. **mixin mode** -- when the file is dominated by one huge class (the
   ``AgentLoop`` case), moves self-contained methods into a mixin class in a
   new sibling module and adds the mixin to the class bases. Attribute access
   through ``self`` keeps working unchanged.

Safety rules (any violation -> the block simply is not moved):

* moved code may reference ONLY builtins, imported names, and other moved
  names -- never a name that stays behind (that would create a circular
  import);
* methods using ``super()``, ``global``/``nonlocal``, or name-mangled
  ``__private`` attributes are never moved (their semantics are tied to the
  defining class/module);
* both resulting files must parse; the target must actually shrink; every
  moved top-level name must remain importable from the target.
"""
from __future__ import annotations

import ast
import builtins as _builtins_mod
from dataclasses import dataclass, field
from pathlib import Path

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
        elif isinstance(sub, ast.ExceptHandler) and sub.name:
            bound.add(sub.name)
        elif isinstance(sub, ast.MatchAs) and sub.name:
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
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name):
            if sub.func.id == "super":
                return True
        if isinstance(sub, ast.Attribute):
            if sub.attr.startswith("__") and not sub.attr.endswith("__"):
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


def _pick_new_module_path(workspace: Path, target: str, suffix: str) -> str:
    base = target.replace("\\", "/").removesuffix(".py")
    candidate = f"{base}_{suffix}.py"
    n = 2
    while (workspace / candidate).exists():
        candidate = f"{base}_{suffix}{n}.py"
        n += 1
    return candidate


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
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
            if isinstance(first.value.value, str):  # module docstring
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
    ordered = sorted(candidates.values(), key=lambda n: n.lineno)
    return ordered


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
        assert isinstance(node, ast.Assign)
        tgt = node.targets[0]
        assert isinstance(tgt, ast.Name)
        return tgt.id

    kept = list(group)
    while kept and total(kept) > max_lines:
        names = {name_of(n) for n in kept}
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
) -> SplitStep | None:
    lines = src.split("\n")
    group = _movable_function_group(tree, dominant_class)
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
            assert isinstance(tgt, ast.Name)
            moved_names.append(tgt.id)

    refs: set[str] = set()
    for node in group:
        refs |= _free_refs(node)
    refs -= set(moved_names)
    import_stmts = _needed_import_stmts(tree, lines, refs)

    new_rel = _pick_new_module_path(workspace, target, "helpers")
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
    insert_after = _last_import_end(tree)
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
    target_content = _collapse_blank_runs("\n".join(out))
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
        span = (node.end_lineno or node.lineno) - node.lineno + 1
        for deco in node.decorator_list:
            span += 0 if deco.lineno >= node.lineno else 0
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
    insert_after = _last_import_end(tree)
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
    target_content = _collapse_blank_runs("\n".join(out))
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


# ── public API ───────────────────────────────────────────────────────────────


def plan_incremental_split(
    workspace: str | Path,
    target: str,
    *,
    max_move_lines: int = DEFAULT_MAX_MOVE_LINES,
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

    step = _plan_function_split(ws, rel, src, tree, dominant, max_move_lines)
    if step is None and dominant is not None:
        step = _plan_mixin_split(ws, rel, src, tree, dominant, max_move_lines)
    if step is None:
        return SplitPlan(
            "no_split",
            f"no self-contained block under {max_move_lines} lines found in "
            f"{rel!r} (every candidate references names that must stay)",
        )

    # Deterministic self-checks; refuse rather than propose a broken step.
    try:
        new_tree = ast.parse(step.target_content)
        ast.parse(step.new_content)
    except SyntaxError as exc:
        return SplitPlan("no_split", f"planned step does not parse: {exc}")
    new_total = step.target_content.count("\n") + 1
    if new_total >= total_lines:
        return SplitPlan("no_split", "planned step does not shrink the target")
    if step.mode == "functions":
        kept = _bound_names(new_tree)
        missing = [n for n in step.moved_names if n not in kept]
        if missing:
            return SplitPlan(
                "no_split",
                "planned step would drop importable name(s): "
                + ", ".join(missing),
            )

    step.notes.append(f"target shrinks {total_lines} -> {new_total} lines")
    return SplitPlan(
        "planned",
        f"{step.mode} extraction of {len(step.moved_names)} name(s) "
        f"({step.lines_moved} lines) into {step.new_module}",
        step,
    )
