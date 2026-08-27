#!/usr/bin/env python3
"""Generate knowledge/generated/AGENT_ANATOMY.md as a grouped module index.

Groups the flat core/*.py modules under the architecture sections (see
"архитектура автономного Агента.txt" / AGENT_DOCTRINE) so the anatomy map is
navigable. Modules physically stay in core/ (paths are semantic data elsewhere),
so every row is still `core/<name>` and the read-only drift check keeps working.

Run:  python scripts/gen_anatomy.py
"""
from __future__ import annotations

import ast
import os
import re
import sys
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: Where the generated map is written. `scripts/agent_anatomy_check.py` declares
#: the same path as the place it READS, and `tests/test_agent_anatomy_check.py`
#: asserts the two agree. Measured 2026-08-07: moving the writer alone left the
#: whole suite green — the checker went on reading a file nobody updated any
#: more, and both halves looked healthy on their own.
DOC_PATH = os.path.join(ROOT, "knowledge", "generated", "AGENT_ANATOMY.md")
CORE = os.path.join(ROOT, "core")

# Ordered logical groups. Each module name may appear in exactly one group.
# Таблица групп переехала в core/anatomy_groups.py (MIR-180): полоса
# самоприменения вправе менять core/*.py и НЕ вправе менять scripts/, а без
# строки группировки каждый инкрементальный раскол откатывался анатомическим
# сторожем. Читаем ЛИТЕРАЛ ast-разбором, не импортируя: правило этого скрипта
# «не исполнять код агента» сохраняется.
def _load_groups() -> list:
    import ast as _ast

    with open(os.path.join(ROOT, "core", "anatomy_groups.py"),
              encoding="utf-8") as fh:
        source = fh.read()
    for node in _ast.parse(source).body:
        target = getattr(node, "target", None) or (
            node.targets[0] if getattr(node, "targets", None) else None)
        if getattr(target, "id", "") == "GROUPS":
            return _ast.literal_eval(node.value)
    raise ValueError("GROUPS not found in core/anatomy_groups.py")


GROUPS: list[tuple[str, str, list[str]]] = _load_groups()


def _first_doc_line(stem: str) -> str:
    """First *sentence* of the module docstring, rejoined across wrapped lines.

    Reading `splitlines()[0]` used to cut the summary wherever the author had
    wrapped it, producing map rows that ended mid-clause ("... and the",
    "... by the"). The docstring's opening paragraph is the summary; a physical
    line break inside it carries no meaning.
    """
    path = os.path.join(CORE, f"{stem}.py")
    try:
        doc = ast.get_docstring(ast.parse(Path(path).read_text(encoding="utf-8"))) or ""
    except (OSError, SyntaxError, UnicodeDecodeError, ValueError):
        doc = ""
    if not doc.strip():
        return ""

    # The opening paragraph, unwrapped: everything before the first blank line.
    paragraph = " ".join(
        part.strip()
        for part in doc.strip().split("\n\n")[0].splitlines()
        if part.strip()
    )

    # Cut at the first sentence end, so a long paragraph does not fill the cell.
    # A dot only ends a sentence when a space follows and the next word starts a
    # new one, which keeps "e.g." and "core/loop.py" intact.
    match = re.search(r"(?<!\be\.g)(?<!\bi\.e)[.?!](?=\s+[^a-z]|\s*$)", paragraph)
    sentence = paragraph[: match.end()] if match else paragraph
    return sentence.strip().replace("|", "\\|")


def _actual_modules() -> set[str]:
    return {
        e[:-3] for e in os.listdir(CORE)
        if e.endswith(".py") and e != "__init__.py"
    }


def build_document() -> str:
    """Render the whole map as text. Pure apart from reading `core/`.

    Split out from :func:`main` so a test can compare the committed document
    against what the generator would write, without writing anything. Nothing
    enforced that equality before, which is exactly how `GROUPS` drifted 37
    modules behind `core/` while the map itself looked in sync — the drift check
    compares module *names*, and hand-edits kept those correct.

    Raises :class:`ValueError` on a bad `GROUPS`, never `SystemExit`: this is a
    library function now, and killing the caller's process is not its decision.
    `SystemExit` derives from `BaseException`, so an ordinary
    ``except Exception`` around it would not even catch it. :func:`main` turns
    the error into a message and exit code 1, exactly as before.
    """
    actual = _actual_modules()
    seen: set[str] = set()
    for _title, _desc, mods in GROUPS:
        for m in mods:
            if m in seen:
                raise ValueError(f"module listed twice in GROUPS: {m}")
            seen.add(m)

    missing = sorted(actual - seen)
    unknown = sorted(seen - actual)
    if missing:
        raise ValueError(f"modules in core/ not grouped: {missing}")
    if unknown:
        raise ValueError(f"grouped names with no core/ module: {unknown}")

    out: list[str] = []
    out.append("# Agent Anatomy")
    out.append("")
    out.append("Grouped module index for the `core/` package, organized by the")
    out.append("architecture sections (§1–§12). Modules physically live flat in `core/`")
    out.append("— their paths are used as semantic identifiers elsewhere (planner")
    out.append("self-build targets, locators, audits), so this map groups them")
    out.append("*logically* without moving files.")
    out.append("")
    out.append("Kept in sync with the codebase by `scripts/agent_anatomy_check.py`")
    out.append("(read-only drift check, TD-029). Regenerate with")
    out.append("`python scripts/gen_anatomy.py` whenever a module is added or removed.")
    out.append("")
    out.append(f"_Total: {len(actual)} modules across {len(GROUPS)} groups._")
    out.append("")
    for title, desc, mods in GROUPS:
        out.append(f"## {title}")
        out.append("")
        out.append(f"_{desc}_")
        out.append("")
        out.append("| Module | Purpose |")
        out.append("| ------ | ------- |")
        for m in mods:
            out.append(f"| `core/{m}` | {_first_doc_line(m)} |")
        out.append("")

    return "\n".join(out)


def main() -> int:
    try:
        text = build_document()
    except ValueError as exc:
        # Same operator-visible contract the old `raise SystemExit(msg)` had:
        # the message on stderr, exit code 1.
        print(exc, file=sys.stderr)
        return 1
    os.makedirs(os.path.dirname(DOC_PATH), exist_ok=True)
    with open(DOC_PATH, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    print(
        f"Wrote knowledge/generated/AGENT_ANATOMY.md: {len(_actual_modules())} modules, "
        f"{len(GROUPS)} groups."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
