"""Keeping the anatomy map and its group table in step with a proposal.

Born of surgery 2026-09-04: `_sync_anatomy_index` lived in
`core/self_build_producer.py`, wrote its own canned row and never touched
the module total, so EVERY split proposal that added a module rolled back
on the anatomy guard (which compares the map with `scripts/gen_anatomy.py`
byte for byte). The repair rendered the proposal's map through the
generator itself; the producer file then grew past its size ratchet, and
this block moved here. Nothing decides anything: the functions rewrite two
files inside a proposal so the guards downstream find what they expect.
"""
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


#: Таблица групп карты анатомии — лежит в core НАМЕРЕННО (MIR-180): полоса
#: вправе менять core/*.py и не вправе scripts/, а без строки группировки
#: каждый инкрементальный раскол откатывался анатомическим сторожем.
_ANATOMY_GROUPS_PATH = "core/anatomy_groups.py"


def _sync_anatomy_groups(
    build: dict[str, Any], target: str, reader: Callable[[str], str | None]
) -> None:
    """Вложить строку группировки нового core-модуля в само предложение.

    Правило наследования детерминированное: новый модуль встаёт в группу
    своего ИСХОДНИКА — семантику расщепитель знать не может, происхождение
    знает точно. Исходника нет в таблице — строку не выдумываем: анатомический
    сторож поймает пропуск и назовёт его, а выдуманная группа лгала бы карте.
    """
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
    files.append({"path": _ANATOMY_GROUPS_PATH, "content": updated})
    build["files"] = files


def _load_anatomy_generator(workspace: str | Path | None) -> Any:
    """The repo's own `scripts/gen_anatomy.py`, loaded by path the way the
    anatomy test loads it — never imported as a package. ``None`` when the
    workspace has no generator (a sandbox), so the caller can fall back."""
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
    """Keep ``knowledge/generated/AGENT_ANATOMY.md`` in sync when the proposal
    adds NEW core modules.

    Измерено 2026-09-04 (полоса, откат ain_b7a0…): эта функция дописывала
    СВОЮ строку («Extracted from … by autonomous self-build module split») и
    не трогала «_Total: N modules», а сторож анатомии сравнивает карту с
    выводом `scripts/gen_anatomy.py` побайтно. Итог: каждый раскол с новым
    модулем откатывался на одном и том же тесте — молча, всегда. Теперь
    карта в заявке — то, что напишет САМ генератор для предложенного дерева:
    группы из предложенного `core/anatomy_groups.py`, модули с диска плюс
    новые, назначение — первая фраза докстринга из содержимого заявки.

    Без рабочей области (песочница без генератора) — прежний канцелярский
    путь. Best-effort: если карту нельзя отрисовать (группы не покрывают
    модули), она остаётся как была, и сторож полосы назовёт пропуск.
    """
    files = build.get("files") or []
    stems = _new_core_module_stems(files)
    if not stems:
        return
    gen = _load_anatomy_generator(workspace)
    if gen is not None:
        # Группы — прежде карты, независимо от порядка у вызывающего: карта
        # рендерится ПО группам, и без строки нового модуля генератор честно
        # отказывает («not grouped»), а отказ уводил в канцелярский путь —
        # второй откат того же раскола 2026-09-04 (ain_0bd1…).
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
