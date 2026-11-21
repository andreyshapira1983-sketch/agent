#!/usr/bin/env python3
"""Generate knowledge/generated/AGENT_ANATOMY.md: the grouped index of core/ and tools/ modules."""
from __future__ import annotations

import ast
import os
import re
import sys
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: Must equal the path agent_anatomy_check.py reads (a test asserts writer and reader agree).
DOC_PATH = os.path.join(ROOT, "knowledge", "generated", "AGENT_ANATOMY.md")
CORE = os.path.join(ROOT, "core")

# GROUPS (ordered, each module in one group) lives in core/anatomy_groups.py, which the
# self-apply lane may edit; it is read as a literal with ast and never imported.
def load_groups_from_source(source: str) -> list:
    """The GROUPS literal parsed from core/anatomy_groups.py text (public: proposals use it)."""
    import ast as _ast

    for node in _ast.parse(source).body:
        target = getattr(node, "target", None) or (
            node.targets[0] if getattr(node, "targets", None) else None)
        if getattr(target, "id", "") == "GROUPS":
            return _ast.literal_eval(node.value)
    raise ValueError("GROUPS not found in core/anatomy_groups.py")


def _load_groups() -> list:
    with open(os.path.join(ROOT, "core", "anatomy_groups.py"),
              encoding="utf-8") as fh:
        return load_groups_from_source(fh.read())


GROUPS: list[tuple[str, str, list[str]]] = _load_groups()


def purpose_from_source(source: str) -> str:
    """The Purpose cell for a module given its source text (works for files not on disk yet)."""
    try:
        doc = ast.get_docstring(ast.parse(source)) or ""
    except (SyntaxError, ValueError):
        doc = ""
    return first_sentence(doc)


