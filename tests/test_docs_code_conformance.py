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
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "docs_code_conformance.py"

#: A module that exists, and one that never will — used as rename targets.
LIVE_MODULE = "core/evidence_support.py"
RENAMED_OLD = "core/confidence_gate.py"


def _run(*extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *extra],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        encoding="utf-8",
        errors="replace",
    )


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


def _count(out: str, label: str) -> int:
    """First integer reported for that label.

    Parsed with a regex rather than `split(":")` because one of the labels
    (`:command tokens`) contains a colon itself — the naive split reads the
    label instead of the number.
    """
    line = next(l for l in out.splitlines() if label in l)
    match = re.search(r":\s*(\d+)", line[line.index(label) + len(label):])
    assert match is not None, line
    return int(match.group(1))


def _docs_tree(tmp_path: Path, name: str, body: str) -> Path:
    """An isolated documentation tree — the real `docs/` is never touched."""
    root = tmp_path / "docs"
    root.mkdir(parents=True, exist_ok=True)
    (root / name).write_text(body, encoding="utf-8")
    return root


# ── The real repository ──────────────────────────────────────────────────────

def test_every_code_reference_in_the_docs_resolves():
    result = _run()
    assert result.returncode == 0, (
        "documentation drifted from the code:\n" + result.stdout + result.stderr
    )
    assert "every code reference resolves" in result.stdout


def test_no_repository_document_presents_a_renamed_module_as_current():
    """The reconciliation this guard was hardened for, asserted directly.

    `test_every_code_reference_in_the_docs_resolves` would also catch this, but
    only as an opaque non-zero exit. This says which property is being kept.
    """
    out = _run().stdout
    assert _count(out, "renamed-path refs") > 0, (
        "no renamed path is referenced anywhere — either the rename map went "
        "stale or the extractor stopped matching:\n" + out
    )
    assert _count(out, "live references") == 0, out
    assert _count(out, "replacement missing") == 0, out
    assert "RENAMED MODULE PRESENTED AS CURRENT" not in out, out


def test_the_checker_actually_checked_something():
    """Guard the guard: an extractor that finds nothing must not pass silently."""
    out = _run().stdout
    for marker in ("code paths referenced", "renamed-path refs",
                   "line anchors checked", ":command tokens"):
        assert marker in out, out

    assert _count(out, "documents scanned") >= 25
    assert _count(out, "code paths referenced") >= 400
    assert _count(out, ":command tokens") >= 200


def test_those_thresholds_would_catch_a_dead_extractor(tmp_path):
    """And the thresholds above are not vacuous.

    Point the same script at a documentation tree with nothing in it: every
    count collapses, so the assertions in `test_the_checker_actually_checked
    _something` would fail rather than pass on an extractor that returns nothing.
    """
    empty = tmp_path / "docs"
    empty.mkdir()
    (empty / "EMPTY.md").write_text("# nothing to extract here\n", encoding="utf-8")

    out = _run("--docs", str(empty)).stdout
    assert _count(out, "code paths referenced") == 0, out
    assert _count(out, "renamed-path refs") == 0, out
    assert _count(out, ":command tokens") == 0, out
    assert _count(out, "code paths referenced") < 400
    assert _count(out, ":command tokens") < 200


# ── Renamed modules: rejected as current, accepted as history ────────────────

def test_extensionless_reference_to_a_renamed_module_is_rejected(tmp_path):
    """`core/confidence_gate` — the spelling authoritative docs actually use.

    This is the hole that let the drift through: `_PATH_RE` required `.py`, so
    the extensionless form was not merely exempted, it was never extracted.
    """
    root = _docs_tree(
        tmp_path,
        "CURRENT_ARCHITECTURE.md",
        "# Current architecture\n\n"
        "- Verifier + confidence gating: `core/verifier*`, `core/confidence_gate`.\n",
    )
    result = _run("--docs", str(root))
    assert result.returncode == 1, result.stdout
    assert "RENAMED MODULE PRESENTED AS CURRENT" in result.stdout
    assert "CURRENT_ARCHITECTURE.md:3" in result.stdout
    assert _count(result.stdout, "live references") == 1


def test_dotted_reference_to_a_renamed_module_is_rejected(tmp_path):
    root = _docs_tree(
        tmp_path,
        "CURRENT_ARCHITECTURE.md",
        "# Current architecture\n\n"
        f"The gate lives in `{RENAMED_OLD}` and blocks the answer.\n",
    )
    result = _run("--docs", str(root))
    assert result.returncode == 1, result.stdout
    assert "RENAMED MODULE PRESENTED AS CURRENT" in result.stdout
    assert _count(result.stdout, "live references") == 1


def test_an_inline_historical_marker_accepts_the_old_name(tmp_path):
    """A mixed document may keep the old name on a line it declares historical."""
    root = _docs_tree(
        tmp_path,
        "CURRENT_ARCHITECTURE.md",
        "# Current architecture\n\n"
        f"Telemetry lives in `{LIVE_MODULE}`.\n\n"
        f"> Formerly `{RENAMED_OLD}` <!-- historical-ref -->, renamed 2026-07-27.\n",
    )
    result = _run("--docs", str(root))
    assert result.returncode == 0, result.stdout
    assert "every code reference resolves" in result.stdout
    assert _count(result.stdout, "declared historical") == 1
    assert _count(result.stdout, "live references") == 0


def test_the_marker_covers_only_its_own_line(tmp_path):
    """Per-line, never per-document — otherwise it is the old blanket exemption."""
    root = _docs_tree(
        tmp_path,
        "CURRENT_ARCHITECTURE.md",
        f"> Formerly `{RENAMED_OLD}` <!-- historical-ref -->, renamed 2026-07-27.\n\n"
        f"And today the gate is `{RENAMED_OLD}`, which blocks the answer.\n",
    )
    result = _run("--docs", str(root))
    assert result.returncode == 1, result.stdout
    assert "CURRENT_ARCHITECTURE.md:3" in result.stdout
    assert _count(result.stdout, "declared historical") == 1
    assert _count(result.stdout, "live references") == 1


def test_a_document_declared_historical_keeps_the_old_name(tmp_path):
    """The dated-record mechanism: whole-document, by explicit allowlist."""
    root = _docs_tree(
        tmp_path,
        "LIVE_PROBE_FINDINGS.md",  # in _HISTORICAL_RENAME_DOCS
        "# Live probe findings\n\n"
        f"- **Where:** `{RENAMED_OLD}:52` — `compute_confidence` gave +0.5.\n",
    )
    result = _run("--docs", str(root))
    assert result.returncode == 0, result.stdout
    assert _count(result.stdout, "declared historical") == 1


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


def test_both_spellings_of_a_renamed_path_are_extracted(guard):
    """The extractor half of the fix, pinned independently of the classifier."""
    found = [m.group(0) for m in guard._RENAMED_REF_RE.finditer(
        "see `core/confidence_gate` and `core/confidence_gate.py` and "
        "`core/confidence_gate_v2.py` and `core/evidence_support.py`"
    )]
    assert found == ["core/confidence_gate", "core/confidence_gate.py"], found
