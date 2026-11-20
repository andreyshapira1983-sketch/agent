"""Keep the anatomy map and its group table in step with a proposal."""
from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

_ANATOMY_DOC_PATH = "knowledge/generated/AGENT_ANATOMY.md"


def _new_core_module_stems(files: list[dict[str, Any]]) -> list[str]:
    """Bare stems of every ``core/<name>.py`` file carried in the proposal."""
    stems: list[str] = []
    for entry in files:
        path = str(entry.get("path") or "").replace("\\", "/").strip()
        if path.startswith("core/") and path.endswith(".py"):
            stem = path[len("core/"):-len(".py")]
            if stem and "/" not in stem and stem != "__init__":
                stems.append(stem)
    return stems


#: Lives in core on purpose: the change lane may edit core/*.py but not scripts/.
_ANATOMY_GROUPS_PATH = "core/anatomy_groups.py"


def _sync_anatomy_groups(
    build: dict[str, Any], target: str, reader: Callable[[str], str | None]
) -> None:
    """Add the group row of each new core module to the proposal, inheriting the group of its source module; a source missing from the table gets no row (the guard reports it)."""
    files = build.get("files") or []
    target_norm = str(target or "").replace("\\", "/").strip()
    if not target_norm.startswith("core/"):
        return
    target_stem = target_norm[len("core/"):-len(".py")]
    new_stems = [s for s in _new_core_module_stems(files) if s != target_stem]
    if not new_stems:
        return
    groups_src = reader(_ANATOMY_GROUPS_PATH)
    if not groups_src or f'"{target_stem}"' not in groups_src:
        return
    anchor = f'"{target_stem}",'
    if anchor not in groups_src:
        return
    insertion = "".join(
        f'\n        "{stem}",' for stem in new_stems
        if f'"{stem}"' not in groups_src
    )
    if not insertion:
        return
    updated = groups_src.replace(anchor, anchor + insertion, 1)
    # Replace, do not append: a second call would list the file twice in files_changed.
    _carry_file(files, _ANATOMY_GROUPS_PATH, updated)
    build["files"] = files


def _load_anatomy_generator(workspace: str | Path | None) -> Any:
    """Load the repo's scripts/gen_anatomy.py by path (not as a package); None when absent."""
    if workspace is None:
        return None
    path = Path(workspace) / "scripts" / "gen_anatomy.py"
    if not path.is_file():
        return None
    try:
        import importlib.util

        spec = importlib.util.spec_from_file_location("gen_anatomy_for_proposal", path)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except Exception:  # noqa: BLE001 — a generator that cannot load is «absent», the canned path stays
        return None
    return module


def _sync_anatomy_index(
    build: dict[str, Any], target: str, reader: Callable[[str], str | None],
    *, workspace: str | Path | None = None,
) -> None:
    """Sync knowledge/generated/AGENT_ANATOMY.md when the proposal adds new core modules; best-effort."""
    files = build.get("files") or []
    stems = _new_core_module_stems(files)
    if not stems:
        return
    gen = _load_anatomy_generator(workspace)
    if gen is not None:
        # Groups first: the map is rendered per group and the generator refuses a module without a group row.
        _sync_anatomy_groups(build, target, reader)
        files = build.get("files") or files
        rendered = _render_map_for_proposal(gen, files, stems, reader, workspace)
        if rendered is not None:
            _carry_file(files, _ANATOMY_DOC_PATH, rendered)
            build["files"] = files
            return
    doc_text = reader(_ANATOMY_DOC_PATH) or ""
    if not doc_text.strip():
        return
    documented = set(re.findall(r"core/([a-zA-Z0-9_]+)", doc_text))
    target_stem = (
        target[len("core/"):-len(".py")]
        if target.startswith("core/") and target.endswith(".py")
        else ""
    )
    # A split target may itself be new-ish or renamed; only add rows for modules
    # not already documented, and never re-add the target's own row.
    missing = [s for s in stems if s not in documented and s != target_stem]
    if not missing:
        return

    def _row(stem: str) -> str:
        parent = target_stem or "self-build"
        return (
            f"| `core/{stem}` | Extracted from `core/{parent}` by autonomous "
            "self-build module split. |"
        )

    lines = doc_text.splitlines()
    anchor = -1
    last_row = -1
    for i, line in enumerate(lines):
        if re.search(r"\|\s*`core/[a-zA-Z0-9_]+`", line):
            last_row = i
            if target_stem and f"`core/{target_stem}`" in line:
                anchor = i
    insert_at = anchor if anchor >= 0 else last_row
    new_rows = [_row(s) for s in missing]
    if insert_at >= 0:
        lines[insert_at + 1: insert_at + 1] = new_rows
    else:
        lines.extend(["", *new_rows])
    new_doc = "\n".join(lines)
    if doc_text.endswith("\n"):
        new_doc += "\n"
    _carry_file(files, _ANATOMY_DOC_PATH, new_doc)
    build["files"] = files


def _carry_file(files: list[dict[str, Any]], path: str, content: str) -> None:
    """Put ``content`` under ``path`` in the proposal's file list, replacing
    an existing entry or appending one."""
    for entry in files:
        if str(entry.get("path") or "").replace("\\", "/").strip() == path:
            entry["content"] = content
            return
    files.append({"path": path, "content": content})


def _render_map_for_proposal(
    gen: Any, files: list[dict[str, Any]], stems: list[str],
    reader: Callable[[str], str | None], workspace: str | Path | None,
) -> str | None:
    """The anatomy map the generator would write for the PROPOSED tree, or
    ``None`` when it cannot be rendered (groups do not cover the modules —
    the guard will say so by name)."""
    carried: dict[str, str] = {
        str(e.get("path") or "").replace("\\", "/").strip(): str(e.get("content") or "")
        for e in files
    }
    groups_src = carried.get(_ANATOMY_GROUPS_PATH) or reader(_ANATOMY_GROUPS_PATH) or ""
    core_dir = Path(workspace) / "core" if workspace is not None else None
    if not groups_src or core_dir is None or not core_dir.is_dir():
        return None
    try:
        groups = gen.load_groups_from_source(groups_src)
        actual = {
            p.name[:-3] for p in core_dir.glob("*.py") if p.name != "__init__.py"
        } | set(stems)

        def purpose(stem: str) -> str:
            source = carried.get(f"core/{stem}.py")
            if source is None:
                source = reader(f"core/{stem}.py") or ""
            return gen.purpose_from_source(source)

        return gen.build_document(actual=actual, groups=groups, purpose=purpose)
    except Exception:  # noqa: BLE001 — unrenderable = leave the map alone; the guard names it
        return None