def _first_doc_line(stem: str) -> str:
    """First sentence of a core module's docstring, rejoined across wrapped lines."""
    path = os.path.join(CORE, f"{stem}.py")
    try:
        return purpose_from_source(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return ""


def first_sentence(doc: str) -> str:
    """The opening sentence of a docstring, unwrapped and cell-safe."""
    if not doc.strip():
        return ""

    # The opening paragraph, unwrapped: everything before the first blank line.
    paragraph = " ".join(
        part.strip()
        for part in doc.strip().split("\n\n")[0].splitlines()
        if part.strip()
    )

    # Cut at the first sentence end: a dot counts only before whitespace and a non-lowercase
    # word, so "e.g." and "core/loop.py" stay intact.
    match = re.search(r"(?<!\be\.g)(?<!\bi\.e)[.?!](?=\s+[^a-z]|\s*$)", paragraph)
    sentence = paragraph[: match.end()] if match else paragraph
    return sentence.strip().replace("|", "\\|")


def _actual_modules() -> set[str]:
    return {
        e[:-3] for e in os.listdir(CORE)
        if e.endswith(".py") and e != "__init__.py"
    }


#: Where things are wired outside core/, so the agent finds them instead of guessing.
#: Each line was checked against the code; change it when the code changes.
WIRING = (
    ("Tools are registered in `app/bootstrap.py` — `registry.register(SomeTool(...))`; "
     "there is no separate registry module."),
    ("`Tool` and `ToolRegistry` live in `tools/base.py`; a tool's `risk` is a plain string "
     "(`Risk = Literal[\"read_only\", \"reversible\", \"irreversible\", \"external\"]`), "
     "not an enum."),
    ("A model call goes through a role object: `model_router.for_role(ModelRole.X).complete("
     "system=..., user=...)` (`core/model_router.py`); that wrapper records tokens and dollars "
     "and checks the budget. A tool that needs a model is given `model_router` in "
     "`app/bootstrap.py`, as `tools/spawn_subagent.py` is."),
    ("Self-repair: an edit is written to `proposals/selffix/<name>/edits.txt` "
     "(FILE: + SEARCH/REPLACE or LINES blocks) and tried by `patch_check` on a copy of the repo."),
)

#: Who judges an answer, in turn order (test_the_map_says_who_judges checks it against core/).
#: A same-family model judge overrates its own (arXiv 2404.13076), so the final yes is Claude's.
JUDGES = (
    ("synthesis", "draft_refutation", "code", "a draft that contradicts its own evidence is rewritten once"),
    ("verify", "verifier_core", "code + model", "each claim needs a matching cited source; a model checks the source entails it"),
    ("verify", "relevance_judge", "model", "does the answer address what was asked (per claim: yes / no / idk)"),
    ("compose", "answer_contradiction", "code", "one claim stated both as a fact and as unverified"),
    ("compose", "unsupported_claims", "code", "claims whose citations do not match this turn's sources are cut"),
    ("compose", "low_evidence_policy", "code", "an answer with too little verified support is shortened, guesses hidden"),
    ("compose", "output_policy", "code", "the source ranking's warnings reach the answer"),
    ("after compose", "requested_format", "code (+ model to rewrite)", "the line the user prescribed ends the answer, clean"),
    ("after compose", "completion_obligation", "code", "an obligation the turn incurred and left unmet"),
    ("after compose", "request_checklist", "model", "each requirement of the request as a yes/no question (TICK)"),
    ("goal closure", "campaign_verdict", "code", "the goal's criterion observed in the world, traces newer than the run"),
    ("goal closure", "goal_content_judge", "model", "what the product SAYS meets the criterion; every yes needs a quote"),
    ("out of band", "judge_queue", "Claude, outside the run", "every 'goal met' is provisional until Claude rules"),
)


def _tools_rows() -> list[str]:
    tools_dir = os.path.join(os.path.dirname(CORE), "tools")
    rows = []
    if not os.path.isdir(tools_dir):
        return rows
    for entry in sorted(os.listdir(tools_dir)):
        if not entry.endswith(".py") or entry == "__init__.py":
            continue
        try:
            text = Path(os.path.join(tools_dir, entry)).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            text = ""
        rows.append(f"| `tools/{entry[:-3]}` | {purpose_from_source(text)} |")
    return rows


def build_document(
    *,
    actual: set[str] | None = None,
    groups: list | None = None,
    purpose=None,
) -> str:
    """Render the whole map as text; pure apart from reading ``core/``.

    The keyword arguments let a self-apply proposal render the map of a tree not on disk.
    Raises ValueError on a bad GROUPS, never SystemExit: this is a library function.
    """
    actual = _actual_modules() if actual is None else set(actual)
    groups = GROUPS if groups is None else groups
    purpose = _first_doc_line if purpose is None else purpose
    seen: set[str] = set()
    for _title, _desc, mods in groups:
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
    out.append(f"_Total: {len(actual)} modules across {len(groups)} groups._")
    out.append("")
    for title, desc, mods in groups:
        out.append(f"## {title}")
        out.append("")
        out.append(f"_{desc}_")
        out.append("")
        out.append("| Module | Purpose |")
        out.append("| ------ | ------- |")
        for m in mods:
            out.append(f"| `core/{m}` | {purpose(m)} |")
        out.append("")

    out.append("## Who judges an answer, and in what order")
    out.append("")
    out.append("| Stage | Module | How | What it judges |")
    out.append("| ----- | ------ | --- | -------------- |")
    out.extend(f"| {stage} | `core/{mod}` | {how} | {what} |" for stage, mod, how, what in JUDGES)
    out.append("")
    out.append("Exams judge from outside the agent: the nightly anchor exam and the work exam "
               "check files and tests, not the agent's words.")
    out.append("")
    out.append("## Where things are wired (outside core/)")
    out.append("")
    out.extend(f"- {line}" for line in WIRING)
    out.append("")
    out.append("| Tool module | Purpose |")
    out.append("| ----------- | ------- |")
    out.extend(_tools_rows())
    out.append("")

    return "\n".join(out)


def main() -> int:
    try:
        text = build_document()
    except ValueError as exc:
        # Operator-visible contract: the message on stderr, exit code 1.
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
