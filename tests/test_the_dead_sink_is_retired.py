"""Consolidation reports are computed on demand, never persisted — MIR-044.

WHY THIS EXISTS. The operator ruled on 2026-07-19: repurposing rejected —
re-reading `consolidate_memory` showed the report is a pure tally of statuses
the procedures already hold, so there is nothing in it to "apply". The
prescription was retirement: stop persisting, compute the CLI tally on demand,
archive history. A month later the reports were still written every cycle —
**255 reports, 738 KB**, the third-largest state file, read by exactly one
display command. This entry was not awaiting a decision; it was awaiting the
execution of one, and this file is that execution's witness.

THE SHAPE. `consolidate_memory` itself is untouched — it is a pure function
and the tally is still wanted, on demand: `:memory-consolidate` computes and
prints it fresh, `:smart-memory` derives its consolidation section from the
live stores. What stops existing is the per-cycle persistence: the loop's
memory-write path no longer computes or saves a report at all, because a tally
nobody reads until an operator asks is exactly what "on demand" means.

WHAT THIS DOES NOT TOUCH. The archived history (`data/` file renamed to
`.archive.jsonl` by the migration) stays readable; `MemoryConsolidationStore`
the class survives for that. The `consolidation` durable-sink token stays in
the vocabulary with a retirement note, so an old config naming it still
validates instead of failing on a phantom typo.
"""
from __future__ import annotations

import ast
import pathlib

from core.smart_memory import consolidate_memory


def test_no_production_site_saves_a_consolidation_report() -> None:
    """The ruling's first clause, pinned structurally: nothing persists.
    Walks every production root for a `.save(` on a consolidation store."""
    offenders: list[str] = []
    for root in ("core", "cli", "app"):
        for path in sorted(pathlib.Path(root).rglob("*.py")):
            src = path.read_text(encoding="utf-8", errors="replace")
            if "consolidation_store" not in src:
                continue
            tree = ast.parse(src)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                fn = node.func
                if (isinstance(fn, ast.Attribute) and fn.attr == "save"
                        and isinstance(fn.value, ast.Attribute)
                        and fn.value.attr == "consolidation_store"):
                    offenders.append(f"{path}:{node.lineno}")
    assert offenders == [], (
        f"consolidation reports are still persisted at {offenders} — the "
        "2026-07-19 retirement ruling remains unexecuted"
    )


def test_the_cycle_write_path_does_not_consolidate() -> None:
    """The per-cycle arm specifically: 255 reports accumulated because every
    cycle computed and saved a tally nobody would read until asked."""
    import inspect

    import core.loop_memory_write as mod

    src = inspect.getsource(mod)
    assert "consolidate_memory(" not in src, (
        "the memory-write path still computes a consolidation report per cycle"
    )


def test_the_tally_is_still_available_on_demand() -> None:
    """Retirement must not lose the tally itself — the pure function stays,
    and the operator's command computes it fresh."""
    report = consolidate_memory(episodes=[], procedures=[])
    assert report.episode_count == 0
    assert report.procedure_count == 0
    import inspect

    import cli.commands_memory as cli_mod

    src = inspect.getsource(cli_mod)
    assert "consolidate_memory(" in src, (
        ":memory-consolidate no longer computes the tally at all — retirement "
        "was supposed to remove the persistence, not the answer"
    )
