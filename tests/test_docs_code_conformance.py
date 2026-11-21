"""The documentation's code references must keep resolving.

Four guards already cover one axis each: `docs_link_check` (relative links),
`agent_anatomy_check` (the `core/` module index), `commands_map_check` (registry
↔ COMMANDS_MAP parity) and `registry_tally` (issue counts). None of them looks at
the code references embedded in *prose* — "``core/loop.py`` does X", "see
``cli/app.py:69``", "``:self-apply-run`` applies" — which is where most
documentation claims actually live.

`scripts/docs_code_conformance.py` checks those, and this test runs it, so the
conformance is a build gate rather than something re-verified by hand whenever
somebody wonders. Measured when it was introduced: 31 documents, 611 code paths,
138 line anchors, 299 command tokens — all resolving.

When this fails, the fix is one of four, and the script says which:
  * the document points at a file that no longer exists → update the document;
  * a line anchor drifted → update it, or declare the document historical in
    `_HISTORICAL_ANCHOR_DOCS` if its anchors are provenance for an old commit;
  * a documented `:command` is not in the registry → the command was renamed or
    removed, or the token is prose and belongs in `_NON_COMMAND_TOKENS`;
  * a **renamed** module is named as if it still existed → rewrite the sentence
    with the current name, or, if the sentence is dated narrative, mark that line
    `<!-- historical-ref -->` (see below).

## Why the renamed-path half of this file exists

Declaring a rename in `_RENAMED_PATHS` used to exempt the old name in *every*
document, and `_PATH_RE` only matched paths ending in `.py` while prose writes
`core/confidence_gate`. Both holes pointed the same way: a source-of-truth
document could go on describing a removed module as live architecture and this
guard still printed "every code reference resolves". The tests below pin the four
verdicts that replaced that blanket exemption, in both spellings.
"""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "docs_code_conformance.py"


def _load_guard():
    spec = importlib.util.spec_from_file_location("docs_code_conformance", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(name="guard")
def guard_fixture():
    """Named so the injected `guard` argument shadows nothing at module scope."""
    return _load_guard()


def _count(out: str, label: str, *, within: str | None = None) -> int:
    """First integer reported for that label, optionally on one summary line.

    Parsed with a regex rather than `split(":")` because one of the labels
    (`:command tokens`) contains a colon itself — the naive split reads the
    label instead of the number.

    ``within`` scopes the search to the summary line owning that counter.
    `declared historical` is reported twice — once for renamed paths, once for
    line anchors — so without it the answer depends on print order rather than
    on which counter the test means.
    """
    rows = [row for row in out.splitlines() if label in row]
    if within is not None:
        rows = [row for row in rows if within in row]
    scope = f" on the {within!r} line" if within else ""
    assert rows, f"no line reporting {label!r}{scope} in:\n{out}"
    row = rows[0]
    match = re.search(r":\s*(\d+)", row[row.index(label) + len(label):])
    assert match is not None, row
    return int(match.group(1))


def _docs_tree(tmp_path: Path, name: str, body: str) -> Path:
    """An isolated documentation tree — the real `docs/` is never touched."""
    root = tmp_path / "docs"
    root.mkdir(parents=True, exist_ok=True)
    (root / name).write_text(body, encoding="utf-8")
    return root


# ── Renamed modules: rejected as current, accepted as history ────────────────


def test_a_rename_whose_replacement_is_gone_still_fails(guard, tmp_path,
                                                       monkeypatch, capsys):
    """A declared rename is not a licence — the new path must exist.

    Amends the map through the module the guard actually reads, and rebuilds the
    pattern from it, so the new entry is visible to both halves of the rule —
    the extractor and the classifier.
    """
    monkeypatch.setitem(guard._RENAMED_PATHS, "core/gone_module.py",
                        "core/never_written.py")
    monkeypatch.setattr(guard, "_RENAMED_REF_RE", guard._renamed_ref_pattern())

    root = _docs_tree(
        tmp_path,
        "LIVE_PROBE_FINDINGS.md",  # even a document allowed to keep old names
        "# Live probe findings\n\nThe defect was in `core/gone_module`.\n",
    )
    assert guard.main(["--docs", str(root)]) == 1
    out = capsys.readouterr().out
    assert "MISSING PATHS" in out
    assert "core/never_written.py" in out
    assert "which does not exist either" in out


def test_classify_renamed_reference_separates_the_four_verdicts(guard):
    """The rule itself, as a pure function — no documentation tree involved."""
    renames = {"old/mod.py": "new/mod.py"}
    live = {"new/mod.py"}.__contains__          # only the new path is a file
    both = {"new/mod.py", "old/mod.py"}.__contains__
    neither: set[str] = set()                   # nothing on disk at all

    assert guard.classify_renamed_reference(
        "old/mod.py", doc="CURRENT.md", line="`old/mod.py` does X",
        exists=live, renames=renames, historical_docs=set(),
    ) == guard.RENAMED_LIVE_REFERENCE

    assert guard.classify_renamed_reference(
        "old/mod.py", doc="CURRENT.md",
        line="formerly `old/mod.py` <!-- historical-ref -->",
        exists=live, renames=renames, historical_docs=set(),
    ) == guard.RENAMED_HISTORICAL

    assert guard.classify_renamed_reference(
        "old/mod.py", doc="DATED_AUDIT.md", line="the defect was in `old/mod.py`",
        exists=live, renames=renames, historical_docs={"DATED_AUDIT.md"},
    ) == guard.RENAMED_HISTORICAL

    # A stale map outranks the historical allowlist: nothing to point at.
    assert guard.classify_renamed_reference(
        "old/mod.py", doc="DATED_AUDIT.md", line="the defect was in `old/mod.py`",
        exists=neither.__contains__, renames={"old/mod.py": "new/gone.py"},
        historical_docs={"DATED_AUDIT.md"},
    ) == guard.RENAMED_REPLACEMENT_MISSING

    # The rename was undone — the old path is a real file again.
    assert guard.classify_renamed_reference(
        "old/mod.py", doc="CURRENT.md", line="`old/mod.py` does X",
        exists=both, renames=renames, historical_docs=set(),
    ) == guard.RENAMED_OLD_PATH_EXISTS


def test_every_markdown_tree_the_repo_owns_is_scanned():
    """docs/ + repo root + knowledge/ — all three, or the guard has blind spots.

    Root docs were invisible until the 2026-08 audit found FABLE_AUDIT's anchors
    rotting undetected. knowledge/ was invisible for exactly one commit: the
    documents the AGENT reads moved there on 2026-08-07 and left this checker's
    field of view, which the document-count guard caught immediately. A stale
    code path in knowledge/ is worse than in prose — it is injected into the
    agent's context as current fact.
    """
    import io
    from contextlib import redirect_stdout

    mod = _load_guard()
    docs_count = len(list((REPO_ROOT / "docs").rglob("*.md")))
    root_count = len(list(REPO_ROOT.glob("*.md")))
    knowledge_count = len(list((REPO_ROOT / "knowledge").rglob("*.md")))

    buf = io.StringIO()
    with redirect_stdout(buf):
        assert mod.main([]) == 0
    out = buf.getvalue()
    scanned = _count(out, "documents scanned")
    assert scanned == docs_count + root_count + knowledge_count, (
        f"scanned {scanned}, expected docs/ ({docs_count}) + root ({root_count})"
        f" + knowledge/ ({knowledge_count})"
    )
