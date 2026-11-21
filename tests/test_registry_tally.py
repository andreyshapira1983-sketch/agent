"""The registry's tally must match the registry's own sections.

MASTER_ISSUE_REGISTRY is the single owner of issue status.
A hand-typed tally broke that three times in one session, ending with the
document claiming "50 issues total — this tally is the authoritative count"
four lines below "Total is now 53".

This is the guard that stops a stale count from being quoted as authority.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "registry_tally.py"


def _load():
    spec = importlib.util.spec_from_file_location("registry_tally", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_tally_matches_the_sections() -> None:
    assert _load().main(["--check"]) == 0, (
        "the status tally has drifted from the issue sections — run "
        "`python scripts/registry_tally.py --write`"
    )


def test_the_external_checklist_is_not_counted_as_an_issue() -> None:
    """It is a checklist of failure modes seen elsewhere, not a repo defect.

    Counting it is what made the totals ambiguous in the first place.
    """
    parse = _load().parse
    issue = "### MIR-001 — a defect\n- **Status:** `fixed`\n\n"
    checklist = "### MIR-external-checklist — seen elsewhere\n"

    with_status = parse(issue + checklist + "- **Status:** `planned_gap`\n")
    without_status = parse(issue + checklist + "- **Sources:** OFM\n")

    assert with_status == ({"fixed": ["001"]}, []), with_status
    assert without_status == ({"fixed": ["001"]}, []), (
        f"the checklist was held to the issue rule of a Status line: {without_status}"
    )
    # Control: the same missing Status under an issue id is reported.
    assert parse("### MIR-002 — a defect\n- **Sources:** x\n") == ({}, ["002"])


def test_no_issue_appears_under_two_statuses() -> None:
    module = _load()
    by_status, _ = module.parse(module.REGISTRY.read_text(encoding="utf-8"))

    seen: dict[str, str] = {}
    for status, ids in by_status.items():
        for ident in ids:
            assert ident not in seen, (
                f"MIR-{ident} is listed under both {seen[ident]!r} and {status!r}"
            )
            seen[ident] = status
